import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TextSink = Callable[[str], Awaitable[None]]  # receives the model's text as it is written


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""  # which model actually produced this (matters with latency routing)

    def to_assistant_content(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        blocks += [
            {"type": "tool_use", "id": t.id, "name": t.name, "input": t.input}
            for t in self.tool_uses
        ]
        return blocks


class LLMClient(Protocol):
    model: str

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str, effort: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self._effort = effort  # "low" trades some depth for speed (voice fast path)

    async def complete(self, system, messages, tools, on_text: TextSink | None = None):
        """One model call. With `on_text` the reply is streamed and every piece of text is
        handed over as soon as it arrives (so a voice can start speaking before it is done)."""
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        params = dict(model=self.model, max_tokens=2048, system=system, messages=messages, **kwargs)
        if on_text is None:
            r = await self._client.messages.create(**params)
        else:
            async with self._client.messages.stream(**params) as stream:
                async for piece in stream.text_stream:
                    await on_text(piece)
                r = await stream.get_final_message()
            await on_text("\n")  # end of this reply: lets a listener release its last sentence
        out = LLMResponse(
            input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens, model=self.model
        )
        for b in r.content:
            if b.type == "text":
                out.text += b.text
            elif b.type == "tool_use":
                out.tool_uses.append(ToolUse(b.id, b.name, dict(b.input)))
        return out


class GeminiClient:
    """Google Gemini over REST (free tier available). Same contract as AnthropicClient: the
    agent keeps its history in Anthropic's block format and this class translates it."""

    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, client: Any = None) -> None:
        import httpx

        self._key = api_key
        self.model = model
        self._http = client or httpx.AsyncClient(timeout=60)

    @staticmethod
    def _contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names: dict[str, str] = {}  # tool_use id -> tool name (Gemini answers by name)
        out: list[dict[str, Any]] = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            if isinstance(m["content"], str):
                out.append({"role": role, "parts": [{"text": m["content"]}]})
                continue
            parts: list[dict[str, Any]] = []
            for b in m["content"]:
                if b["type"] == "text" and b["text"]:
                    parts.append({"text": b["text"]})
                elif b["type"] == "tool_use":
                    names[b["id"]] = b["name"]
                    part: dict[str, Any] = {"functionCall": {"name": b["name"], "args": b["input"]}}
                    if "|" in b["id"]:  # Gemini 3 wants its thought signature handed back
                        part["thoughtSignature"] = b["id"].split("|", 1)[1]
                    parts.append(part)
                elif b["type"] == "tool_result":
                    content = b["content"]
                    if not isinstance(content, str):
                        content = "".join(c.get("text", "") for c in content)
                    parts.append(
                        {
                            "functionResponse": {
                                "name": names.get(b["tool_use_id"], "tool"),
                                "response": {"result": content},
                            }
                        }
                    )
            if parts:
                out.append({"role": role, "parts": parts})
        return out

    async def complete(self, system, messages, tools, on_text: TextSink | None = None):
        import json

        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(messages),
            "generationConfig": {"maxOutputTokens": 2048},
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parametersJsonSchema": t["input_schema"],
                        }
                        for t in tools
                    ]
                }
            ]
        headers = {"x-goog-api-key": self._key}
        out = LLMResponse(model=self.model)
        usage: dict[str, Any] = {}

        def take(chunk: dict[str, Any]) -> list[str]:
            texts: list[str] = []
            usage.update(chunk.get("usageMetadata") or {})
            for cand in chunk.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("text") and not part.get("thought"):
                        texts.append(part["text"])
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        n = len(out.tool_uses)
                        args = dict(fc.get("args") or {})
                        sig = part.get("thoughtSignature")
                        call_id = f"call_{n}|{sig}" if sig else f"call_{n}"
                        out.tool_uses.append(ToolUse(call_id, fc["name"], args))
            return texts

        if on_text is None:
            url = f"{self.BASE}/{self.model}:generateContent"
            for attempt in range(4):  # free tier: busy (503) or rate-limited (429) now and then
                r = await self._http.post(url, json=body, headers=headers)
                if r.status_code not in (429, 503) or attempt == 3:
                    break
                await asyncio.sleep(2**attempt)
            r.raise_for_status()
            out.text = "".join(take(r.json()))
        else:
            url = f"{self.BASE}/{self.model}:streamGenerateContent?alt=sse"
            for attempt in range(4):
                async with self._http.stream("POST", url, json=body, headers=headers) as r:
                    if r.status_code in (429, 503) and attempt < 3:
                        await r.aread()
                    else:
                        if r.status_code >= 400:
                            await r.aread()
                            r.raise_for_status()
                        async for line in r.aiter_lines():
                            if line.startswith("data:"):
                                for piece in take(json.loads(line[5:])):
                                    out.text += piece
                                    await on_text(piece)
                        break
                await asyncio.sleep(2**attempt)
            await on_text("\n")
        out.input_tokens = usage.get("promptTokenCount", 0)
        out.output_tokens = usage.get("candidatesTokenCount", 0)
        return out


