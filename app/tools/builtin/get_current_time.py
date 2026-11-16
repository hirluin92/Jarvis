from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.tools.registry import Tool


class TimeInput(BaseModel):
    timezone: str = "Europe/Rome"


async def get_current_time(inp: TimeInput) -> dict[str, str]:
    now = datetime.now(ZoneInfo(inp.timezone))
    return {
        "timezone": inp.timezone,
        "iso": now.isoformat(),
        "human": now.strftime("%A %d %B %Y, %H:%M"),
    }


TOOL = Tool(
    "get_current_time",
    "Current date and time in an IANA timezone (default Europe/Rome).",
    TimeInput,
    "read",
    get_current_time,
)
