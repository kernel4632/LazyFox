#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
把 goody2.ai 包装成 OpenAI Responses API 协议的中转代理

上游 goody2.ai 的接口很简单：
  POST https://www.goody2.ai/send
  请求体：{"message": "用户消息", "conversationToken": "可选的会话令牌", "debugParams": null}
  响应：SSE 事件流，每个事件格式为 event: message\\ndata: {"content":"文本片段"}\\n\\n
  最后一个事件带 conversationToken：data: {"conversation":"jwt令牌"}

本代理对外暴露的接口：
  GET  /v1/models             → 返回静态模型列表
  POST /v1/responses          → 创建响应（支持流式和非流式）

调用示例：
  curl http://localhost:46346/v1/models
  curl -X POST http://localhost:46346/v1/responses -H "Content-Type: application/json" -d '{"model":"goody2","input":"你好","stream":true}'
  curl -X POST http://localhost:46346/v1/responses -H "Content-Type: application/json" -d '{"model":"goody2","input":"1+1等于几","stream":false}'
  curl -X POST http://localhost:46346/v1/responses -H "Content-Type: application/json" -d '{"model":"goody2","input":[{"role":"user","content":"你好"}],"stream":true}'
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Generator

import httpx
import uvicorn
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from rich.console import Console

load_dotenv()


class Config:
    """
    集中管理运行配置和默认请求头。

    base_url：goody2 上游地址
    port：本代理监听端口
    headers：模拟浏览器请求头，和 goody2 网站真实请求一致
    """

    base_url = os.getenv("GOODY2_BASE_URL", "https://www.goody2.ai").rstrip("/")
    port = int(os.getenv("GOODY2_PORT", "46346"))
    debug = os.getenv("DEBUG", "false").strip().lower() == "true"
    default_model = os.getenv("GOODY2_MODEL", "goody2").strip()
    user_agent = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
    )

    @classmethod
    def make_headers(cls) -> dict:
        """上游请求头，从 goody2.ai 真实抓包得到。"""
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Content-Type": "text/plain",
            "DNT": "1",
            "Origin": cls.base_url,
            "Pragma": "no-cache",
            "Referer": f"{cls.base_url}/chat",
            "User-Agent": cls.user_agent,
        }


console = Console()

# 模块级的 httpx 异步客户端，用 lifespan 管理：启动时创建、关闭时清理
# 这样流式响应在读取过程中 client 不会被提前关闭
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app_instance):
    """FastAPI 生命周期管理：启动时创建 httpx 客户端，关闭时清理。"""
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))
    yield
    await http_client.aclose()
    http_client = None


app = FastAPI(title="Goody2 Responses API Proxy", lifespan=lifespan)


def make_error_response(message: str, error_type: str = "server_error") -> dict:
    """构造 OpenAI 风格的错误响应体。"""
    return {"error": {"message": message, "type": error_type}}


def log_info(title: str, message: str) -> None:
    """用 Rich 输出中文彩色日志。"""
    console.log(f"[bold cyan]信息[/] [{title}] {message}")


def log_error(title: str, message: str) -> None:
    console.log(f"[bold red]错误[/] [{title}] {message}")


def log_debug(title: str, message: str) -> None:
    if Config.debug:
        console.log(f"[bold magenta]调试[/] [{title}] {message}")


# ─────────────────────── 上游请求 ───────────────────────


async def send_to_goody2(message: str, conversation_token: str | None = None):
    """
    向 goody2.ai 发起 SSE 流式请求，返回 httpx 响应对象。

    用模块级的 http_client 发请求，这样流式响应在读取过程中 client 不会被提前关闭。
    之前用 async with AsyncClient() 在函数返回后关闭了 client，
    导致后续 aiter_lines() 读流时报 httpx.ReadError。

    输入：用户消息文本、可选的会话令牌
    输出：httpx.Response 对象（流式）
    会修改：无
    结果：调用者可以从 response.aiter_lines() 读取 SSE 事件流
    """
    if not message:
        raise ValueError("消息不能为空")

    headers = Config.make_headers()
    body = {"message": message, "debugParams": None}
    if conversation_token:
        body["conversationToken"] = conversation_token

    log_info("上游请求", f"向 goody2.ai 发送消息，长度={len(message)}，有会话令牌={bool(conversation_token)}")
    log_debug("上游请求", f"请求体：{json.dumps(body, ensure_ascii=False)[:300]}")

    # 用模块级 client 发流式请求，client 的生命周期由 FastAPI lifespan 管理
    req = http_client.build_request("POST", f"{Config.base_url}/send", headers=headers, json=body)
    response = await http_client.send(req, stream=True)
    response.raise_for_status()
    return response


# ─────────────────────── SSE 解析 ───────────────────────


