"""
HTML 表单解析工具：把页面里的 form、字段、Turnstile 配置和结果页文案读成结构化数据。

设计思想：
逆向注册和 CTF 分析经常从一个 HTML 页面开始。浏览器能直接点表单，但纯 HTTP 复现时，
调用方需要知道 action、method、字段名、默认值、maxlength、required 和站点校验组件配置。
这些信息不应该散落在每个脚本的 BeautifulSoup 片段里，本文件把它们收成稳定的小工具。
"""

from dataclasses import dataclass                           # 用不可变语义表达页面协议结构

from bs4 import BeautifulSoup                               # 成熟 HTML 解析器，容错处理真实网页片段


@dataclass
class Field:
    """一个表单字段的静态声明。"""

    tag: str                                                # input / textarea / select 等标签名
    name: str                                               # 提交到服务端的字段名
    value: str = ""                                        # HTML 中给出的默认值
    type: str = ""                                         # input type，textarea/select 通常为空
    required: bool = False                                  # 是否有 required 属性
    maxlength: int | None = None                            # maxlength 存在时转成整数，非法值保留为空


@dataclass
class Form:
    """一个 HTML form 的提交契约。"""

    method: str                                             # GET / POST，默认按 HTML 规范视为 GET
    action: str                                             # 表单提交地址，可能是相对路径
    fields: list[Field]                                     # 表单内可命名字段列表

    # --- 生成提交数据：默认值 + 调用方覆盖值 ---
    def data(self, **values):
        data = {field.name: field.value for field in self.fields if field.name}  # 先保留 HTML 默认值
        data.update({key: value for key, value in values.items() if value is not None})  # 调用方覆盖真实提交值
        return data                                          # 返回普通 dict，可直接交给 HTTP.post(data=...)

    # --- 按字段名取字段声明 ---
    def field(self, name):
        for field in self.fields:
            if field.name == name:
                return field                                # 找到后立即返回，调用方可读 required/maxlength
        return None                                         # 未声明字段返回 None，便于 if 判断


@dataclass
class Result:
    """结果页的核心可读反馈。"""

    title: str = ""                                        # <title>，通常是页面级状态
    label: str = ""                                        # .eyebrow，很多靶场用它放状态短语
    heading: str = ""                                      # h1 主标题
    lead: str = ""                                         # .lead 详细说明

    # --- 拼成一行摘要，方便日志和响应聚类 ---
    def summary(self):
        parts = [self.title, self.label, self.heading, self.lead]
        return " | ".join(part for part in parts if part)  # 空字段自动跳过，避免多余分隔符


# --- 解析页面里的所有表单 ---
def forms(html):
    soup = BeautifulSoup(html or "", "html.parser")        # 空字符串也返回空列表，不让调用方额外判空
    return [_form(node) for node in soup.find_all("form")]  # 保持页面顺序，适合按第一个主表单使用


# --- 解析页面里的第一个表单 ---
def first_form(html):
    items = forms(html)                                     # 复用 forms，避免两套解析规则分叉
    return items[0] if items else None                      # 没有表单时返回 None


# --- 读取 Cloudflare Turnstile widget 的静态配置 ---
def turnstile(html):
    soup = BeautifulSoup(html or "", "html.parser")
    node = soup.select_one(".cf-turnstile")                 # 官方自动渲染模式使用 cf-turnstile 类名
    if not node:
        return {}                                           # 页面没有 Turnstile，返回空 dict 方便 truthy 判断
    return {
        key[5:]: value                                      # data-sitekey -> sitekey，调用方不用处理 data- 前缀
        for key, value in node.attrs.items()
        if key.startswith("data-")
    }


# --- 提取结果页的核心文本 ---
def result(html):
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    label = _text(soup.select_one(".eyebrow"))
    heading = _text(soup.find("h1"))
    lead = _text(soup.select_one(".lead"))
    return Result(title=title, label=label, heading=heading, lead=lead)


# --- 内部：把一个 form 标签转成 Form 对象 ---
def _form(node):
    method = (node.get("method") or "get").upper()         # HTML 默认 GET，统一大写便于请求分派
    action = node.get("action") or ""                      # 无 action 时提交当前页，调用方可自行补 URL
    fields = [_field(item) for item in node.find_all(["input", "textarea", "select"])]
    return Form(method=method, action=action, fields=[field for field in fields if field.name])


# --- 内部：把一个字段标签转成 Field 对象 ---
def _field(node):
    maxlength = _int(node.get("maxlength"))                 # 非法 maxlength 不让解析失败
    return Field(
        tag=node.name or "",
        name=node.get("name") or "",
        value=node.get("value") or node.get_text(strip=True),
        type=node.get("type") or "",
        required=node.has_attr("required"),
        maxlength=maxlength,
    )


# --- 内部：安全读取元素文字 ---
def _text(node):
    return node.get_text(strip=True) if node else ""        # 找不到元素时返回空串


# --- 内部：字符串转整数，失败时返回 None ---
def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
