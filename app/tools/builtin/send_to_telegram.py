from pydantic import BaseModel, Field

from app import notify
from app.tools.registry import Tool


class TelegramInput(BaseModel):
    text: str = Field(min_length=1, max_length=3800)


async def send_to_telegram(inp: TelegramInput) -> dict[str, object]:
    if notify.sender is None:
        raise RuntimeError("Telegram is not available")
    await notify.sender(inp.text)
    return {"sent": True, "chars": len(inp.text)}


TOOL = Tool(
    "send_to_telegram",
    "Send a text to the user on Telegram. Use it for long answers when the user is talking: say "
    "a short version aloud and send the details here.",
    TelegramInput,
    "write",
    send_to_telegram,
)