async def parse_goody2_sse(response) -> Generator[tuple[str, dict], None, None]:
    """
    从 goody2 的 SSE 响应中逐行解析事件，yield (event_type, data_dict)。

    goody2 的 SSE 格式很固定：
      event: message
      data: {"content":"文本片段"}

    最后一个事件带会话令牌：
      data: {"conversation":"jwt令牌"}

    输入：httpx.Response 对象（流式）
    输出：yield (event_type, data_dict) 元组
    会修改：无
    结果：调用者拿到每个 SSE 事件的类型和数据
    """
    event_type = "message"  # goody2 只用这一种事件类型
    async for raw_line in response.aiter_lines():
        if not raw_line:
            continue
        # 解析 event: xxx 行，记录事件类型
        if raw_line.startswith("event: "):
            event_type = raw_line[7:].strip()
            continue
        # 解析 data: xxx 行，提取 JSON 数据
        if raw_line.startswith("data: "):
            try:
                data = json.loads(raw_line[6:])
                yield event_type, data
            except json.JSONDecodeError:
                continue
            event_type = "message"  # 重置为默认


# ─────────────────────── OpenAI Responses API 格式构造 ───────────────────────


def make_response_id() -> str:
    """生成 OpenAI 风格的响应 ID，格式：resp-随机的32位hex。"""
    return f"resp-{uuid.uuid4().hex[:32]}"


def make_output_id() -> str:
    """生成消息项 ID，格式：msg-随机的32位hex。"""
    return f"msg-{uuid.uuid4().hex[:32]}"


def make_text_id() -> str:
    """生成文本内容项 ID，格式：text-随机的24位hex。"""
    return f"text-{uuid.uuid4().hex[:24]}"


def build_response_object(response_id: str, model: str, text: str, created_at: int) -> dict:
    """
    构造 OpenAI Responses API 非流式响应体。

    输入：响应ID、模型名、完整回复文本、创建时间戳
    输出：完整的 Responses API 响应字典
    会修改：无
    结果：可以直接 JSONResponse 返回给调用者
    """
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": make_output_id(),
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
        "metadata": {},
    }


# ── 流式事件构造函数 ──


def make_stream_event_created(response_id: str, model: str, created_at: int, seq: int) -> dict:
    """response.created 事件，流开始时发送，告诉调用者响应已创建。"""
    return {
        "type": "response.created",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": "in_progress",
            "model": model,
            "output": [],
            "usage": None,
            "metadata": {},
        },
        "sequence_number": seq,
    }


def make_stream_event_in_progress(response_id: str, model: str, created_at: int, seq: int) -> dict:
    """response.in_progress 事件，紧跟 created 之后发送。"""
    return {
        "type": "response.in_progress",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": "in_progress",
            "model": model,
            "output": [],
            "usage": None,
            "metadata": {},
        },
        "sequence_number": seq,
    }


def make_stream_event_output_item_added(output_id: str, seq: int) -> dict:
    """response.output_item.added 事件，告诉调用者开始输出一个消息项。"""
    return {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {
            "type": "message",
            "id": output_id,
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        },
        "sequence_number": seq,
    }


def make_stream_event_content_part_added(output_id: str, seq: int) -> dict:
    """response.content_part.added 事件，告诉调用者开始输出一个文本内容项。"""
    return {
        "type": "response.content_part.added",
        "item_id": output_id,
        "output_index": 0,
        "content_index": 0,
        "part": {
            "type": "output_text",
            "text": "",
            "annotations": [],
        },
        "sequence_number": seq,
    }


def make_stream_event_text_delta(output_id: str, delta: str, seq: int) -> dict:
    """response.output_text.delta 事件，每个文本片段发送一次，这是流式的核心事件。"""
    return {
        "type": "response.output_text.delta",
        "item_id": output_id,
        "output_index": 0,
        "content_index": 0,
        "delta": delta,
        "sequence_number": seq,
    }


def make_stream_event_text_done(output_id: str, full_text: str, seq: int) -> dict:
    """response.output_text.done 事件，文本输出完毕时发送，带上完整文本。"""
    return {
        "type": "response.output_text.done",
        "item_id": output_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
        "sequence_number": seq,
    }


def make_stream_event_output_item_done(output_id: str, full_text: str, seq: int) -> dict:
    """response.output_item.done 事件，消息项输出完毕时发送。"""
    return {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "type": "message",
            "id": output_id,
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": full_text,
                    "annotations": [],
                }
            ],
        },
        "sequence_number": seq,
    }


def make_stream_event_completed(response_id: str, model: str, created_at: int, output_id: str, full_text: str, seq: int) -> dict:
    """response.completed 事件，整个响应完成时发送，包含完整输出和用量。"""
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "id": output_id,
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": full_text,
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "metadata": {},
        },
        "sequence_number": seq,
    }


# ─────────────────────── 从请求中提取用户消息 ───────────────────────


def extract_user_message(input_data) -> str:
    """
    从 OpenAI Responses API 的 input 字段中提取用户消息文本。

    input 可以是字符串，也可以是消息数组：
    - 字符串：直接作为用户消息
    - 数组：取最后一条 role=user 的 content 文本

    输入：input 字段的值（str 或 list）
    输出：用户消息文本
    会修改：无
    结果：调用者拿到纯文本消息发给上游
    """
    if isinstance(input_data, str):
        return input_data

    if isinstance(input_data, list):
        # 从消息列表中找最后一条用户消息
        for item in reversed(input_data):
            if not isinstance(item, dict):
                continue
            if item.get("role") != "user":
                continue
            content = item.get("content", "")
            # content 可能是字符串，也可能是内容数组 [{"type":"input_text","text":"xxx"}]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in ("input_text", "text")]
                return "".join(text_parts)

    return ""


