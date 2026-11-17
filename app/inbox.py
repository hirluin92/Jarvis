"""Reading the dedicated mailbox label (phase 13).

Jarvis reads ONLY the messages that carry the label named in INBOX_LABEL. The Gmail permission that
makes reading possible (`gmail.readonly`) covers the whole mailbox, so this restriction is in the
code: a message without the label is never fetched in full and never stored.
"""

import base64
import html
import re
from typing import Any

from app.config import get_settings
from app.tools import gmail

MAX_BODY = 4000


class InboxError(RuntimeError):
    pass


def _header(payload: dict[str, Any], name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def body_text(payload: dict[str, Any]) -> str:
    """The readable text of a message: the plain part, or the HTML part without tags."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    text = "\n".join(plain) or re.sub(r"<[^>]+>", " ", html.unescape("\n".join(rich)))
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()[:MAX_BODY]


async def fetch_new(known_ids: set[str], limit: int = 20, label: str | None = None) -> list[dict]:
    """New messages of the dedicated label: [{gmail_id, thread_id, from_addr, subject, body}]."""
    label = label or get_settings().inbox_label
    token = await gmail.access_token()
    async with gmail.client(token) as c:
        labels = await c.get("/labels")
        if labels.status_code == 403:
            raise InboxError(
                "manca il permesso di lettura di Gmail: rilancia `uv run python -m app.auth_gmail`"
            )
        labels.raise_for_status()
        wanted = next(
            (
                x["id"]
                for x in labels.json().get("labels", [])
                if x["name"].lower() == label.lower()
            ),
            None,
        )
        if wanted is None:
            raise InboxError(
                f"l'etichetta Gmail «{label}» non esiste: creala e assegnala ai messaggi"
            )
        listing = await c.get(
            "/messages", params={"labelIds": wanted, "maxResults": limit, "q": "newer_than:30d"}
        )
        listing.raise_for_status()
        out: list[dict] = []
        for ref in listing.json().get("messages", []):
            if ref["id"] in known_ids:
                continue
            msg = await c.get(f"/messages/{ref['id']}", params={"format": "full"})
            msg.raise_for_status()
            data = msg.json()
            if wanted not in data.get("labelIds", []):  # never trust the listing alone
                continue
            payload = data.get("payload", {})
            out.append(
                {
                    "gmail_id": data["id"],
                    "thread_id": data.get("threadId", ""),
                    "from_addr": _header(payload, "From"),
                    "subject": _header(payload, "Subject"),
                    "body": body_text(payload),
                }
            )
    return out
