from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app import reminders
from app.config import get_settings
from app.tools.registry import Tool


class ReminderInput(BaseModel):
    text: str = Field(min_length=1, max_length=300, description="what to remind, as a sentence")
    when: str | None = Field(
        None,
        description="ISO 8601 date and time, e.g. 2026-11-18T09:00:00 (Rome time unless it has an "
        "offset). Call get_current_time first to work out 'tomorrow', 'on Friday', and so on.",
    )
    in_minutes: int | None = Field(None, ge=1, le=525600, description="minutes from now")


async def set_reminder(inp: ReminderInput) -> dict[str, object]:
    from datetime import UTC, datetime

    tz = ZoneInfo(get_settings().report_timezone)
    due = reminders.parse_when(inp.when, inp.in_minutes, tz, datetime.now(UTC))
    saved = await reminders.create(inp.text, due)
    return {
        "id": saved.id,
        "text": saved.text,
        "due_local": saved.due_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "status": saved.status,
    }


TOOL = Tool(
    "set_reminder",
    "Set a reminder: Jarvis will send it on Telegram at that time. Give either 'when' (ISO "
    "date-time) or 'in_minutes'. Reversible with cancel_reminder.",
    ReminderInput,
    "write",
    set_reminder,
)
