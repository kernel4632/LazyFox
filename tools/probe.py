"""
协议探针工具：批量发送小规模请求变体，并把响应压成可比较的签名。

设计思想：
CTF、逆向注册和接口复现都需要回答同一个问题：服务端到底信任哪个字段、哪种编码、
哪条状态路径。手写 for 循环很快会变成不可复现的临时代码。本文件把“构造实验 → 发送请求
→ 提取反馈 → 按响应签名聚类”收成通用工具，只做低频、低影响的协议边界确认。
"""

from dataclasses import dataclass                           # 探针输入和输出都用结构化对象表达
from hashlib import sha256                                  # 响应体指纹用于区分模板相同但文案不同的页面

from tools.form import result                               # HTML 结果页摘要复用表单解析层，不重复写解析逻辑


@dataclass
class Case:
    """一个请求实验。"""

    name: str                                               # 实验名，日志和报告里用它定位变体
    method: str = "POST"                                   # 请求方法，默认覆盖表单提交场景
    path: str = ""                                         # 请求路径或完整 URL
    kwargs: dict | None = None                              # 交给 HTTP.request 的参数，如 data/json/headers


@dataclass
class Finding:
    """一次请求实验的响应摘要。"""

    case: str                                               # 来源实验名
    status: int                                             # HTTP 状态码
    url: str                                                # 最终响应 URL，包含重定向后的地址
    title: str                                              # HTML title
    label: str                                              # 页面状态短语
    heading: str                                            # 页面主标题
    lead: str                                               # 页面说明文字
    body_hash: str                                          # 响应正文短指纹，用于聚类

    # --- 响应签名：用来判断两个实验是否进入同一服务端分支 ---
    def signature(self):
        # body_hash 常包含 cf-ray、时间戳等边缘注入噪声；默认按可读语义聚类。
        return (self.status, self.title, self.label, self.heading, self.lead)

    # --- 一行可读摘要，方便终端输出 ---
    def summary(self):
        parts = [str(self.status), self.title, self.label, self.heading, self.lead]
        return " | ".join(part for part in parts if part)  # 空字段不显示，保持输出紧凑


# --- 运行一组请求实验 ---
def run(web, cases, check=False, replay_safe=True):
    findings = []                                           # 保持输入顺序，便于和人工计划对照
    for case in cases:
        kwargs = dict(case.kwargs or {})                    # 复制一份，避免调用方传入的 dict 被 httpx 修改
        response = web.request(
            case.method,
            case.path,
            check=check,
            replay_safe=replay_safe,
            **kwargs,
        )
        findings.append(_finding(case.name, response))      # 每个响应立即压缩成结构化结果
    return findings


# --- 按响应签名聚类 ---
def groups(findings):
    buckets = {}                                            # signature -> list[Finding]
    for item in findings:
        buckets.setdefault(item.signature(), []).append(item)
    return list(buckets.values())                           # 返回分组列表，调用方自行排序或打印


# --- 生成常见表单字段变体 ---
def form_cases(path, base, token_field="cf-turnstile-response", token="fake"):
    # path：目标提交路径
    # base：基础字段字典，如 team/message
    # token_field：目标服务端预期的 token 字段名
    # token：用于探测的占位 token；真实验证仍由服务端决定
    normal = dict(base, **{token_field: token})             # 标准表单体，进入正常校验分支
    return [
        Case("missing-token", path=path, kwargs={"data": dict(base)}),
        Case("blank-token", path=path, kwargs={"data": dict(base, **{token_field: ""})}),
        Case("fake-token", path=path, kwargs={"data": normal}),
        Case("json-fake", path=path, kwargs={"json": normal}),
        Case("query-token", path=path, kwargs={"params": {token_field: token}, "data": dict(base)}),
        Case("underscore-token", path=path, kwargs={"data": dict(base, cf_turnstile_response=token)}),
        Case("upper-token", path=path, kwargs={"data": dict(base, **{token_field.upper(): token})}),
        Case(
            "duplicate-token",
            path=path,
            kwargs={
                "content": _urlencode_pairs([*base.items(), (token_field, ""), (token_field, token)]),
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            },
        ),
    ]


# --- 内部：把响应转成 Finding ---
def _finding(name, response):
    feedback = result(response.text)                        # HTML 结果页没有这些元素时会返回空字段
    return Finding(
        case=name,
        status=response.status_code,
        url=str(response.url),
        title=feedback.title,
        label=feedback.label,
        heading=feedback.heading,
        lead=feedback.lead,
        body_hash=sha256(response.text.encode("utf-8", errors="ignore")).hexdigest()[:12],
    )


# --- 内部：按 x-www-form-urlencoded 编码键值对，保留重复字段顺序 ---
def _urlencode_pairs(pairs):
    from urllib.parse import urlencode                      # 标准库编码，doseq 支持 list 值

    return urlencode(pairs, doseq=True)
