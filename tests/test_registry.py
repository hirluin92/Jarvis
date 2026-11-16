import pytest
from pydantic import BaseModel

from app.tools.registry import Tool, ToolInputError, ToolRegistry


class In(BaseModel):
    n: int


async def double(i: In) -> int:
    return i.n * 2


def make() -> ToolRegistry:
    r = ToolRegistry()
    r.register(Tool("double", "doubles", In, "read", double))
    return r


def test_schema_generation():
    d = make().definitions()[0]
    assert d["name"] == "double" and d["input_schema"]["properties"]["n"]["type"] == "integer"


def test_duplicate_rejected():
    r = make()
    with pytest.raises(ValueError):
        r.register(Tool("double", "x", In, "read", double))


async def test_execute_valid():
    assert await make().execute("double", {"n": 4}) == 8


async def test_invalid_input_rejected():
    with pytest.raises(ToolInputError, match="n"):
        await make().execute("double", {"n": "abc"})
