"""HTTP 会话测试：用 httpx MockTransport 验证相对网址、重试和 401 自动登录。"""

import asyncio                                              # 驱动异步 HTTP 会话测试

import httpx                                                # 提供内存 MockTransport，不发真实网络请求
import pytest                                               # 验证副作用 POST 失败后不会自动重发

from tools.http import AsyncHTTP, HTTP                      # 被测同步/异步 HTTP 会话


def test_http_builds_url_and_retries():
    calls = 0                                               # 记录上游被请求次数

    def send(request):
        nonlocal calls
        calls += 1                                         # 每次请求递增
        if calls == 1:
            return httpx.Response(500, request=request)     # 第一次故意失败，触发重试
        return httpx.Response(200, json={"ok": True}, request=request)  # 第二次成功

    web = HTTP(base="https://demo.test", tries=2, gap=0)   # 不等待的两次尝试
    web.client.close()                                      # 关闭构造时的真实连接池
    web.client = httpx.Client(transport=httpx.MockTransport(send))  # 换成内存上游
    response = web.get("/user")                            # 使用相对路径发请求
    assert response.json() == {"ok": True}                 # 最终拿到成功响应
    assert str(response.request.url) == "https://demo.test/user"  # 地址拼接正确
    assert calls == 2                                       # 确实重试了一次
    web.close()


def test_http_relogs_after_401():
    logged = False                                          # 模拟当前登录状态

    def login(_web):
        nonlocal logged
        logged = True                                       # 登录动作刷新状态

    def send(request):
        status = 200 if logged else 401                     # 登录前 401，登录后 200
        return httpx.Response(status, json={"logged": logged}, request=request)

    web = HTTP(base="https://demo.test", tries=3, gap=0, login=login)
    web.client.close()
    web.client = httpx.Client(transport=httpx.MockTransport(send))
    assert web.get("/private").json()["logged"] is True    # 应自动登录并重发成功
    web.close()


def test_post_does_not_retry_by_default():
    calls = 0                                               # 记录副作用请求发送次数

    def send(request):
        nonlocal calls
        calls += 1                                         # 每发送一次就计数
        return httpx.Response(500, request=request)         # 上游失败，业务状态可能未知

    web = HTTP(base="https://demo.test", tries=3, gap=0)
    web.client.close()
    web.client = httpx.Client(transport=httpx.MockTransport(send))
    with pytest.raises(httpx.HTTPStatusError):
        web.post("/register")                               # 未声明 replay_safe，POST 只能发送一次
    assert calls == 1                                      # 防止重复注册、重复发验证码
    web.close()


def test_async_http_relogs_after_401():
    async def run():
        logged = False                                      # 模拟异步登录状态

        async def login(_web):
            nonlocal logged
            logged = True                                   # 异步登录动作刷新状态

        def send(request):
            status = 200 if logged else 401                 # 登录前后返回不同状态
            return httpx.Response(status, json={"logged": logged}, request=request)

        web = AsyncHTTP(base="https://demo.test", tries=1, gap=0, login=login)
        await web.client.aclose()                           # 关闭真实连接池
        web.client = httpx.AsyncClient(transport=httpx.MockTransport(send))  # 换成内存上游
        web.headers = web.client.headers                    # 保持公开 headers 指向当前测试会话
        result = await web.get("/private")                  # tries=1 时自动登录也不应消耗重试
        assert result.json()["logged"] is True              # 应重登并重发成功
        await web.close()

    asyncio.run(run())                                      # 执行完整异步场景


def test_async_stream_relogs_after_401():
    async def run():
        logged = False                                      # 模拟流式接口登录态

        async def login(_web):
            nonlocal logged
            logged = True                                   # 刷新登录态

        def send(request):
            status = 200 if logged else 401                 # 第一次拒绝，刷新后成功
            return httpx.Response(status, text="data: ok\n\n", request=request)

        web = AsyncHTTP(base="https://demo.test", tries=1, gap=0, login=login)
        await web.client.aclose()
        web.client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        web.headers = web.client.headers
        async with web.stream("POST", "/chat") as response:  # 流式 POST 也应自动刷新鉴权
            text = await response.aread()
        assert text == b"data: ok\n\n"                      # 成功拿到流正文
        await web.close()

    asyncio.run(run())                                      # 执行流式鉴权场景
