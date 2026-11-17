from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app import reminders
from app.config import get_settings
from app.tools.registry import Tool


class NoInput(BaseModel):
    pass


async def list_reminders(inp: NoInput) -> dict[str, object]:
    tz = ZoneInfo(get_settings().report_timezone)
    items = await reminders.pending()
    return {
        "reminders": [
            {
                "id": r.id,
                "text": r.text,
                "due_local": r.due_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            }
            for r in items
        ]
    }


TOOL = Tool(
    "list_reminders",
    "List the user's pending reminders (the agenda), earliest first, with id and local time.",
    NoInput,
    "read",
    list_reminders,
)