def _wait(response: Any, attempt: int) -> float:
    """Seconds to wait before retrying a busy/rate-limited call: what the server asks for."""
    h = response.headers
    try:
        return min(float(h["retry-after"]), 30.0)
    except (KeyError, ValueError):
        pass
    return float(2**attempt)


class OpenAICompatClient:
    """Any server speaking the OpenAI chat-completions protocol (Groq, OpenRouter, Ollama...).
    Same contract as the other clients; history stays in Anthropic's block format."""

    def __init__(self, base_url: str, api_key: str, model: str, client: Any = None) -> None:
        import httpx

        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.model = model
        self._http = client or httpx.AsyncClient(timeout=60)

    @staticmethod
    def _messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import json

        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m["content"], str):
                out.append({"role": m["role"], "content": m["content"]})
                continue
            text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in m["content"]
                if b["type"] == "tool_use"
            ]
            if calls:
                out.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            elif text:
                out.append({"role": m["role"], "content": text})
            for b in m["content"]:
                if b["type"] == "tool_result":
                    content = b["content"]
                    if not isinstance(content, str):
                        content = "".join(c.get("text", "") for c in content)
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": content,
                    }
                    out.append(tool_msg)
        return out

    async def complete(self, system, messages, tools, on_text: TextSink | None = None):
        """A model that reasons can spend all its tokens thinking and answer with nothing:
        in that case ask once more with twice the room (nothing was streamed, so it is safe)."""
        out = await self._complete_once(system, messages, tools, on_text, 1200)
        if not out.text.strip() and not out.tool_uses:
            out = await self._complete_once(system, messages, tools, on_text, 2400)
        return out

    async def _complete_once(self, system, messages, tools, on_text, max_tokens: int):
        import json

        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]
        headers = {"Authorization": f"Bearer {self._key}"}
        out = LLMResponse(model=self.model)
        calls: dict[int, dict[str, str]] = {}

        def take(choice: dict[str, Any]) -> str:
            part = choice.get("delta") or choice.get("message") or {}
            for tc in part.get("tool_calls") or []:
                blank = {"id": "", "name": "", "args": ""}
                slot = calls.setdefault(tc.get("index", len(calls)), blank)
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["args"] += fn.get("arguments") or ""
            return part.get("content") or ""

        def usage_of(chunk: dict[str, Any]) -> None:
            u = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage") or {}
            out.input_tokens = u.get("prompt_tokens", out.input_tokens)
            out.output_tokens = u.get("completion_tokens", out.output_tokens)

        for attempt in range(5):
            if on_text is None:
                r = await self._http.post(self._url, json=body, headers=headers)
                if r.status_code in (429, 503) and attempt < 4:
                    await asyncio.sleep(_wait(r, attempt))
                    continue
                r.raise_for_status()
                data = r.json()
                out.text = take(data["choices"][0])
                usage_of(data)
                break
            async with self._http.stream(
                "POST", self._url, json={**body, "stream": True}, headers=headers
            ) as r:
                if r.status_code in (429, 503) and attempt < 4:
                    await r.aread()
                    wait = _wait(r, attempt)
                else:
                    if r.status_code >= 400:
                        await r.aread()
                        r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.startswith("data:") or line.strip() == "data: [DONE]":
                            continue
                        chunk = json.loads(line[5:])
                        usage_of(chunk)
                        for choice in chunk.get("choices") or []:
                            piece = take(choice)
                            if piece:
                                out.text += piece
                                await on_text(piece)
                    await on_text("\n")
                    break
            await asyncio.sleep(wait)
        for i in sorted(calls):
            c = calls[i]
            args = json.loads(c["args"]) if c["args"].strip() else {}
            out.tool_uses.append(ToolUse(c["id"] or f"call_{i}", c["name"], args))
        return out


