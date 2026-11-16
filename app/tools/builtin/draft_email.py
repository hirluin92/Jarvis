from app.tools import gmail
from app.tools.gmail import MailInput
from app.tools.registry import Tool


async def draft_email(inp: MailInput) -> dict[str, object]:
    token = await gmail.access_token()
    async with gmail.client(token) as c:
        created = await c.post("/drafts", json={"message": {"raw": gmail.raw_message(inp)}})
        created.raise_for_status()
        draft_id = created.json()["id"]
        check = await c.get(f"/drafts/{draft_id}")  # read back what Gmail really stored
        check.raise_for_status()
        headers = {
            h["name"].lower(): h["value"] for h in check.json()["message"]["payload"]["headers"]
        }
    return {
        "draft_id": draft_id,
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "where": "Gmail, cartella Bozze (non inviata)",
    }


TOOL = Tool(
    "draft_email",
    "Create a Gmail DRAFT (never sends). Takes one recipient, subject and body. Reversible: the "
    "draft can be deleted.",
    MailInput,
    "write",
    draft_email,
)
