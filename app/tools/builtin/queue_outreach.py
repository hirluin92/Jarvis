from typing import Literal

from pydantic import BaseModel, Field

from app import growth
from app.tools.registry import Tool


class QueueInput(BaseModel):
    lead_id: int
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(
        min_length=20, max_length=2000, description="honest, short, useful, no tricks"
    )
    kind: Literal["cold", "followup", "reply"] = "cold"
    thread_id: str | None = None


async def queue_outreach(inp: QueueInput) -> dict[str, object]:
    item = await growth.queue(inp.lead_id, inp.subject, inp.body, inp.kind, inp.thread_id)
    return {
        "queue_id": item.id, "status": item.status,
        "note": "Nothing was sent: it waits for the user's click on Telegram.",
    }  # fmt: skip


TOOL = Tool(
    "queue_outreach",
    "Put a message in the approval queue. It does NOT send anything: each message waits for "
    "the user's click. Refused for private addresses (cold), excluded contacts, second cold "
    "contacts, and follow-ups that are too early or too many. The signature, the AI notice and "
    "the way to stop are added automatically.",
    QueueInput,
    "write",
    queue_outreach,
)
