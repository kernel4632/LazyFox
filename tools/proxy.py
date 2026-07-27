"""
OpenAI 代理骨架：只写一个 reply 函数，就把任意上游站点变成 OpenAI 兼容接口。

设计思想：
reverse 脚本真正独有的代码只有两块：怎样请求目标站点、怎样从上游事件里拿到文字。
FastAPI 路由、模型列表、请求校验、流式 SSE、非流式合并、OpenAI 响应格式都属于固定样板，
不应该每个站点再写几百行。本文件把固定样板全部做好，使用者只交出 reply 函数。

reply 接收三个参数：prompt（用户文字）、model（模型名）、body（完整请求体）。它可以返回：
- 一个字符串：适合上游直接返回完整答案
- 字符串迭代器 / 异步迭代器：适合 SSE，上游每来一段就 yield 一段
- 字典：支持 content、reasoning_content、tool_calls 字段

最小示例：
    from lazyfox import Proxy

    async def reply(prompt, model, body):
        yield "收到："
        yield prompt

    api = Proxy("demo", reply)
    api.run(port=8000)

自动提供：
- GET  /v1/models
- POST /v1/chat/completions（流式 + 非流式）
- POST /v1/responses（流式 + 非流式）
"""

import inspect                                              # 判断 reply 返回的是 awaitable、异步流还是普通值
import json                                                 # 把 OpenAI 流式事件编码成 SSE JSON 文本
import time                                                 # 生成 OpenAI 响应所需的创建时间
import uuid                                                 # 生成每次响应的唯一 ID

import uvicorn                                              # 启动 FastAPI 服务
from fastapi import FastAPI, Request                       # 提供路由和请求体读取能力
from fastapi.responses import JSONResponse, StreamingResponse  # 返回 JSON 或 SSE 流


# --- 从 OpenAI messages / Responses input 里取出最后一段用户文字 ---
def prompt(body):
    source = body.get("messages")                          # Chat Completions 使用 messages
    if source is None:
        source = body.get("input", "")                    # Responses API 使用 input
    if isinstance(source, str):                            # input 可以直接是一段字符串
        return source
    if not isinstance(source, list):                       # 既不是字符串也不是消息列表，无法提取
        return ""

    for message in reversed(source):                      # 从后往前找最后一条用户消息
        if not isinstance(message, dict):                  # 非字典项不是合法消息，跳过
            continue
        if message.get("role", "user") != "user":         # 只取用户消息，忽略 system/assistant/tool
            continue
        content = message.get("content", "")              # 读取消息正文
        if isinstance(content, str):                       # 普通文本消息直接返回
            return content
        if isinstance(content, list):                      # 多模态消息里只拼文字块
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") in ("text", "input_text")
            ]
            return "".join(parts)                          # 按原顺序拼成上游可用的纯文本
    return ""                                              # 没有用户文字


# --- 把 reply 的一项结果归一成统一字典 ---
def part(value):
    if value is None:                                      # None 表示没有输出，归一为空字典方便跳过
        return {}
    if isinstance(value, str):                             # 最常见的字符串片段归到 content
        return {"content": value}
    if isinstance(value, dict):                            # 高级场景可以直接 yield content/tool_calls 等字段
        return value
    return {"content": str(value)}                        # 其他简单类型转字符串，避免上游偶发数字导致崩溃


# --- 把任意 reply 返回值统一变成异步事件流 ---
async def read(value):
    if inspect.isawaitable(value):                         # async def 返回协程，先等待得到真正结果
        value = await value
    if hasattr(value, "__aiter__"):                       # 异步生成器：逐项原样转成统一字典
        async for item in value:
            yield part(item)
        return
    if isinstance(value, (str, dict)) or value is None:    # 单值结果只产生一个事件
        item = part(value)
        if item:
            yield item
        return
    if hasattr(value, "__iter__"):                        # 普通生成器/列表：同步逐项读取
        for item in value:
            yield part(item)
        return
    yield part(value)                                      # 最后兜底：未知对象转字符串事件


