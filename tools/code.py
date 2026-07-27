"""
验证码与链接提取工具：从一封邮件的文本里，把注册验证码、验证链接精准挖出来。

设计思想：
逆向注册流程里，最烦的一步就是"收到邮件后从一大坨文字里找出那 6 位验证码"。
不同网站的验证码格式五花八门：纯数字、字母数字、带横杠的分段码。链接也一样，
有的藏在 HTML 标签里，有的直接是明文。这个文件把这些提取规则集中收好，
让调用方一行代码拿到结果，不用每次都自己写正则。

里面有什么：
- find_code(text)      从文本里找验证码，按常见格式依次尝试
- find_link(text)      从文本里找链接，可指定关键词只要包含该词的链接
- strip_tags(html)     去掉 HTML 标签，把网页正文还原成纯文字

怎么调用：
    from tools.code import find_code, find_link
    code = find_code("您的验证码是 483920，5分钟内有效")      # 得到 "483920"
    link = find_link(html, keyword="verify")                  # 得到含 verify 的那条链接
"""

import html                                                  # 还原 HTML 链接里的 &amp; 等转义字符
import re                                                    # 正则库，用来按格式匹配验证码和链接

from bs4 import BeautifulSoup                               # 按 HTML 结构查找突出显示的验证码节点


# 验证码的常见格式，按"越具体越靠前"排列，先试严格格式再退回宽松格式
# 放在模块级是因为这些规则固定不变，每次调用复用同一份，不必反复构建
# 说明：字母数字型的码统一要求"至少含一个数字"（前瞻 (?=[a-zA-Z0-9]*\d)），
# 因为真实验证码几乎必带数字，这样能避免把 "code here" 里的 "here" 这类纯单词误当成码
code_patterns = [
    r"验证码[^\d]{0,20}(\d{4,8})",                                          # 中文场景："验证码是 483920"，紧跟验证码字样的数字最可信
    r"verification code[^\da-zA-Z]{0,20}((?=[a-zA-Z0-9]*\d)[a-zA-Z0-9]{4,8})",  # 英文场景："verification code: 4A8B9C"，要求含数字
    r"code[^\da-zA-Z]{0,20}((?=[a-zA-Z0-9]*\d)[a-zA-Z0-9]{4,8})",           # 泛化英文场景：code 字样后面且含数字的短码，避开纯单词
    r"\b([A-Z0-9]{2,}-[A-Z0-9]{2,})\b",                                    # 分段码格式："A9F-3KD"，x.ai 等站点用这种
    r">\s*(\d{4,8})\s*<",                                                   # HTML 场景：验证码被单独包在标签里 "<span>483920</span>"
    r"\b(\d{6})\b",                                                         # 最常见的裸 6 位数字码，作为兜底
    r"\b(\d{4,8})\b",                                                       # 最宽松的 4~8 位数字，最后才用，避免误抓其他数字
]


# --- 去掉 HTML 标签，还原纯文字 ---
def strip_tags(html):
    # html：可能是网页源码，也可能本身就是纯文字
    if not html:                                             # 空内容直接返回空串，省去后续正则开销
        return ""

    text = re.sub(r"<[^>]+>", " ", html)                    # 把每个 <...> 标签替换成空格，避免单词粘连
    text = re.sub(r"\s+", " ", text)                        # 连续空白压成一个空格，让文本整洁便于匹配
    return text.strip()                                      # 去掉首尾空白，返回可读的纯文字


# --- 从文本里找验证码 ---
def find_code(text):
    # text：邮件的正文或标题，允许直接传 HTML，函数内部会自己清洗
    if not text:                                             # 没有内容就没有验证码，直接返回空串
        return ""

    if "<" in text:                                         # HTML 邮件先检查视觉上独立突出的验证码节点
        code = find_html_code(text)
        if code:
            return code                                     # 结构化结果更可信，优先于正文里的订单号/年份

    clean = strip_tags(text) if "<" in text else text       # 再还原纯文本，继续走通用正则规则

    for pattern in code_patterns:                           # 按可信度从高到低逐个格式尝试
        match = re.search(pattern, clean, re.IGNORECASE)    # 忽略大小写，兼容 Code / CODE 等写法
        if match:                                           # 命中第一个格式就采用，不再往下试宽松规则
            return match.group(1)                           # group(1) 是括号里捕获的那段纯验证码

    return ""                                               # 所有格式都没命中，返回空串表示这封邮件没有码


# --- 从 HTML 结构里找被突出显示的验证码 ---
def find_html_code(source):
    # 真实验证码通常独占一个标签，并带 code/otp 类名或大号/粗体样式；普通年份和订单号没有这些特征
    soup = BeautifulSoup(source, "html.parser")             # 用解析器理解标签结构，不靠脆弱字符串切割
    best = ""                                               # 当前最可信的候选码
    best_score = 0                                          # 分数越高越像真正验证码

    for node in soup.find_all(True):                        # 检查所有元素节点
        value = node.get_text(" ", strip=True)              # 取该节点可见文字并清理空白
        match = re.fullmatch(r"(?:\d{4,8}|[A-Z0-9]{2,}-[A-Z0-9]{2,})", value, re.IGNORECASE)
        if not match:                                       # 不是完整短码就跳过，避免从长段正文截数字
            continue

        hint = " ".join([str(node.get("id", "")), *node.get("class", [])]).lower()  # 合并 id/class 语义提示
        style = str(node.get("style", "")).lower()         # 读取内联视觉样式
        score = 2 if len(value.replace("-", "")) == 6 else 0  # 6 位码最常见，先给基础分
        if any(word in hint for word in ("code", "otp", "verify", "pin")):
            score += 5                                      # 类名明确说是验证码，可信度最高
        if "font-size" in style or "font-weight" in style:
            score += 3                                      # 大号或粗体通常是邮件主验证码
        if node.name in ("b", "strong", "code"):
            score += 2                                      # 语义标签本身代表突出内容

        if score > best_score:                              # 只保留目前分数最高的候选
            best = value
            best_score = score

    return best if best_score >= 2 else ""                 # 至少具备一个可信特征才返回，避免误抓年份


# --- 从文本里找链接 ---
def find_link(text, keyword=""):
    # text：邮件正文或 HTML 源码
    # keyword：只要包含这个词的链接，比如 "verify" 只挑验证链接，留空则返回第一条链接
    if not text:                                            # 没有内容自然没有链接
        return ""

    links = re.findall(r"https?://[^\s\"'<>）】]+", text)   # 抓出所有 http/https 链接，遇到空白或引号括号即结束
    links = [html.unescape(link) for link in links]         # HTML 属性中的 &amp; 还原成真正查询参数连接符
    if not links:                                           # 一条链接都没有，返回空串
        return ""

    if not keyword:                                         # 没指定关键词就返回第一条，通常就是主操作链接
        return links[0]

    for link in links:                                      # 指定了关键词，逐条检查
        if keyword.lower() in link.lower():                 # 链接里包含关键词（忽略大小写）就是目标
            return link

    return ""                                               # 有链接但没有一条含关键词，返回空串
