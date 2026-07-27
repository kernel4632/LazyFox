"""OpenAI 代理骨架测试：覆盖模型、Chat/Responses 的流式与非流式输出。"""

from fastapi.testclient import TestClient                  # 在内存里调用 FastAPI，不启动真实端口

from tools.proxy import Proxy                              # 被测代理骨架


async def reply(text, model, body):
    yield {"reasoning_content": "think "}                  # 先产出思考内容
    yield "hello "                                          # 再产出两段正文
    yield text


def test_models_and_chat():
    api = Proxy("demo", reply)                             # 建立最小代理
    client = TestClient(api.app)                           # 创建内存客户端
    models = client.get("/v1/models").json()               # 查询模型列表
    assert models["data"][0]["id"] == "demo"              # 默认模型应存在

    body = {"model": "demo", "messages": [{"role": "user", "content": "world"}]}
    result = client.post("/v1/chat/completions", json=body).json()  # 非流式聊天
    message = result["choices"][0]["message"]              # 取 assistant 消息
    assert message["content"] == "hello world"             # 多段正文应自动合并
    assert message["reasoning_content"] == "think "         # 思考内容应独立保留


def test_chat_stream():
    api = Proxy("demo", reply)
    client = TestClient(api.app)
    body = {"messages": [{"role": "user", "content": "world"}], "stream": True}
    text = client.post("/v1/chat/completions", json=body).text  # 收集完整测试响应流
    assert '"content": "hello "' in text                   # 正文增量已输出
    assert '"reasoning_content": "think "' in text          # 思考增量已输出
    assert "data: [DONE]" in text                           # 结束标记存在


def test_responses_both_modes():
    api = Proxy("demo", reply)
    client = TestClient(api.app)
    result = client.post("/v1/responses", json={"input": "world"}).json()  # 非流式 Responses
    assert result["output"][0]["content"][0]["text"] == "hello world"  # 完整正文正确

    text = client.post("/v1/responses", json={"input": "world", "stream": True}).text
    assert "response.output_text.delta" in text             # 流式增量事件存在
    assert "response.completed" in text                     # 完成事件存在
