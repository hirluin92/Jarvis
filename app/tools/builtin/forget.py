from pydantic import BaseModel

from app import memory
from app.tools.registry import Tool


class ForgetInput(BaseModel):
    id: int


async def describe(inp: ForgetInput) -> str:
    m = await memory.store().get(inp.id)
    if m is None or m.status != "active":
        return f"dimenticare il fatto #{inp.id} (non risulta tra i fatti salvati)"
    return f"dimenticare il fatto #{m.id} [{m.category}]: «{m.text}»"


async def forget(inp: ForgetInput) -> dict[str, object]:
    store = memory.store()
    if not await store.forget(inp.id):
        raise ValueError(f"memory #{inp.id} not found or already forgotten")
    check = await store.get(inp.id)  # read back
    if check is None or check.status != "forgotten":
        raise RuntimeError(f"memory #{inp.id} is still active")
    return {"id": inp.id, "status": check.status}


TOOL = Tool(
    "forget",
    "Forget a saved fact by id (get ids from recall). Needs user confirmation.",
    ForgetInput,
    "confirm",
    forget,
    describe,
)
