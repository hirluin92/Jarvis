from dataclasses import dataclass, field
from typing import Any, Protocol


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

    async def complete(self, system, messages, tools) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        r = await self._client.messages.create(
            model=self.model, max_tokens=2048, system=system, messages=messages, **kwargs
        )
        out = LLMResponse(input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
        for b in r.content:
            if b.type == "text":
                out.text += b.text
            elif b.type == "tool_use":
                out.tool_uses.append(ToolUse(b.id, b.name, dict(b.input)))
        return out


class RoutedLLM:
    """Latency routing: a small fast model answers first; the main model takes over when a tool
    is needed or after tool results (reasoning). Simple questions never wait for the big model.

    `model` is the main model's, which is what the run is billed against in the trace; the
    fast attempt that escalates is discarded and its (small) tokens are not counted.
    """

    def __init__(self, fast: LLMClient, main: LLMClient) -> None:
        self.fast, self.main = fast, main
        self.model = main.model

    async def complete(self, system, messages, tools) -> LLMResponse:
        first_step = bool(messages) and messages[-1]["role"] == "user"
        first_step = first_step and isinstance(messages[-1]["content"], str)
        if first_step:
            quick = await self.fast.complete(system, messages, tools)
            if not quick.tool_uses:
                return quick
        return await self.main.complete(system, messages, tools)


class FakeLLMClient:
    """Returns predefined responses in sequence. Used by tests."""

    model = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.systems: list[str] = []

    async def complete(self, system, messages, tools) -> LLMResponse:
        self.calls.append(list(messages))
        self.systems.append(system)
        return self._responses.pop(0)
