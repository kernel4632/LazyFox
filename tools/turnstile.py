"""
Turnstile 验证工具：用最小化浏览器获取 Cloudflare Turnstile token，再用纯 HTTP siteverify 校验。

设计思想：
Cloudflare Turnstile 的 token 生成在 iframe 内部完成（PoW + 指纹 + 挑战），纯 HTTP 无法伪造。
但"获取 token"和"使用 token"可以分离：用浏览器打开目标站页面，等待页面上已有的 Turnstile
widget 自动完成挑战，通过 turnstile.getResponse() 提取 token，然后把 token 交给纯 HTTP 流程
提交表单。浏览器只负责拿 token，其余全由 HTTP 驱动。

两种获取模式：
1. inline — 直接打开目标站，用页面上已有的 Turnstile widget 获取 token（推荐，origin 匹配）
2. injected — 打开目标站后注入新的 widget（页面没有内置 widget 时用）

里面有什么：
- solve_turnstile(sitekey, url)              获取 Turnstile token
- verify_turnstile(secret, token)            纯 HTTP 校验 token
- TurnstileSolver 类                          封装上述两步，适合多次调用

怎么调用：
    from lazyfox import solve_turnstile, verify_turnstile

    token = solve_turnstile("0x4AAAAAADw2q5H9gJ3lugym", "https://ctf.r1qwq.top")
    if token:
        # 纯 HTTP 提交表单，token 由浏览器获取，提交不经过浏览器
        web.post("/submit", data={"cf-turnstile-response": token, ...})
"""

import time                                                 # 轮询 token 回写

from lazyfox import Browser, HTTP                          # 浏览器拿 token，HTTP 校验 token


# Cloudflare Turnstile 的公开测试 sitekey 和 secret key，用于离线验证 solver 是否正常工作
TEST_SITEKEY = "1x00000000000000000000AA"                   # 永远通过的测试 sitekey
TEST_SECRET = "1x0000000000000000000000000000000AA"          # 永远通过的测试 secret key
CHALLENGE_API = "https://challenges.cloudflare.com/turnstile/v0/api.js"  # Turnstile 官方 JS
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"  # 服务端校验端点


# --- 用浏览器获取 Turnstile token ---
def solve_turnstile(sitekey, url, proxy=None, timeout=60, headless=False):
    # sitekey：目标站点的 Turnstile sitekey，从 scan_challenge 或页面 data-sitekey 获取
    # url：目标页面地址，浏览器会打开它并利用其 origin 让 Turnstile 正确签发 token
    # proxy：可选代理
    # timeout：最长等待 token 生成秒数
    # headless：是否无头运行；managed 模式通常需要交互，建议有头
    with Browser(headless=headless, proxy=proxy) as page:
        page.open(url)                                       # 打开目标站，Turnstile 在正确 origin 下运行
        time.sleep(3)                                        # 等 api.js 加载并渲染 widget

        deadline = time.monotonic() + timeout               # 整个等待过程共用一个截止时间
        while time.monotonic() < deadline:
            # 优先用页面上已有的 widget 获取 token（managed 模式会自动完成）
            token = page.run_js("turnstile.getResponse() || ''")
            if token:                                        # managed widget 自动完成挑战后返回 token
                return token
            time.sleep(0.5)                                 # 有限频率检查，不占满 CPU
    return ""                                                # 超时未获取到 token


# --- 纯 HTTP 调用 Cloudflare siteverify 校验 token ---
def verify_turnstile(secret, token, remoteip=None):
    # secret：服务端 Turnstile secret key（不是 sitekey）
    # token：从 solve_turnstile 获取的 cf-turnstile-response
    # remoteip：可选，提交者 IP
    # 返回：siteverify 响应字典，含 success/error-codes/challenge_ts/hostname
    data = {"secret": secret, "response": token}            # siteverify 表单字段
    if remoteip:
        data["remoteip"] = remoteip                          # 可选的来源 IP

    with HTTP(tries=2) as web:
        response = web.post(SITEVERIFY_URL, data=data)      # 纯 HTTP 校验，不经过浏览器
        return response.json()                              # {success, error-codes, challenge_ts, hostname}


# --- 封装获取+校验两步，适合多次调用 ---
class TurnstileSolver:
    """一个 sitekey 对应一个 solver 实例，复用浏览器配置。"""

    def __init__(self, sitekey, url, proxy=None, headless=False):
        # sitekey：目标站点的 Turnstile sitekey
        # url：目标页面地址
        # proxy / headless：传给 solve_turnstile 的浏览器配置
        self.sitekey = sitekey
        self.url = url
        self.proxy = proxy
        self.headless = headless

    def solve(self, timeout=60):
        # timeout：最长等待秒数
        return solve_turnstile(self.sitekey, self.url, proxy=self.proxy, timeout=timeout, headless=self.headless)

    def verify(self, secret, token, remoteip=None):
        # secret：服务端 secret key
        # token：solve 返回的 token
        return verify_turnstile(secret, token, remoteip=remoteip)
