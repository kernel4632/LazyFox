"""
HTTP 请求工具：统一处理会话复用、代理、超时、失败重试和登录失效后的自动刷新。

设计思想：
逆向接口通常不是发一次请求就结束。Cookie 要复用，401/403 后要重新登录，网络抖动时
还要重试。这个文件把这些固定动作收进 HTTP 和 AsyncHTTP，调用方只写网址和请求数据。

同步调用：
    from tools.http import HTTP

    web = HTTP(base="https://example.com", tries=3)
    data = web.get("/api/user").json()

自动重新登录：
    def login(web):
        token = web.post("/login", json={"user": "demo"}, auth=False).json()["token"]
        web.headers["Authorization"] = f"Bearer {token}"

    web = HTTP(base="https://example.com", login=login)
"""

import time                                                 # 请求失败后按间隔等待，避免连续冲击上游
from contextlib import asynccontextmanager, contextmanager  # 保证同步/异步流式响应离开作用域时关闭

import httpx                                                # 成熟 HTTP 客户端，支持同步、异步、代理和流式响应


class HTTP:
    """同步 HTTP 会话，适合 requests 风格的逆向脚本。"""

    # --- 准备可复用的同步会话 ---
    def __init__(self, base="", headers=None, proxy=None, timeout=30, tries=3, gap=1.0, login=None):
        # base：上游根地址，之后可以只传 /api/user 这类相对路径
        # headers：每次请求都带上的公共请求头
        # proxy：HTTP 或 SOCKS 代理地址
        # timeout：单次请求最多等待秒数
        # tries：失败后最多尝试次数
        # gap：两次尝试之间等待秒数
        # login：重新登录函数，接收当前 HTTP 对象；401/403 时自动调用一次
        self.base = base.rstrip("/")                       # 去掉末尾斜杠，和相对路径拼接时不会出现双斜杠
        self.tries = max(1, int(tries))                    # 至少尝试一次，防止 tries=0 导致请求完全不发
        self.gap = max(0, float(gap))                      # 负间隔没有意义，统一收敛为 0
        self.login = login                                 # 保存重新登录动作，鉴权失败时使用
        self.client = httpx.Client(                        # 创建长连接会话，复用 Cookie 和 TCP 连接
            headers=dict(headers or {}),
            proxy=proxy,
            timeout=timeout,
            follow_redirects=True,
        )
        self.headers = self.client.headers                 # 公开真实会话请求头，登录函数修改后下一次请求立即生效

    # --- 拼出完整请求地址 ---
    def url(self, path):
        if str(path).startswith(("http://", "https://")):  # 已经是完整地址就直接使用
            return str(path)
        return f"{self.base}/{str(path).lstrip('/')}"       # 相对路径和根地址各保留一个斜杠

    # --- 发请求，失败重试，鉴权失效自动登录 ---
    def request(self, method, path, auth=True, replay_safe=None, **kwargs):
        # method / path：请求方法和地址
        # auth：是否允许在 401/403 时自动执行 login；登录请求自身应传 False 防止递归
        # replay_safe：是否允许失败后重发；默认只重试 GET/HEAD/OPTIONS，POST 必须明确传 True
        method = method.upper()                             # 统一方法名，便于判断是否天然幂等
        if replay_safe is None:
            replay_safe = method in ("GET", "HEAD", "OPTIONS")  # 读取请求默认可安全重放
        last_error = None                                  # 保存最后一次异常，全部失败后原样抛出
        did_login = False                                  # 一次 request 最多自动登录一次，避免账号失效后死循环

        attempt = 0                                        # 只统计真正失败的请求，自动登录本身不消耗重试次数
        while attempt < self.tries:                        # 按配置次数尝试完整请求
            try:
                response = self.client.request(method, self.url(path), **kwargs)  # 复用会话发出请求
                if auth and self.login and response.status_code in (401, 403) and not did_login:
                    did_login = True                       # 先标记，login 内部若请求失败也不会递归刷新
                    self.login(self)                       # 让业务提供的登录动作刷新 Cookie 或请求头
                    continue                              # 登录成功后重新发送原请求
                response.raise_for_status()                # 4xx/5xx 转成明确异常，进入重试或最终抛出
                return response                            # 请求成功，立即返回 httpx.Response
            except (httpx.HTTPError, OSError) as error:
                last_error = error                         # 记住失败原因，供最后一次尝试后抛出
                attempt += 1                               # 只有网络/状态失败才占用一次普通重试
                if not replay_safe:
                    raise                                  # POST 等副作用请求失败后状态未知，默认禁止盲目重发
                if attempt < self.tries:                   # 后面还有机会才等待，最后一次无需多睡
                    time.sleep(self.gap)

        raise last_error or RuntimeError("HTTP 请求失败")   # 理论兜底：始终把失败明确反馈给调用方

    # --- 常用 GET 快捷入口 ---
    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)         # 转交统一 request，保留重试和登录能力

    # --- 常用 POST 快捷入口 ---
    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)        # 转交统一 request

    # --- 构造同步流式请求上下文 ---
    @contextmanager
    def stream(self, method, path, auth=True, replay_safe=None, **kwargs):
        # 用法：with web.stream("POST", "/chat", json=data) as response: parse(response.iter_lines())
        method = method.upper()                             # 统一方法名
        if replay_safe is None:
            replay_safe = method in ("GET", "HEAD", "OPTIONS")  # 副作用流默认不盲目重发
        response = None                                    # 成功响应交给调用方读取，离开 with 后关闭
        did_login = False                                  # 单次流最多自动刷新一次登录
        attempt = 0                                        # 只统计普通失败

        while attempt < self.tries:
            try:
                request = self.client.build_request(method, self.url(path), **kwargs)  # 构造但暂不读取响应体
                response = self.client.send(request, stream=True)  # 只读取响应头，正文留给 parse
                if auth and self.login and response.status_code in (401, 403) and not did_login:
                    response.close()                       # 先释放失败响应连接
                    response = None
                    did_login = True
                    self.login(self)                       # 刷新登录态
                    continue                              # 使用新登录态重连流
                response.raise_for_status()                # 建流前先验证状态码
                break
            except (httpx.HTTPError, OSError):
                if response:
                    response.close()                       # 失败响应必须释放连接
                    response = None
                attempt += 1
                if not replay_safe:
                    raise                                  # POST 流状态未知，默认禁止重新发送
                if attempt < self.tries:
                    time.sleep(self.gap)                   # 安全读取流按间隔重试

        if response is None:
            raise RuntimeError("HTTP 流式请求失败")          # 防止把空响应交给调用方
        try:
            yield response                                 # 调用方在 with 内逐行消费正文
        finally:
            response.close()                               # 无论读完、断流还是调用方异常都释放连接

    # --- 关闭连接池 ---
    def close(self):
        self.client.close()                                # 释放连接和文件句柄

    def __enter__(self):
        return self                                        # 支持 with HTTP() as web

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()                                       # 离开 with 时确保连接池关闭
        return False                                       # 不吞掉业务异常


