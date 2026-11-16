from app.tools import gmail
from app.tools.gmail import MailInput
from app.tools.registry import Tool

SHOWN_BODY_CHARS = 1500


def describe(inp: MailInput) -> str:
    shown = inp.body[:SHOWN_BODY_CHARS]
    more = (
        "\n[...testo troncato nel messaggio, verrà inviato per intero]" if shown != inp.body else ""
    )
    return f"inviare un'email a {inp.to}\nOggetto: {inp.subject}\n\n{shown}{more}"


async def send_email(inp: MailInput) -> dict[str, object]:
    token = await gmail.access_token()
    async with gmail.client(token) as c:
        sent = await c.post("/messages/send", json={"raw": gmail.raw_message(inp)})
        sent.raise_for_status()
    data = sent.json()
    # the send response is the proof: Gmail must report the message as SENT
    if "SENT" not in data.get("labelIds", []):
        raise RuntimeError("Gmail accepted the message but it is not labelled SENT")
    return {
        "message_id": data["id"],
        "thread_id": data["threadId"],
        "to": inp.to,
        "subject": inp.subject,
        "labels": data["labelIds"],
    }


TOOL = Tool(
    "send_email",
    "Send an email through Gmail to ONE recipient. Irreversible: needs user confirmation.",
    MailInput,
    "confirm",
    send_email,
    describe,
)
