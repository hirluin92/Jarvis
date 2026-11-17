from pydantic import BaseModel, Field

from app import notify
from app.tools.registry import Tool


class ModeInput(BaseModel):
    product: str = Field(description="product name to advise on, or 'off' to leave the mode")


async def set_advisor_mode(inp: ModeInput) -> dict[str, object]:
    if notify.advisor_switch is None:
        raise RuntimeError("advisor mode is not available")
    off = inp.product.strip().lower() in ("off", "no", "fine", "esci")
    msg = await notify.advisor_switch(None if off else inp.product)
    return {"mode": "off" if off else inp.product, "message": msg}


TOOL = Tool(
    "set_advisor_mode",
    "Switch the user's chat to advisor mode on a product (a partner who knows the documents, the "
    "numbers and the decisions) or back to normal ('off'). Takes effect from the next message.",
    ModeInput,
    "write",
    set_advisor_mode,
)