def build_llms(s: Any) -> tuple[LLMClient, LLMClient]:
    """(main, fast) models for the provider chosen in LLM_PROVIDER."""
    if s.llm_provider == "gemini":
        return GeminiClient(s.gemini_api_key, s.gemini_model), GeminiClient(
            s.gemini_api_key, s.gemini_fast_model
        )
    if s.llm_provider == "groq":
        base = "https://api.groq.com/openai/v1"
        return (
            OpenAICompatClient(base, s.groq_api_key, s.groq_model),
            OpenAICompatClient(base, s.groq_api_key, s.groq_fast_model),
        )
    if s.llm_provider == "anthropic":
        return (
            AnthropicClient(s.anthropic_api_key, s.anthropic_model),
            AnthropicClient(s.anthropic_api_key, s.fast_model, "low"),
        )
    raise ValueError(f"unknown LLM_PROVIDER: {s.llm_provider}")


class RoutedLLM:
    """Latency routing between a small fast model and the main one.

    The fast model handles the plain cases: answering directly, calling a read-only tool, and
    writing the answer from read-only results. The main model takes over whenever something
    could change the world (a write or confirm tool) or after such a tool ran, so decisions with
    consequences are never made by the small model.

    `model` is the main model's, which is what the run is billed against in the trace; a fast
    attempt that escalates is discarded and its (small) tokens are not counted. Each response
    says which model wrote it (`LLMResponse.model`).
    """

    def __init__(
        self, fast: LLMClient, main: LLMClient, safe_tools: frozenset[str] = frozenset()
    ) -> None:
        self.fast, self.main = fast, main
        self.safe_tools = safe_tools  # names of read-only tools the fast model may call
        self.model = main.model

    def _fast_may_handle(self, messages: list[dict[str, Any]]) -> bool:
        last = messages[-1] if messages else None
        if last is None or last["role"] != "user":
            return False
        if isinstance(last["content"], str):  # a fresh request from the user
            return True
        # results of tools: the fast model writes the answer only if every tool was read-only
        previous = messages[-2]["content"] if len(messages) > 1 else []
        called = {
            b["name"] for b in previous if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        return bool(called) and called <= self.safe_tools

    async def complete(self, system, messages, tools, on_text: TextSink | None = None):
        kw: dict[str, Any] = {"on_text": on_text} if on_text else {}
        if self._fast_may_handle(messages):
            quick = await self.fast.complete(system, messages, tools, **kw)
            if all(t.name in self.safe_tools for t in quick.tool_uses):
                return quick  # no tool, or only read-only ones: no need to wait for the big model
        return await self.main.complete(system, messages, tools, **kw)


class FakeLLMClient:
    """Returns predefined responses in sequence. Used by tests."""

    model = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.systems: list[str] = []

    async def complete(self, system, messages, tools, on_text: TextSink | None = None):
        self.calls.append(list(messages))
        self.systems.append(system)
        resp = self._responses.pop(0)
        if on_text is not None and resp.text:  # mimic streaming: a few words at a time
            words = resp.text.split(" ")
            for i in range(0, len(words), 3):
                tail = " " if i + 3 < len(words) else ""
                await on_text(" ".join(words[i : i + 3]) + tail)
            await on_text("\n")  # same end-of-reply marker as the real client
        return resp
