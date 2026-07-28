"""
页面扫描工具：从一段 HTML 里把表单结构和人机验证组件参数挖出来，不靠浏览器。

设计思想：
逆向和 CTF 场景经常需要"先纯 HTTP 拿页面源码，再从源码里提取表单字段、隐藏值和验证组件参数"。
当前 LazyFox 的 HTTP 工具能拿源码，但拿到之后只能靠人眼看。这个文件把"看 HTML 提取结构"
收敛成 scan_form 和 scan_challenge 两个函数，调用方一行代码拿到结构化结果。

和 tools/code.py 的关系：code.py 从文字中提取验证码和链接，scan.py 从 HTML 中提取表单
和人机验证组件参数。两者都属纯文本/解析层，不依赖浏览器网络。

里面有什么：
- scan_form(html)         从 HTML 里找到第一个表单，返回 action/method/字段列表
- scan_forms(html)        返回页面上全部表单的列表
- scan_challenge(html)    从 HTML 里提取 Cloudflare Turnstile / reCAPTCHA / hCaptcha 参数

怎么调用：
    from lazyfox import HTTP, scan_form, scan_challenge

    with HTTP(base="https://example.com") as web:
        html = web.get("/login").text
        form = scan_form(html)                    # 拿到表单结构和字段
        challenge = scan_challenge(html)          # 拿到人机验证组件参数
        fields = {f["name"]: f["value"] for f in form["fields"]}
        if challenge["type"] == "turnstile":
            fields["cf-turnstile-response"] = get_token(challenge["sitekey"])
        web.post(form["action"], data=fields)     # 纯 HTTP 提交表单
"""

import html as html_lib                                       # 还原属性值中的 & 等转义字符
import re                                                     # 从标签属性里提取 data-sitekey 等配置

from bs4 import BeautifulSoup                                # 按 HTML 结构解析标签和属性，不靠脆弱字符串切割


# --- 从 HTML 中找到第一个表单 ---
def scan_form(source):
    # source：页面 HTML 源码
    # 返回：表单字典 {action, method, fields: [{name, value, type, hidden}]}；没找到返回 None
    forms = scan_forms(source)                                # 复用批量解析，保持单表单和多表单逻辑一致
    return forms[0] if forms else None                       # 调用方常用场景只需第一个表单


# --- 从 HTML 中找到全部表单 ---
def scan_forms(source):
    # source：页面 HTML 源码
    # 返回：表单字典列表，每个表单含 action/method/fields
    if not source:                                            # 空内容直接返回空列表，避免 BeautifulSoup 处理空串
        return []

    soup = BeautifulSoup(source, "html.parser")              # 用解析器理解标签结构，按 form 标签分组
    forms = []
    for tag in soup.find_all("form"):                         # 逐个解析 form 标签
        action = html_lib.unescape(tag.get("action", ""))     # 还原 action 中的 HTML 转义，如 /login?next=%2F
        method = (tag.get("method") or "get").lower()        # 未写 method 的表单默认 GET
        fields = []
        for field in tag.find_all(["input", "textarea", "select"]):  # 涵盖文本框、文本域和下拉框
            name = field.get("name")                          # 字段名是提交时必带的键
            if not name:                                      # 没有 name 的按钮和装饰输入框不参与提交
                continue
            field_type = (field.get("type") or field.name).lower()  # input 用 type 区分，textarea/select 用标签名
            fields.append({
                "name": name,                                 # 提交时的字段键名
                "value": html_lib.unescape(field.get("value", "")),  # 预填值或隐藏字段值，还原转义
                "type": field_type,                           # text/hidden/email/password/submit 等
                "hidden": field.name == "input" and field_type == "hidden",  # 隐藏字段通常携带 CSRF/redirect
            })
        forms.append({
            "action": action,                                 # 表单提交地址，可能是相对路径
            "method": method,                                 # GET 或 POST
            "fields": fields,                                 # 按页面顺序保留所有可提交字段
        })
    return forms


# 已知人机验证组件的检测规则，按提供商从常见到冷门排列
# 每条规则：提供商名 → (HTML 特征选择器, 参数提取函数)
_challenge_rules = [
    (
        "turnstile",
        {"class": "cf-turnstile"},
        lambda tag: {
            "sitekey": tag.get("data-sitekey", ""),
            "theme": tag.get("data-theme", ""),
            "token_field": "cf-turnstile-response",          # Turnstile 回写 token 的隐藏字段名
        },
    ),
    (
        "recaptcha",
        {"class": "g-recaptcha"},
        lambda tag: {
            "sitekey": tag.get("data-sitekey", ""),
            "theme": tag.get("data-theme", ""),
            "token_field": "g-recaptcha-response",           # reCAPTCHA 回写 token 的隐藏字段名
        },
    ),
    (
        "recaptcha",
        {"class": re.compile(r"\bg-recaptcha\b")},
        lambda tag: {
            "sitekey": tag.get("data-sitekey", ""),
            "theme": tag.get("data-theme", ""),
            "token_field": "g-recaptcha-response",
        },
    ),
    (
        "hcaptcha",
        {"class": "h-captcha"},
        lambda tag: {
            "sitekey": tag.get("data-sitekey", ""),
            "theme": tag.get("data-theme", ""),
            "token_field": "h-captcha-response",             # hCaptcha 回写 token 的隐藏字段名
        },
    ),
]


# --- 从 HTML 中提取人机验证组件参数 ---
def scan_challenge(source):
    # source：页面 HTML 源码
    # 返回：{type, sitekey, theme, token_field}；没找到则 type 为空串
    if not source:
        return {"type": "", "sitekey": "", "theme": "", "token_field": ""}

    soup = BeautifulSoup(source, "html.parser")              # 用解析器理解标签结构
    for provider, attrs, extract in _challenge_rules:         # 按已知规则逐个提供商检测
        tag = soup.find(attrs=attrs)                          # 按特征选择器查找对应组件容器
        if tag:                                               # 找到第一个匹配就提取参数
            info = extract(tag)                               # 从标签属性中提取 sitekey 等配置
            info["type"] = provider                           # 统一补上提供商标识，方便调用方分支处理
            return info

    # 没找到已知组件容器，再检查 JS 内联的 turnstile.render 调用（某些页面不用 div 容器）
    if "turnstile.render" in source:
        match = re.search(r"sitekey['\"]?\s*[:=]\s*['\"]([0-9A-Za-z]+)['\"]", source)
        if match:
            return {
                "type": "turnstile",                          # JS 内联渲染的 Turnstile
                "sitekey": match.group(1),                    # 从 render 参数中提取 sitekey
                "theme": "",
                "token_field": "cf-turnstile-response",
            }

    return {"type": "", "sitekey": "", "theme": "", "token_field": ""}
