from datetime import date

from pydantic import BaseModel, Field

from app.db import session as db
from app.db.models import Todo
from app.tools.registry import Tool


class TodoInput(BaseModel):
    text: str = Field(min_length=1)
    due: date | None = None  # ISO date, YYYY-MM-DD


async def add_todo(inp: TodoInput) -> dict[str, object]:
    async with db.session_maker()() as s:
        todo = Todo(text=inp.text, due=inp.due)
        s.add(todo)
        await s.commit()
        todo_id = todo.id
    async with db.session_maker()() as s:  # read back: the proof is what is really stored
        saved = await s.get(Todo, todo_id)
    if saved is None:
        raise RuntimeError(f"todo {todo_id} not found after saving")
    return {"id": saved.id, "text": saved.text, "due": saved.due.isoformat() if saved.due else None}


TOOL = Tool(
    "add_todo",
    "Add a to-do item, with an optional due date (YYYY-MM-DD). Reversible.",
    TodoInput,
    "write",
    add_todo,
)
