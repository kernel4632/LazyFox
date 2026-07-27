"""
SSE 解析工具：一行代码把 Server-Sent Events 流拆成结构化的事件序列。

设计思想：
逆向 AI 站点做代理时,上游几乎都返回 SSE 格式（text/event-stream）。每次自己写
"逐行读 → 攒 data → 遇到空行 yield"很烦且容易出错。这个文件把 SSE 协议规范
完整实现一遍，之后无论是 httpx 的响应流、requests 的 iter_lines、还是 websocket
转来的文本，都能一行解析。

里面有什么：
- parse(source)          同步版：传入行迭代器（如 response.iter_lines()），yield 事件
- parse_async(source)    异步版：传入异步行迭代器（如 response.aiter_lines()），yield 事件
- Event                  解析出的事件对象，含 event(类型)、data(内容)、id、retry 字段

事件对象格式：
    Event.event   →  事件类型字符串，默认 "message"
    Event.data    →  数据内容字符串（多行 data 会用换行拼接）
    Event.json()  →  尝试把 data 解析成字典/列表，解析失败返回 None

怎么调用：
    from tools.sse import parse, parse_async

    # 同步（配合 httpx stream 或 requests）
    response = httpx.get(url, stream=True)
    for event in parse(response.iter_lines()):
        print(event.event, event.json())

    # 异步（配合 httpx AsyncClient）
    async for event in parse_async(response.aiter_lines()):
        if event.data == "[DONE]":
            break
        chunk = event.json()
"""

import json                                                 # 用于把事件的 data 字段从 JSON 字符串解析成字典


class Event:
    """一个解析出来的 SSE 事件。"""

    # --- 初始化：填入各字段的默认值 ---
    def __init__(self):
        self.event = "message"                             # 事件类型，SSE 规范默认 "message"
        self.data = ""                                     # 事件数据，可能是 JSON 也可能是纯文本
        self.id = ""                                       # 事件 ID（可选，大多数场景用不到）
        self.retry = None                                  # 重连间隔毫秒数（可选，客户端用来决定重连频率）

    # --- 把 data 字段尝试解析为字典/列表 ---
    def json(self):
        if not self.data or self.data == "[DONE]":         # 空数据或 OpenAI 的结束标记直接返回 None
            return None
        try:
            return json.loads(self.data)                    # 尝试 JSON 解析
        except (json.JSONDecodeError, ValueError):         # 不是合法 JSON（如纯文本事件）
            return None                                    # 返回 None 让调用方自己处理原始 data

    def __repr__(self):
        return f"Event(event={self.event!r}, data={self.data[:60]!r})"


# --- 同步解析：传入任意行迭代器，yield 完整事件 ---
def parse(lines):
    # lines：可迭代对象，每次 yield 一行文本（不含末尾换行）
    # 例如 response.iter_lines()、open(file) 等
    event = Event()                                        # 当前正在攒的事件
    has_data = False                                       # 标记是否收到过 data 行（空事件不 yield）

    for raw in lines:                                      # 逐行遍历输入流
        line = raw.rstrip("\r\n") if isinstance(raw, str) else raw.decode("utf-8", errors="replace").rstrip("\r\n")

        if not line:                                       # 空行 = 事件分隔符，该 yield 当前事件了
            if has_data:                                   # 只有真正收到过 data 才算一个有效事件
                yield event
            event = Event()                                # 无论是否 yield，都开始攒下一个事件
            has_data = False
            continue

        if line.startswith(":"):                           # 冒号开头是注释行，SSE 规范要求静默忽略
            continue

        # 拆分 "field: value" 格式
        if ":" in line:
            field, value = line.split(":", 1)              # 只拆第一个冒号，value 里可能还有冒号
            value = value.lstrip(" ")                      # 规范要求去掉 value 前面的一个空格（如果有的话）
        else:
            field, value = line, ""                        # 没有冒号的行，整行是 field，value 为空

        if field == "data":                                # data 字段：事件的实际内容
            if has_data:                                   # 多行 data 用换行符拼接（SSE 规范）
                event.data += "\n" + value
            else:
                event.data = value
            has_data = True
        elif field == "event":                             # event 字段：事件类型
            event.event = value
        elif field == "id":                                # id 字段：事件 ID
            event.id = value
        elif field == "retry":                             # retry 字段：重连间隔
            try:
                event.retry = int(value)
            except ValueError:
                pass                                       # 非数字的 retry 值静默忽略

    if has_data:                                           # 流结束时如果还有未 yield 的事件，补上
        yield event


# --- 异步解析：传入异步行迭代器，yield 完整事件 ---
async def parse_async(lines):
    # lines：异步可迭代对象，每次 yield 一行文本
    # 例如 response.aiter_lines()
    event = Event()
    has_data = False

    async for raw in lines:                                # 异步逐行遍历
        line = raw.rstrip("\r\n") if isinstance(raw, str) else raw.decode("utf-8", errors="replace").rstrip("\r\n")

        if not line:
            if has_data:
                yield event
            event = Event()
            has_data = False
            continue

        if line.startswith(":"):
            continue

        if ":" in line:
            field, value = line.split(":", 1)
            value = value.lstrip(" ")
        else:
            field, value = line, ""

        if field == "data":
            if has_data:
                event.data += "\n" + value
            else:
                event.data = value
            has_data = True
        elif field == "event":
            event.event = value
        elif field == "id":
            event.id = value
        elif field == "retry":
            try:
                event.retry = int(value)
            except ValueError:
                pass

    if has_data:
        yield event
