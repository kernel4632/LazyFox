<div align="center">

# 🦊 LazyFox

### 懒惰不是罪，自动化才是美。

**为自动注册与接口逆向 MVP 打造的 Python 脚手架**

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](LICENSE)

</div>

---

## 目标

写 `register/` 时，只关心目标网站有哪几个表单和按钮。

写 `reverse/` 时，只关心怎样请求上游、怎样从上游响应里拿到文字。

浏览器重试、点击结果判断、临时邮箱、验证码提取、HTTP 重试、SSE 解析、OpenAI
协议转换、日志和结果保存都由 LazyFox 处理。常用工具只需一条导入：

```python
from lazyfox import Browser, Mail, Person, Proxy, HTTP, AsyncHTTP, Log, Lines, Table
```

## 能力

| 工具 | 用途 | 重点 |
|------|------|------|
| `Browser` | nodriver 浏览器自动化 | 点击/填写后自动判断结果并重试 |
| `Mail` | 270+ 渠道临时邮箱 | 申请邮箱、筛选邮件、等待验证码/链接 |
| `Person` | Faker 假身份 | 中英文姓名、密码、电话、地址，支持种子复现 |
| `HTTP` / `AsyncHTTP` | 上游请求 | 会话复用、代理、重试、401/403 自动重新登录 |
| `parse` / `parse_async` | SSE 解析 | 同步和异步事件流统一解析 |
| `Proxy` | OpenAI 代理骨架 | 自动提供 Chat Completions 和 Responses API |
| `find_code` / `find_link` | 邮件提取 | 纯文本、HTML、分段码和链接转义 |
| `Lines` / `Table` | 结果保存 | token 去重、账号 JSON 保存 |
| `Log` | 日志 | Rich 彩色终端、异常堆栈、文件输出 |

## 安装

要求 Python 3.12+。使用浏览器功能时，本机需安装 Chrome / Chromium。

```bash
uv sync
```

也可以使用锁定依赖：

```bash
pip install -r requirements.txt
pip install -e .
```

## 写注册机

```python
from lazyfox import Browser, Lines, Mail, Person

who = Person(lang="en").all()                         # 一次生成注册资料

with Mail(channel="mailinator") as mail:
    address = mail.create()                            # 申请真实可收信的临时邮箱

    with Browser(headless=False) as page:
        page.open("https://example.com/sign-up", appear="#email")
        page.fill_form({                               # 一次填写多个字段
            "#email": address,
            "#name": who["name"],
            "#password": who["password"],
        }, verify=True)

        page.click("#send-code", appear="#code")      # 验证码框出现才算点击成功
        code = mail.wait_code(timeout=120, sender="example.com")
        page.type("#code", code, delay=0.08, verify=True)

        if page.click("#submit", url_has="/welcome"):
            Lines("tokens.txt").add(page.cookie("token"))
```

### 浏览器智能判断

```python
page.click("#next", appear="#step-two")               # 元素出现才成功
page.click("#close", vanish="#dialog")                # 弹窗消失才成功
page.click("#login", url_has="/home")                 # 地址变化才成功
page.click("#send", text_has="Sent")                  # 页面文字出现才成功
page.fill("#email", address, value_is=address)         # 回读值完全一致才成功
page.wait(appear="#result", timeout=30)                # 只等待，不重复动作
page.click("#submit", appear="#done", skip_if_done=True)  # 恢复中断流程：已完成则不再提交
```

`click` 默认只真正点击一次，之后只轮询成功条件，避免重复注册、重复发码和重复扣费。
只有明确需要连点时才传 `repeat=True`。

选择器支持显式前缀，也支持自动识别：

```python
page.click("css=button.submit")
page.click("xpath=//button[@type='submit']")
page.click("text=Create account")
```

其他高频动作包括 `type`、`press`、`hover`、`check_box`、`select`、`upload`、`remove`、
`switch`、`reload`、`back`、`forward`、`value`、`html`、`cookies` 和 `shot`。

## 临时邮箱高级控制

```python
from lazyfox import Mail

channels = Mail.channels("mailinator")                 # 搜索 SDK 支持渠道

with Mail(channel="mailinator", proxy="http://127.0.0.1:7890", timeout=20) as mail:
    address = mail.create(
        domain=None,                                    # 可指定渠道支持的域名
        duration=30,                                    # 有效分钟数
        max_channels=20,                                # 随机模式最多尝试渠道数
        total_timeout=60,
        tries=3,
    )
    code = mail.wait_code(subject="verify", sender="no-reply", timeout=120)
    link = mail.wait_link(keyword="confirm", sender="no-reply")
```

SDK 默认匿名遥测已关闭。不同 `Mail` 实例使用不同代理时，配置和请求会加锁隔离。

## 写 OpenAI 代理

只实现上游逻辑。`reply` 可以返回字符串，也可以 yield 字符串或包含
`content`、`reasoning_content`、`tool_calls` 的字典。

```python
from lazyfox import AsyncHTTP, Proxy, parse_async

web = AsyncHTTP(base="https://upstream.example", tries=3)

async def reply(prompt, model, body):
    async with web.stream("POST", "/chat", json={"message": prompt}) as response:
        response.raise_for_status()
        async for event in parse_async(response.aiter_lines()):
            data = event.json()
            if data and data.get("text"):
                yield data["text"]

api = Proxy("example-model", reply)
api.run(port=8000)
```

以上代码自动提供：

- `GET /v1/models`
- `POST /v1/chat/completions`，支持流式与非流式
- `POST /v1/responses`，支持流式与非流式

## HTTP 自动登录

```python
from lazyfox import HTTP

def login(web):
    token = web.post("/login", json={"user": "demo"}, auth=False).json()["token"]
    web.headers["Authorization"] = f"Bearer {token}"

web = HTTP(base="https://example.com", proxy=None, tries=3, login=login)
data = web.get("/api/private").json()                    # 401/403 时自动登录后重发
```

GET/HEAD/OPTIONS 默认可重试；POST、PUT 等可能产生副作用的请求默认只发送一次。
确认上游支持幂等键或请求可安全重复时，显式传 `replay_safe=True`。

## 保存可靠性

`Lines` 和 `Table` 使用同目录临时文件 + 原子替换保存，多线程操作同一路径时共享锁。
若账号 JSON 已损坏，`Table` 会抛出 `StoreError` 并保留原文件，不会把损坏文件当空库覆盖。

## 测试

```bash
uv run pytest tests/
uv run ruff check tools/ lazyfox/ tests/
```

浏览器测试会真实启动无头 Chrome，覆盖元素定位、填写回读、逐字输入、复选框、下拉框和
点击结果判断。没有可用浏览器时仅跳过该项。

## 许可

[AGPL-3.0](LICENSE)
