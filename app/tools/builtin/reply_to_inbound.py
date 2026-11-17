from pydantic import BaseModel, Field

from app import growth
from app.tools.gmail import send_mail
from app.tools.registry import Tool


class ReplyInput(BaseModel):
    message_id: int
    body: str = Field("", max_length=2000, description="omit to use the proposed reply")


async def describe(inp: ReplyInput) -> str:
    msg = await growth.inbound_by_id(inp.message_id)
    text = (inp.body or msg.proposed_reply).strip()
    if len(text) < 20:
        raise ValueError("manca il testo della risposta (almeno 20 caratteri)")
    return f"risposta a {msg.from_addr} (oggetto: Re: {msg.subject})\n\n{text[:700]}"


async def reply_to_inbound(inp: ReplyInput) -> dict[str, object]:
    return await growth.reply_to_message(inp.message_id, inp.body, send_mail)


TOOL = Tool(
    "reply_to_inbound",
    "Reply to a message received in the dedicated mailbox, from the user's mailbox. Needs the "
    "user's confirmation, which shows the text. Not allowed for stop requests or bounces.",
    ReplyInput,
    "confirm",
    reply_to_inbound,
    describe,
)
