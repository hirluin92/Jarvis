from pydantic import BaseModel

from app import growth
from app.tools.registry import Tool


class ForgetInput(BaseModel):
    lead_id: int


async def forget_lead(inp: ForgetInput) -> dict[str, object]:
    contact = await growth.forget_lead(inp.lead_id)
    return {"forgotten": True, "never_contacted_again": contact}


TOOL = Tool(
    "forget_lead",
    "Delete a contact and its queued messages, and put the address on the never-contact list "
    "(the right to be forgotten). Use it when someone asks to be removed.",
    ForgetInput,
    "write",
    forget_lead,
)
