from typing import Literal

from pydantic import BaseModel, Field

from app import growth
from app.tools.registry import Tool


class LeadInput(BaseModel):
    contact: str = Field(description="one email address")
    source_url: str = Field(description="the public page where the contact was found")
    legal_basis: str = Field(min_length=10, description="why this person may be contacted")
    consent: Literal["inbound", "business"] = Field(
        description="business = a company or professional address found on a public page (never "
        "a private free-mail address); inbound = a person who really wrote to the mailbox. "
        "Explicit opt-in can only be declared by the user, never by you"
    )
    name: str = ""
    opportunity_id: int | None = None


async def add_lead(inp: LeadInput) -> dict[str, object]:
    lead = await growth.add_lead(
        inp.contact, inp.source_url, inp.legal_basis, inp.consent, inp.name, inp.opportunity_id
    )
    return {"lead_id": lead.id, "contact": lead.contact, "consent": lead.consent}


TOOL = Tool(
    "add_lead",
    "Add a contact with its public source and its legal basis (both mandatory). Private "
    "free-mail addresses are refused unless the person opted in; addresses on the never-contact "
    "list are refused. Take contacts only from public pages, never from private profiles.",
    LeadInput,
    "write",
    add_lead,
)
