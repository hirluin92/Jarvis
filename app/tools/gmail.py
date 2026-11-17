import base64
import re
from email.message import EmailMessage

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
ONE_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class MailInput(BaseModel):
    to: str
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)

    @field_validator("to")
    @classmethod
    def _single_address(cls, v: str) -> str:
        if not ONE_ADDRESS.match(v):
            raise ValueError("must be exactly one plain email address")
        return v

    @field_validator("subject")
    @classmethod
    def _no_newlines(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("must be a single line")
        return v


async def access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret and s.google_refresh_token):
        raise RuntimeError(
            "Gmail is not configured: set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
            "GOOGLE_REFRESH_TOKEN (run: uv run python -m app.auth_gmail)"
        )
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            TOKEN_URL,
            data={
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "refresh_token": s.google_refresh_token,
                "grant_type": "refresh_token",
            },
        )
    r.raise_for_status()
    return r.json()["access_token"]


def client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API, timeout=20, headers={"Authorization": f"Bearer {token}"})


def raw_message(mail: MailInput) -> str:
    msg = EmailMessage()
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg.set_content(mail.body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def send_mail(
    to: str, subject: str, body: str, thread_id: str | None = None
) -> dict[str, object]:
    """Send one message from the user's own mailbox (inside a thread when `thread_id` is given).
    Gmail must answer that it is SENT: that is the proof."""
    mail = MailInput(to=to, subject=subject, body=body)
    payload: dict[str, object] = {"raw": raw_message(mail)}
    if thread_id:
        payload["threadId"] = thread_id
    token = await access_token()
    async with client(token) as c:
        sent = await c.post("/messages/send", json=payload)
        sent.raise_for_status()
    data = sent.json()
    if "SENT" not in data.get("labelIds", []):
        raise RuntimeError("Gmail accepted the message but it is not labelled SENT")
    return data