# --- 构造 OpenAI 风格错误体 ---
def error(message, kind="server_error"):
    return {"error": {"message": str(message), "type": kind}}  # 固定错误结构，SDK 客户端能直接识别


class Proxy:
    """OpenAI 兼容代理，一个实例就是一个可运行的 FastAPI 应用。"""

    # --- 建立代理并注册全部路由 ---
    def __init__(self, name, reply, models=None):
        # name：默认模型名，也是只有一个模型时对外展示的名称
        # reply：站点特定的上游调用函数
        # models：可选模型名列表，不传则只提供 name
        self.name = name                                    # 默认模型名
        self.reply = reply                                  # 保存上游动作，路由收到请求后调用
        self.models = list(models or [name])                # 对外模型列表
        self.app = FastAPI(title=f"{name} OpenAI Proxy")   # 创建独立 FastAPI 应用
        self._routes()                                      # 把 models/chat/responses 路由装上去

    # --- 调用上游 reply，返回统一异步事件流 ---
    async def ask(self, text, model, body):
        result = self.reply(text, model, body)              # 把用户文字、模型和完整请求交给站点代码
        async for item in read(result):                     # 不管 reply 返回哪种形态都统一成字典流
            yield item

    # --- 收集完整输出，供非流式接口使用 ---
    async def collect(self, text, model, body):
        content = []                                       # 普通回答片段按顺序收集
        reason = []                                        # 思考内容单独收集，兼容 reasoning_content
        tools = []                                         # 工具调用保持字典列表

        async for item in self.ask(text, model, body):     # 逐个消费上游事件
            if item.get("content"):
                content.append(str(item["content"]))       # 追加普通正文
            if item.get("reasoning_content"):
                reason.append(str(item["reasoning_content"]))  # 追加思考正文
            if item.get("tool_calls"):
                tools.extend(item["tool_calls"])           # 合并工具调用列表
        return "".join(content), "".join(reason), tools    # 返回三类完整结果

    # --- 注册 OpenAI 所需的固定路由 ---
    def _routes(self):
        @self.app.get("/v1/models")
        async def list_models():
            now = int(time.time())                         # 所有模型使用当前时间作为 created
            data = [
                {"id": model, "object": "model", "created": now, "owned_by": self.name}
                for model in self.models
            ]
            return {"object": "list", "data": data}      # OpenAI 模型列表固定结构

        @self.app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()                    # 读取完整请求体，工具调用等扩展字段会原样传给 reply
            text = prompt(body)                            # 提取最后一段用户文字
            if not text:
                return JSONResponse(error("messages 不能为空", "invalid_request_error"), status_code=400)
            model = body.get("model") or self.name         # 请求没传模型就用默认名称
            stream = bool(body.get("stream", False))       # 是否走 SSE 流式输出
            chat_id = f"chatcmpl-{uuid.uuid4().hex}"       # 本次回答唯一标识
            created = int(time.time())                     # 创建时间在整个流里保持一致

            if stream:
                return StreamingResponse(
                    self._chat_stream(text, model, body, chat_id, created),
                    media_type="text/event-stream",
                )

            try:
                content, reason, tools = await self.collect(text, model, body)  # 非流式先收完整结果
                message = {"role": "assistant", "content": content}  # 构造 assistant 消息
                if reason:
                    message["reasoning_content"] = reason  # 有思考内容才附加，普通客户端不受影响
                if tools:
                    message["tool_calls"] = tools          # 有工具调用时附加列表
                result = {
                    "id": chat_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
                return JSONResponse(result)                # 返回 OpenAI Chat Completions 完整结构
            except Exception as exc:
                return JSONResponse(error(exc), status_code=502)  # 上游失败统一反馈 502

        @self.app.post("/v1/responses")
        async def responses(request: Request):
            body = await request.json()                    # Responses API 请求体
            text = prompt(body)                            # input 字段归一成用户文字
            if not text:
                return JSONResponse(error("input 不能为空", "invalid_request_error"), status_code=400)
            model = body.get("model") or self.name         # 默认模型
            stream = bool(body.get("stream", False))       # 是否流式
            response_id = f"resp-{uuid.uuid4().hex}"       # Responses API 唯一标识
            created = int(time.time())                     # 创建时间

            if stream:
                return StreamingResponse(
                    self._response_stream(text, model, body, response_id, created),
                    media_type="text/event-stream",
                )

            try:
                content, reason, _tools = await self.collect(text, model, body)  # 收集完整输出
                result = self._response(response_id, model, created, content, reason)  # 构造 Responses 完整体
                return JSONResponse(result)
            except Exception as exc:
                return JSONResponse(error(exc), status_code=502)

    # --- 生成 Chat Completions SSE 流 ---
    async def _chat_stream(self, text, model, body, chat_id, created):
        first = self._chat_chunk(chat_id, model, created, {"role": "assistant"})  # 第一块声明角色
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
        tool_index = 0                                    # 工具调用需要稳定的递增 index

        try:
            async for item in self.ask(text, model, body):  # 上游每来一项立刻转换并下发
                delta = {}
                if item.get("content") is not None:
                    delta["content"] = str(item["content"])
                if item.get("reasoning_content") is not None:
                    delta["reasoning_content"] = str(item["reasoning_content"])
                if item.get("tool_calls"):
                    delta["tool_calls"] = item["tool_calls"]
                    tool_index += len(item["tool_calls"])  # 记录是否出现过工具调用
                if delta:
                    chunk = self._chat_chunk(chat_id, model, created, delta)
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            reason = "tool_calls" if tool_index else "stop"  # 有工具则用 tool_calls 结束原因
            end = self._chat_chunk(chat_id, model, created, {}, reason)
            yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"                       # OpenAI 流结束标记
        except Exception as exc:
            yield f"data: {json.dumps(error(exc), ensure_ascii=False)}\n\n"  # 流已开始，只能在 SSE 内反馈错误
            yield "data: [DONE]\n\n"

    # --- 构造一个 Chat Completions 流块 ---
    def _chat_chunk(self, chat_id, model, created, delta, finish=None):
        return {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    # --- 生成 Responses API SSE 流 ---
    async def _response_stream(self, text, model, body, response_id, created):
        output_id = f"msg-{uuid.uuid4().hex}"              # 整个流共用一个输出消息 ID
        full = []                                           # 收集完整文字，完成事件需要带回全文
        seq = 0                                             # Responses API 事件序号严格递增
        made = {"type": "response.created", "response": {"id": response_id, "status": "in_progress", "model": model}, "sequence_number": seq}
        yield self._event(made)                             # 首先通知客户端响应已创建
        seq += 1

        try:
            async for item in self.ask(text, model, body):  # 逐项读取上游
                value = item.get("content") or item.get("reasoning_content")
                if not value:
                    continue                              # Responses 简化骨架只输出文本，空项跳过
                value = str(value)
                full.append(value)                         # 保存全文供最终事件使用
                event = {
                    "type": "response.output_text.delta",
                    "item_id": output_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": value,
                    "sequence_number": seq,
                }
                yield self._event(event)                   # 立即发送文本增量
                seq += 1

            done = self._response(response_id, model, created, "".join(full), "")  # 最终完整响应
            event = {"type": "response.completed", "response": done, "sequence_number": seq}
            yield self._event(event)                       # 发送完成事件
        except Exception as exc:
            event = {"type": "error", **error(exc), "sequence_number": seq}
            yield self._event(event)                       # 在流内反馈错误

    # --- 把 Responses 事件编码成标准 SSE ---
    def _event(self, data):
        return f"event: {data['type']}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # --- 构造 Responses API 非流式完整对象 ---
    def _response(self, response_id, model, created, content, reason):
        item = {
            "type": "message",
            "id": f"msg-{uuid.uuid4().hex}",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
        }
        if reason:
            item["reasoning_content"] = reason             # 保留上游思考文本供支持该字段的客户端使用
        return {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "status": "completed",
            "model": model,
            "output": [item],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "metadata": {},
        }

    # --- 启动代理服务 ---
    def run(self, host="0.0.0.0", port=8000, **kwargs):
        uvicorn.run(self.app, host=host, port=port, **kwargs)  # 交给 uvicorn 启动，额外参数原样透传
