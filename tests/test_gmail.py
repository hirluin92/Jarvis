import base64
import email
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.auth_gmail import pkce_pair, set_env_value
from app.tools import gmail
from app.tools.builtin import draft_email, send_email
from app.tools.gmail import MailInput
from app.tools.registry import ToolRegistry

MAIL = MailInput(to="anna@example.com", subject="Ciao è", body="Ci vediamo domani.")


def _decoded(raw: str):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


@pytest.fixture
def api(monkeypatch):
    seen: list[httpx.Request] = []
    state = {"labels": ["SENT"]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d1"})
        if path.endswith("/drafts/d1"):
            headers = [
                {"name": "To", "value": "anna@example.com"},
                {"name": "Subject", "value": "Ciao è"},
            ]
            return httpx.Response(200, json={"message": {"payload": {"headers": headers}}})
        if request.method == "POST" and path.endswith("/messages/send"):
            return httpx.Response(
                200, json={"id": "m1", "threadId": "t1", "labelIds": state["labels"]}
            )
        return httpx.Response(404)

    async def token() -> str:
        return "access"

    monkeypatch.setattr(gmail, "access_token", token)
    monkeypatch.setattr(
        gmail,
        "client",
        lambda t: httpx.AsyncClient(base_url=gmail.API, transport=httpx.MockTransport(handler)),
    )
    return SimpleNamespace(seen=seen, state=state)


async def test_draft_is_created_and_read_back(api):
    out = await draft_email.draft_email(MAIL)
    assert out["draft_id"] == "d1" and out["to"] == "anna@example.com"
    assert "non inviata" in out["where"]
    paths = [(r.method, r.url.path) for r in api.seen]
    assert all("/messages/send" not in p for _, p in paths)  # a draft never sends
    sent = _decoded(__import__("json").loads(api.seen[0].content)["message"]["raw"])
    assert sent["To"] == "anna@example.com" and "domani" in sent.get_payload(decode=True).decode()


async def test_send_returns_the_proof_from_gmail(api):
    out = await send_email.send_email(MAIL)
    assert (
        out["message_id"] == "m1" and out["labels"] == ["SENT"] and out["to"] == "anna@example.com"
    )


async def test_send_not_labelled_sent_is_an_error(api):
    api.state["labels"] = ["DRAFT"]
    with pytest.raises(RuntimeError, match="not labelled SENT"):
        await send_email.send_email(MAIL)


def test_send_is_confirm_level_and_draft_is_write():
    assert send_email.TOOL.permission == "confirm"
    assert draft_email.TOOL.permission == "write"


def test_confirmation_shows_recipient_subject_and_full_text():
    reg = ToolRegistry()
    reg.register(send_email.TOOL)
    text = reg.describe(
        "send_email", {"to": "anna@example.com", "subject": "Ciao", "body": "Ci vediamo domani."}
    )
    assert text == "inviare un'email a anna@example.com\nOggetto: Ciao\n\nCi vediamo domani."


def test_confirmation_flags_a_truncated_body():
    long = "x" * (send_email.SHOWN_BODY_CHARS + 50)
    text = send_email.describe(MailInput(to="a@b.co", subject="s", body=long))
    assert "troncato" in text and text.count("x") == send_email.SHOWN_BODY_CHARS


@pytest.mark.parametrize(
    "to",
    [
        "a@b.co, c@d.co",
        "a@b.co;c@d.co",
        "Anna <a@b.co>",
        "nessuna-chiocciola",
        "a@b.co\nBcc: x@y.zz",
    ],
)
def test_only_one_plain_address_is_accepted(to):
    with pytest.raises(ValidationError):
        MailInput(to=to, subject="s", body="b")


def test_subject_cannot_contain_a_newline():
    with pytest.raises(ValidationError):
        MailInput(to="a@b.co", subject="s\nBcc: x@y.zz", body="b")


async def test_missing_configuration_is_a_clear_error(monkeypatch):
    empty = SimpleNamespace(google_client_id="", google_client_secret="", google_refresh_token="")
    monkeypatch.setattr(gmail, "get_settings", lambda: empty)
    with pytest.raises(RuntimeError, match="auth_gmail"):
        await gmail.access_token()


def test_pkce_challenge_matches_the_verifier():
    import hashlib

    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode() and len(verifier) >= 43


def test_set_env_value_replaces_or_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nGOOGLE_REFRESH_TOKEN=old\nB=2\n", encoding="utf-8")
    set_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")
    set_env_value(env, "C", "3")
    assert env.read_text(encoding="utf-8") == "A=1\nGOOGLE_REFRESH_TOKEN=new\nB=2\nC=3\n"