class AsyncHTTP:
    """异步 HTTP 会话，适合 FastAPI 和流式代理。"""

    # --- 准备可复用的异步会话 ---
    def __init__(self, base="", headers=None, proxy=None, timeout=30, tries=3, gap=1.0, login=None):
        self.base = base.rstrip("/")                       # 上游根地址
        self.tries = max(1, int(tries))                    # 至少请求一次
        self.gap = max(0, float(gap))                      # 重试间隔不能为负
        self.login = login                                 # 异步重新登录函数，接收当前 AsyncHTTP
        self.client = httpx.AsyncClient(                   # 异步长连接会话，适合并发和流式读取
            headers=dict(headers or {}),
            proxy=proxy,
            timeout=timeout,
            follow_redirects=True,
        )
        self.headers = self.client.headers                 # 暴露真实异步会话请求头，登录刷新可直接修改

    # --- 拼出完整请求地址 ---
    def url(self, path):
        if str(path).startswith(("http://", "https://")):  # 完整网址无需拼接
            return str(path)
        return f"{self.base}/{str(path).lstrip('/')}"       # 相对路径拼到根地址后

    # --- 异步发请求，失败重试，鉴权失效自动登录 ---
    async def request(self, method, path, auth=True, replay_safe=None, **kwargs):
        method = method.upper()                             # 统一请求方法
        if replay_safe is None:
            replay_safe = method in ("GET", "HEAD", "OPTIONS")  # 异步版遵循同一重放规则
        last_error = None                                  # 保存最终应抛出的失败原因
        did_login = False                                  # 单次请求最多刷新一次登录

        attempt = 0                                        # 自动登录不计入普通失败次数
        while attempt < self.tries:                        # 按配置次数尝试
            try:
                response = await self.client.request(method, self.url(path), **kwargs)  # 异步发送请求
                if auth and self.login and response.status_code in (401, 403) and not did_login:
                    did_login = True                       # 防止登录函数自身触发无限刷新
                    await self.login(self)                 # 调用业务提供的异步登录动作
                    continue                              # 用新登录态重发原请求
                response.raise_for_status()                # 把错误状态转成异常
                return response                            # 成功返回响应
            except (httpx.HTTPError, OSError) as error:
                last_error = error                         # 保存本次失败
                attempt += 1                               # 网络/状态失败消耗一次重试
                if not replay_safe:
                    raise                                  # 副作用请求默认只发一次
                if attempt < self.tries:                   # 还有尝试机会才等待
                    import asyncio                         # 只在重试时引入异步等待能力

                    await asyncio.sleep(self.gap)          # 不阻塞事件循环地等待

        raise last_error or RuntimeError("HTTP 请求失败")   # 全部失败后明确抛出

    # --- 异步 GET 快捷入口 ---
    async def get(self, path, **kwargs):
        return await self.request("GET", path, **kwargs)   # 转交统一 request

    # --- 异步 POST 快捷入口 ---
    async def post(self, path, **kwargs):
        return await self.request("POST", path, **kwargs)  # 转交统一 request

    # --- 构造流式请求上下文 ---
    @asynccontextmanager
    async def stream(self, method, path, auth=True, replay_safe=None, **kwargs):
        # 流式响应必须在 async with 内读取，连接才能在读完后正确归还连接池
        import asyncio                                     # 异步重试等待不阻塞其他请求

        method = method.upper()                             # 统一方法名
        if replay_safe is None:
            replay_safe = method in ("GET", "HEAD", "OPTIONS")  # 副作用流默认只发一次
        response = None                                    # 成功响应离开 async with 后关闭
        did_login = False                                  # 最多自动登录一次
        attempt = 0                                        # 普通失败次数

        while attempt < self.tries:
            try:
                request = self.client.build_request(method, self.url(path), **kwargs)  # 构造流式请求
                response = await self.client.send(request, stream=True)  # 只读取响应头
                if auth and self.login and response.status_code in (401, 403) and not did_login:
                    await response.aclose()                # 先释放鉴权失败连接
                    response = None
                    did_login = True
                    await self.login(self)                 # 异步刷新登录态
                    continue                              # 使用新登录态重连
                response.raise_for_status()                # 状态成功后才交给调用方
                break
            except (httpx.HTTPError, OSError):
                if response:
                    await response.aclose()                # 失败响应归还连接池
                    response = None
                attempt += 1
                if not replay_safe:
                    raise                                  # POST 等流默认不盲目重发
                if attempt < self.tries:
                    await asyncio.sleep(self.gap)          # 安全流按间隔重试

        if response is None:
            raise RuntimeError("HTTP 流式请求失败")          # 不返回不可用上下文
        try:
            yield response                                 # 调用方异步消费 aiter_lines
        finally:
            await response.aclose()                        # 客户端断连或异常时也释放上游流

    # --- 异步关闭连接池 ---
    async def close(self):
        await self.client.aclose()                         # 释放所有异步连接

    async def __aenter__(self):
        return self                                        # 支持 async with AsyncHTTP() as web

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()                                 # 离开 async with 时关闭连接池
        return False                                       # 不吞业务异常