# ─────────────────────── FastAPI 路由 ───────────────────────


@app.get("/v1/models")
async def list_models():
    """返回静态模型列表，goody2 只有一个模型。"""
    return {
        "object": "list",
        "data": [
            {
                "id": Config.default_model,
                "object": "model",
                "created": 1700000000,
                "owned_by": "goody2",
            }
        ],
    }


@app.post("/v1/responses")
async def create_response(request: Request):
    """
    核心路由：接收 OpenAI Responses API 请求，转发给 goody2.ai，转换响应格式后返回。

    请求体格式：
    {
      "model": "goody2",
      "input": "你好" 或 [{"role":"user","content":"你好"}],
      "stream": true 或 false
    }

    流式响应：返回 SSE 事件流，遵循 OpenAI Responses API 的流式格式
    非流式响应：返回完整 JSON 响应体
    """
    body = await request.json()
    input_data = body.get("input", "")
    model = body.get("model") or Config.default_model
    stream = bool(body.get("stream", False))

    # 从 input 中提取用户消息
    user_message = extract_user_message(input_data)
    if not user_message:
        return JSONResponse(content=make_error_response("input 不能为空", "invalid_request_error"), status_code=400)

    response_id = make_response_id()
    created_at = int(time.time())
    output_id = make_output_id()

    log_info("请求", f"收到请求，模式={'流式' if stream else '非流式'}，消息长度={len(user_message)}")

    try:
        upstream = await send_to_goody2(user_message)

        if stream:
            # ── 流式响应：边读 goody2 的 SSE 边转换成 OpenAI Responses API 的 SSE 格式 ──
            # sequence_number 从 0 开始，每个 SSE 事件递增 1，客户端依赖它排序和关联事件
            # item_id 字段让客户端能把 delta 事件关联到对应的 message item
            async def generate_stream():
                seq = 0  # sequence_number 计数器

                # 发送响应创建事件序列：created → in_progress → output_item.added → content_part.added
                evt = make_stream_event_created(response_id, model, created_at, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                evt = make_stream_event_in_progress(response_id, model, created_at, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                evt = make_stream_event_output_item_added(output_id, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                evt = make_stream_event_content_part_added(output_id, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

                # 逐个读取 goody2 的 SSE 事件，把 content 字段转成 text.delta 事件
                full_text = ""
                event_count = 0
                try:
                    async for event_type, data in parse_goody2_sse(upstream):
                        # goody2 的最后一个事件带 conversation 令牌，不是文本内容，跳过
                        if "conversation" in data:
                            log_debug("流式", f"收到会话令牌，长度={len(data['conversation'])}")
                            continue

                        content = data.get("content", "")
                        if content:
                            event_count += 1
                            full_text += content
                            evt = make_stream_event_text_delta(output_id, content, seq)
                            seq += 1
                            yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except (httpx.ReadError, httpx.ReadTimeout, ConnectionError) as error:
                    log_error("流式", f"上游连接中断：{error}，已收到 {event_count} 个事件，正文长度={len(full_text)}")

                # 流式读取完毕后关闭上游响应，释放连接
                try:
                    await upstream.aclose()
                except Exception:
                    pass

                # 发送完成事件序列：text.done → output_item.done → completed
                evt = make_stream_event_text_done(output_id, full_text, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                evt = make_stream_event_output_item_done(output_id, full_text, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                evt = make_stream_event_completed(response_id, model, created_at, output_id, full_text, seq)
                seq += 1
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

                log_info("流式", f"流式响应完成，事件数={event_count}，正文长度={len(full_text)}")

            return StreamingResponse(generate_stream(), media_type="text/event-stream")

        # ── 非流式响应：先收集完整文本，再一次性返回 ──
        full_text = ""
        async for event_type, data in parse_goody2_sse(upstream):
            # 跳过会话令牌事件
            if "conversation" in data:
                continue
            content = data.get("content", "")
            full_text += content

        # 用完上游响应后关闭，释放连接
        await upstream.aclose()

        result = build_response_object(response_id, model, full_text, created_at)
        log_info("非流式", f"非流式响应完成，正文长度={len(full_text)}")
        return JSONResponse(content=result)

    except httpx.HTTPStatusError as error:
        log_error("上游响应", f"上游返回错误：{error}")
        return JSONResponse(content=make_error_response(f"上游请求失败：{error}", "upstream_error"), status_code=502)
    except Exception as error:
        log_error("响应处理", f"处理失败：{error}")
        return JSONResponse(content=make_error_response(str(error)), status_code=500)


if __name__ == "__main__":
    log_info("启动", f"Goody2 中转代理启动，监听 http://0.0.0.0:{Config.port}")
    uvicorn.run(app, host="0.0.0.0", port=Config.port, log_level="warning")
