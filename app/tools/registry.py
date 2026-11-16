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


class ToolInputError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

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

    async def execute(self, name: str, raw: dict[str, Any]) -> Any:
        return await self._tools[name].func(self.validate(name, raw))
