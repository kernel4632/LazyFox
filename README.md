<div align="center">

# LazyFox

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
| `first_form` / `turnstile` / `result` | HTML 表单分析 | 提取 action、字段约束、Turnstile 配置和结果页摘要 |
| `run_probes` / `form_cases` | 协议探针 | 批量提交字段变体并按响应语义聚类 |
| `turnstile_audit` / `turnstile_verify` | Turnstile 协议 | 识别测试 key、构造提交体、调用 siteverify 解析结果 |
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

## 跑通 Grok 注册示例

`register/grok.py` 是完整可运行的 x.ai 邮箱注册示例。它使用随机临时邮箱渠道，自动完成
邮箱提交、验证码读取、资料填写和结果保存；Cloudflare Turnstile 必须由用户在有头 Chrome
中手动完成。

```bash
uv run python register/grok.py \
  --count 1 \
  --no-headless \
  --mail-channel random \
  --email-attempts 3 \
  --flow-timeout 300 \
  --output token/grok-tokens.txt
```

运行过程：

1. 脚本从随机渠道申请临时邮箱，并等待 xAI 验证码。
2. 邮箱域名被拒绝、邮箱已关联现有账户或收不到验证码时，脚本会关闭当前 Chrome，换邮箱后完整重试。
3. 到达 Turnstile 时，Chrome 会恢复到前台并播放提示音。手动点击“请验证您是真人”即可继续。
4. 注册成功后，脚本留在 xAI 账户页面读取 `sso` Cookie，并原子去重写入 `--output` 文件。

正式注册不支持 `--headless`。无头 Chrome 无法可靠访问 xAI，也无法完成人机验证；传入该参数时
脚本会在创建邮箱和浏览器前退出。只检查站点是否可访问时可以使用：

```bash
uv run python register/grok.py --check --headless --flow-timeout 45
```

每个账号运行在独立子进程中。超过 `--flow-timeout` 后，父进程会终止脚本及其 Chrome 进程树，
避免网络请求或浏览器永久挂住。失败截图保存在 `output/grok/`。

## 写注册机

```python
from lazyfox import Browser, Lines, Mail, Person

who = Person(lang="en").all()                         # 一次生成注册资料

with Mail() as mail:                                   # 不指定渠道时随机选择
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

在 Windows 有头模式下，LazyFox 会关闭 Chrome 的后台计时和窗口遮挡冻结。浏览器可在自动步骤中
保持后台运行；需要真人操作时，可调用 `page.front()` 恢复窗口并播放提示音。

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

with Mail(proxy="http://127.0.0.1:7890", timeout=20) as mail:  # channel=None 表示随机渠道
    address = mail.create(
        domains=["example.com", "example.net"],       # 按优先级指定渠道支持的候选域名
        duration=30,                                    # 有效分钟数
        max_channels=20,                                # 随机模式最多尝试渠道数
        total_timeout=60,
        tries=3,
    )
    code = mail.wait_code(subject="verify", sender="no-reply", timeout=120)
    link = mail.wait_link(keyword="confirm", sender="no-reply")
```

`domain` / `domains` 指 `@` 后面的邮箱域名，只能使用具体渠道实际支持的值；`suffix` 指邮箱
用户名后缀，不是邮箱域名。固定渠道可传 `Mail(channel="渠道标识")`，可用标识通过
`Mail.channels()` 查询。

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

逆向分析时，4xx/5xx 响应体经常包含关键错误原因。传 `check=False` 可保留失败响应而不抛异常：

```python
bad = web.post("/submit", data={"token": "fake"}, check=False, replay_safe=True)
print(bad.status_code, bad.text)
```

## 表单协议分析

```python
from lazyfox import HTTP, first_form, result, turnstile

web = HTTP(base="https://example.com")
html = web.get("/").text

form = first_form(html)                                  # 读取第一个 form 的 method/action/字段
print(form.method, form.action)
print(form.field("email").required)

widget = turnstile(html)                                 # 读取 data-sitekey/data-theme 等配置
print(widget.get("sitekey"))

response = web.post(
    form.action,
    data=form.data(email="a@b.com", **{"cf-turnstile-response": "fake"}),
    check=False,
    replay_safe=True,
)
print(result(response.text).summary())                   # 提取 title/状态短语/主标题/说明文本
```

### 协议探针和 Turnstile 审计

```python
from lazyfox import HTTP, first_form, form_cases, groups, run_probes, turnstile_audit, turnstile_dummy, turnstile_verify

web = HTTP(base="https://example.com")
html = web.get("/").text
form = first_form(html)
widget = turnstile_audit(html)

print(widget.sitekey, widget.mode, widget.dummy_allowed())  # 判断是否官方测试 key

base = form.data(team="lazyfox", message="probe")
base.pop("cf-turnstile-response", None)

cases = form_cases(form.action, base)                    # 缺 token、假 token、JSON、query、重复字段等变体
cases.append(cases[0].__class__(
    "dummy-token",
    path=form.action,
    kwargs={"data": turnstile_dummy(base)},
))

findings = run_probes(web, cases)
for bucket in groups(findings):
    print([item.case for item in bucket], "=>", bucket[0].summary())

check = turnstile_verify(
    "XXXX.DUMMY.TOKEN.XXXX",
    "1x0000000000000000000000000000000AA",                # Cloudflare 官方测试 secret
)
print(check.success, check.errors, check.hostname)
```

`turnstile_audit` 只识别配置和测试 key；`turnstile_verify` 只调用 Cloudflare 官方 `siteverify`。
二者都不生成真实 Cloudflare token。生产 Turnstile token 必须由 Cloudflare 正常签发并由服务端
`siteverify` 校验。

## 保存可靠性

`Lines` 和 `Table` 使用同目录临时文件 + 原子替换保存，多线程操作同一路径时共享锁。
若账号 JSON 已损坏，`Table` 会抛出 `StoreError` 并保留原文件，不会把损坏文件当空库覆盖。

## 测试

```bash
uv run pytest tests/
uv run ruff check tools/ lazyfox/ register/grok.py tests/
```

浏览器测试会真实启动无头 Chrome，覆盖元素定位、填写回读、逐字输入、复选框、下拉框和
点击结果判断。没有可用浏览器时仅跳过该项。Grok 测试使用假浏览器和假邮箱离线覆盖完整流程，
不会创建真实账号；真实注册仍需显式运行上面的有头命令。

## 许可

[AGPL-3.0](LICENSE)
