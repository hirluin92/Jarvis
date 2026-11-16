from typing import Literal

from pydantic import BaseModel, Field

from app import memory
from app.tools.registry import Tool


class RecallInput(BaseModel):
    query: str = Field(min_length=1)
    category: Literal["preference", "fact", "project", "person", "other"] | None = None
    limit: int = Field(default=5, ge=1, le=10)


async def recall(inp: RecallInput) -> dict[str, object]:
    found = await memory.store().search(inp.query, inp.category, inp.limit)
    return {
        "results": [{"id": m.id, "category": m.category, "text": m.text} for m in found],
        "note": "" if found else "no saved fact matches this query",
    }


TOOL = Tool(
    "recall",
    "Search the saved facts about the user by words in the query, optionally by category. One "
    "search is enough: if nothing matches, say you have no saved fact about it instead of "
    "retrying with variations.",
    RecallInput,
    "read",
    recall,
)
