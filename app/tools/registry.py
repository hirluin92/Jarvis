import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

Permission = Literal["read", "write", "confirm"]


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: Permission
    func: Callable[[Any], Awaitable[Any]]
    # human-readable summary of what the call will do, shown in the confirmation message
    describe: Callable[[Any], str | Awaitable[str]] | None = None


class ToolInputError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def without(self, names: frozenset[str] | set[str]) -> "ToolRegistry":
        """A registry with the same tools except `names` (a smaller list is a smaller prompt)."""
        other = ToolRegistry()
        for t in self._tools.values():
            if t.name not in names:
                other.register(t)
        return other

    def names_with(self, permission: Permission) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values() if t.permission == permission)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_model.model_json_schema(),
            }
            for t in self._tools.values()
        ]

    def validate(self, name: str, raw: dict[str, Any]) -> BaseModel:
        tool = self._tools[name]
        try:
            return tool.input_model.model_validate(raw)
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            raise ToolInputError(f"Invalid input for {name}: {msgs}") from e

    async def describe(self, name: str, raw: dict[str, Any]) -> str:
        """What a call will do, for the confirmation message; generic if the tool has no text.

        A tool's `describe` may be sync or async (it may need to read the database).
        """
        tool = self._tools[name]
        if tool.describe is not None:
            try:
                text = tool.describe(self.validate(name, raw))
                return await text if inspect.isawaitable(text) else text
            except Exception:  # noqa: BLE001 - a broken summary must not block the confirmation
                pass
        args = ", ".join(f"{k}={v!r}" for k, v in raw.items())
        return f"{name}({args})"

    async def execute(self, name: str, raw: dict[str, Any]) -> Any:
        return await self._tools[name].func(self.validate(name, raw))
