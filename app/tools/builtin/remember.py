from typing import Literal

from pydantic import BaseModel, Field

from app import memory
from app.runtime import current_run_id
from app.tools.registry import Tool


class RememberInput(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    category: Literal["preference", "fact", "project", "person", "other"] = "fact"
    replaces: int | None = None  # id of the saved fact this one corrects or updates


async def remember(inp: RememberInput) -> dict[str, object]:
    store = memory.store()
    saved = await store.remember(inp.text, inp.category, current_run_id.get(), inp.replaces)
    check = await store.get(saved.id)  # read back: the proof is what is really stored
    if check is None or check.status != "active":
        raise RuntimeError(f"memory #{saved.id} not found after saving")
    return {
        "id": check.id,
        "category": check.category,
        "text": check.text,
        "replaced": inp.replaces,
    }


TOOL = Tool(
    "remember",
    "Save ONE short verified fact or preference about the user for future conversations. Use "
    "'replaces' with the id from recall when the user corrects a saved fact. Never save secrets "
    "(passwords, keys, account numbers): they are refused.",
    RememberInput,
    "write",
    remember,
)
