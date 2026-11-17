from pydantic import BaseModel, Field

from app import growth
from app.tools.gmail import send_mail
from app.tools.registry import Tool

SHOWN = 500


class SendInput(BaseModel):
    queue_ids: list[int] = Field(min_length=1, max_length=10)


async def describe(inp: SendInput) -> str:
    lines = ["invio di questi messaggi dalla tua casella:"]
    for qid in inp.queue_ids:
        item = await growth.get_item(qid)
        lead = await growth.get_lead(item.lead_id)
        lines.append(f"\n#{qid} a {lead.contact} · {item.subject}\n{item.body[:SHOWN]}")
    return "\n".join(lines)


async def send_approved_outreach(inp: SendInput) -> dict[str, object]:
    results = []
    for qid in inp.queue_ids:
        try:
            results.append(await growth.send_item(qid, "human", send_mail))
        except (growth.Blocked, ValueError) as e:
            results.append({"item_id": qid, "not_sent": str(e)})
    return {"results": results}


TOOL = Tool(
    "send_approved_outreach",
    "Send queued messages from the user's mailbox. Needs the user's confirmation, which shows "
    "every message. Each one still passes all checks (stop switch, exclusion list, hours, daily "
    "cap): a blocked one is reported, not sent.",
    SendInput,
    "confirm",
    send_approved_outreach,
    describe,
)
