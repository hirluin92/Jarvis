from pydantic import BaseModel

from app import reminders
from app.tools.registry import Tool


class CancelInput(BaseModel):
    reminder_id: int


async def cancel_reminder(inp: CancelInput) -> dict[str, object]:
    done = await reminders.cancel(inp.reminder_id)
    if done is None:
        raise ValueError(f"no pending reminder with id {inp.reminder_id}")
    return {"id": done.id, "text": done.text, "status": done.status}


TOOL = Tool(
    "cancel_reminder",
    "Cancel a pending reminder by id (use list_reminders to find it).",
    CancelInput,
    "write",
    cancel_reminder,
)
