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
    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(self, system, messages, tools) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
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
