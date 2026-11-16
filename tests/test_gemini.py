import json

import httpx

from app.agent.llm import GeminiClient

TOOLS = [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def make(handler) -> GeminiClient:
    return GeminiClient("key", "m", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_tool_call_keeps_thought_signature_and_history_is_translated():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["x-goog-api-key"] == "key"
        if len(seen) == 1:
            part = {"functionCall": {"name": "t", "args": {"a": 1}}, "thoughtSignature": "SIG"}
            data = {"candidates": [{"content": {"parts": [part]}}]}
        else:
            data = {
                "candidates": [{"content": {"parts": [{"text": "fatto"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
            }
        return httpx.Response(200, json=data)

    llm = make(handler)
    first = await llm.complete("sys", [{"role": "user", "content": "ciao"}], TOOLS)
    assert first.tool_uses[0].name == "t" and first.tool_uses[0].input == {"a": 1}
    assert seen[0]["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]

    history = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": first.to_assistant_content()},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": first.tool_uses[0].id, "content": "ok"}
            ],
        },
    ]
    second = await llm.complete("sys", history, TOOLS)
    assert second.text == "fatto" and (second.input_tokens, second.output_tokens) == (5, 2)
    parts = seen[1]["contents"][1]["parts"]
    assert parts[0]["thoughtSignature"] == "SIG"
    reply = seen[1]["contents"][2]["parts"][0]["functionResponse"]
    assert reply["name"] == "t" and reply["response"] == {"result": "ok"}


async def test_busy_model_is_retried(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda s: _noop())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    out = await make(handler).complete("s", [{"role": "user", "content": "x"}], [])
    assert out.text == "ok" and len(calls) == 3


async def _noop():
    return None


def compat(handler):
    from app.agent.llm import OpenAICompatClient

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatClient("https://x/v1", "key", "m", http)


async def test_openai_compat_tool_call_and_history():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer key"
        if len(seen) == 1:
            call = {"id": "c1", "function": {"name": "t", "arguments": '{"a": 1}'}}
            msg = {"content": None, "tool_calls": call and [call]}
        else:
            msg = {"content": "fatto"}
        usage = {"prompt_tokens": 4, "completion_tokens": 1}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": usage})

    llm = compat(handler)
    first = await llm.complete("sys", [{"role": "user", "content": "ciao"}], TOOLS)
    assert first.tool_uses[0].id == "c1" and first.tool_uses[0].input == {"a": 1}
    history = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": first.to_assistant_content()},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
        },
    ]
    second = await llm.complete("sys", history, TOOLS)
    assert second.text == "fatto" and second.input_tokens == 4
    roles = [m["role"] for m in seen[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert seen[1]["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'


async def test_openai_compat_streams_text_and_tool_fragments():
    def sse(*chunks):
        lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=lines.encode())

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        t1 = {"index": 0, "id": "c1", "function": {"name": "t", "arguments": '{"a"'}}
        t2 = {"index": 0, "function": {"arguments": ": 2}"}}
        return sse(
            {"choices": [{"delta": {"content": "Ciao "}}]},
            {"choices": [{"delta": {"content": "mondo."}}]},
            {"choices": [{"delta": {"tool_calls": [t1]}}]},
            {"choices": [{"delta": {"tool_calls": [t2]}}]},
        )

    got: list[str] = []

    async def sink(piece: str) -> None:
        got.append(piece)

    out = await compat(handler).complete("s", [{"role": "user", "content": "x"}], TOOLS, sink)
    assert out.text == "Ciao mondo." and got[-1] == "\n"
    assert out.tool_uses[0].input == {"a": 2}
