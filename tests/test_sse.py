"""SSE 解析测试：覆盖事件类型、多行 data、JSON、字节输入和流末尾补发。"""

import asyncio                                              # 驱动异步 SSE 测试

from tools.sse import parse, parse_async                    # 被测的同步/异步解析入口


def test_parse_json_event():
    lines = ["event: message", 'data: {"text":"hi"}', ""]  # 一个标准 JSON SSE 事件
    events = list(parse(lines))                             # 同步解析全部事件
    assert len(events) == 1                                 # 应得到一个完整事件
    assert events[0].event == "message"                    # 类型正确
    assert events[0].json() == {"text": "hi"}              # data 能直接解成字典


def test_parse_multiline_and_bytes():
    lines = [b"id: 8", b"data: first", b"data: second", b""]  # 字节输入 + 两行 data
    event = list(parse(lines))[0]                           # 解析第一个事件
    assert event.id == "8"                                 # ID 应保留
    assert event.data == "first\nsecond"                   # 多行 data 按规范用换行拼接


def test_parse_tail_without_blank_line():
    event = list(parse(["data: last"]))[0]                 # 流结束前没有空行
    assert event.data == "last"                            # 解析器仍应补发最后事件


def test_parse_async():
    async def lines():
        for line in ["event: token", "data: hello", ""]:   # 模拟 httpx.aiter_lines()
            yield line

    async def collect():
        return [event async for event in parse_async(lines())]  # 消费异步事件流

    events = asyncio.run(collect())                        # 在测试事件循环里执行
    assert events[0].event == "token"                      # 异步版应保留事件类型
    assert events[0].data == "hello"                       # 数据应正确
