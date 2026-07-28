"""
Cloudflare Turnstile 审计工具：识别测试配置、token 字段和服务端验证边界。

设计思想：
Turnstile 的安全边界在 Cloudflare 的 siteverify，不在前端 widget。LazyFox 不应该伪造或绕过
验证码，但应该能快速判断一个目标是否误用了官方测试 key、是否接受 dummy token、以及表单应携带
哪个字段。这个文件只做配置识别和提交辅助，不做 token 生成。
"""

from dataclasses import dataclass                           # 审计结果用结构化数据返回

from tools.form import turnstile                            # 静态 widget 配置由表单解析层负责


DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"                       # Cloudflare 官方测试 sitekey 生成的 dummy token
TOKEN_FIELD = "cf-turnstile-response"                       # 官方自动渲染表单写入的字段名

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
