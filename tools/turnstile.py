"""
Cloudflare Turnstile 审计工具：识别测试配置、token 字段和服务端验证边界。

设计思想：
Turnstile 的安全边界在 Cloudflare 的 siteverify，不在前端 widget。LazyFox 不应该伪造或绕过
验证码，但应该能快速判断一个目标是否误用了官方测试 key、是否接受 dummy token、以及表单应携带
哪个字段。这个文件只做配置识别和提交辅助，不做 token 生成。
"""

from dataclasses import dataclass, field                    # 审计和验证结果都用结构化数据返回

from tools.form import turnstile                            # 静态 widget 配置由表单解析层负责
from tools.http import HTTP                                 # siteverify 是标准 HTTP 表单接口


DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"                       # Cloudflare 官方测试 sitekey 生成的 dummy token
TOKEN_FIELD = "cf-turnstile-response"                       # 官方自动渲染表单写入的字段名
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

TEST_SITEKEYS = {
    "1x00000000000000000000AA": "always-pass-visible",
    "2x00000000000000000000AB": "always-fail-visible",
    "1x00000000000000000000BB": "always-pass-invisible",
    "2x00000000000000000000BB": "always-fail-invisible",
    "3x00000000000000000000FF": "force-interactive-visible",
}


@dataclass
class Turnstile:
    """一个页面中的 Turnstile 静态配置。"""

    sitekey: str = ""                                      # 公开 sitekey，来自 data-sitekey
    theme: str = ""                                        # widget 主题，可为空
    action: str = ""                                       # 可选 action，服务端可能校验
    cdata: str = ""                                        # 可选 cdata，服务端可能校验
    mode: str = "production"                               # production / test / missing
    behavior: str = ""                                     # 测试 key 的官方行为说明

    # --- 是否官方测试配置 ---
    def testing(self):
        return self.mode == "test"

    # --- 是否可用官方 dummy token 做合法测试提交 ---
    def dummy_allowed(self):
        return self.behavior.startswith("always-pass")      # 仍要求服务端也使用对应测试 secret


@dataclass
class Verification:
    """Cloudflare siteverify 的结构化结果。"""

    success: bool = False                                   # Cloudflare 是否接受该 token
    errors: list[str] = field(default_factory=list)          # error-codes，失败原因直接来自 Cloudflare
    hostname: str = ""                                      # token 签发所在域名
    action: str = ""                                        # widget action，服务端可按需校验
    cdata: str = ""                                         # widget cdata，服务端可按需校验
    challenge_ts: str = ""                                  # token 签发时间，ISO 字符串
    raw: dict = field(default_factory=dict)                  # 原始 JSON，保留 Cloudflare 新增字段

    # --- 一行摘要，方便日志输出 ---
    def summary(self):
        if self.success:
            return f"success host={self.hostname} action={self.action}"
        return "failed " + ",".join(self.errors or ["unknown-error"])


# --- 从 HTML 中审计 Turnstile 配置 ---
def audit(html):
    config = turnstile(html)
    sitekey = config.get("sitekey", "")
    if not sitekey:
        return Turnstile(mode="missing")                   # 页面没有 widget，调用方可走普通表单逻辑
    behavior = TEST_SITEKEYS.get(sitekey, "")
    mode = "test" if behavior else "production"
    return Turnstile(
        sitekey=sitekey,
        theme=config.get("theme", ""),
        action=config.get("action", ""),
        cdata=config.get("cdata", ""),
        mode=mode,
        behavior=behavior,
    )


# --- 把 token 写入表单数据 ---
def attach(data, token, field=TOKEN_FIELD):
    payload = dict(data or {})                              # 复制输入，避免修改调用方原始字典
    payload[field] = token                                  # 官方字段名默认使用 cf-turnstile-response
    return payload


# --- 给测试配置生成 dummy token 提交体 ---
def dummy(data, field=TOKEN_FIELD):
    return attach(data, DUMMY_TOKEN, field=field)           # 只用于官方测试 key/secret 组合


# --- 调用 Cloudflare siteverify 验证 token ---
def verify(token, secret, remoteip=None, idempotency_key=None, web=None):
    # token：客户端提交的 Turnstile token；本函数只验证，不生成 token
    # secret：Cloudflare dashboard 或官方测试 secret；不要写入日志或公开文件
    # remoteip：可选用户 IP；传入后 Cloudflare 会把它纳入验证上下文
    owned = web is None                                     # 外部没传 HTTP 会话时，本函数自己创建并关闭
    web = web or HTTP(timeout=15, tries=2)                  # siteverify 是外部网络接口，保留短重试

    data = {"secret": secret, "response": token}          # Cloudflare 规定的必填字段
    if remoteip:
        data["remoteip"] = remoteip
    if idempotency_key:
        data["idempotency_key"] = idempotency_key

    try:
        response = web.post(SITEVERIFY_URL, data=data, check=False, replay_safe=True)
        payload = response.json() if response.text else {}  # Cloudflare 正常返回 JSON，空响应按失败处理
        return _verification(payload)
    finally:
        if owned:
            web.close()                                     # 只关闭自己创建的会话，不影响调用方复用连接


# --- 内部：Cloudflare JSON 转结构化结果 ---
def _verification(payload):
    return Verification(
        success=bool(payload.get("success")),
        errors=list(payload.get("error-codes") or []),
        hostname=payload.get("hostname") or "",
        action=payload.get("action") or "",
        cdata=payload.get("cdata") or "",
        challenge_ts=payload.get("challenge_ts") or "",
        raw=dict(payload),
    )
