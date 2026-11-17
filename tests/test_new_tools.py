from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import reminders
from app.catalog import Catalog, Product
from app.tools import github_write
from app.tools.builtin import (
    cancel_reminder,
    list_reminders,
    read_webpage,
    set_reminder,
    trigger_deploy,
    web_search,
)

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


# ---------- reminders ----------


def test_parse_when_accepts_local_time_and_minutes():
    due = reminders.parse_when("2026-11-18T09:00:00", None, ROME, NOW)
    assert due == datetime(2026, 11, 18, 8, 0, tzinfo=UTC)  # Rome is UTC+1 in November
    assert reminders.parse_when(None, 30, ROME, NOW) == NOW + timedelta(minutes=30)


@pytest.mark.parametrize(
    "when,minutes,message",
    [
        (None, None, "exactly one"),
        ("2026-11-18T09:00:00", 5, "exactly one"),
        ("domani mattina", None, "ISO 8601"),
        ("2026-11-17T08:00:00", None, "past"),
        ("2028-01-01T08:00:00", None, "more than a year"),
    ],
)
def test_parse_when_refuses_bad_times(when, minutes, message):
    with pytest.raises(ValueError, match=message):
        reminders.parse_when(when, minutes, ROME, NOW)


async def test_reminder_tools_store_list_and_cancel(tracer):
    out = await set_reminder.set_reminder(
        set_reminder.ReminderInput(text="chiamare Mario", in_minutes=90)
    )
    assert out["status"] == "pending" and out["id"]
    listed = await list_reminders.list_reminders(list_reminders.NoInput())
    assert [r["text"] for r in listed["reminders"]] == ["chiamare Mario"]
    done = await cancel_reminder.cancel_reminder(cancel_reminder.CancelInput(reminder_id=out["id"]))
    assert done["status"] == "cancelled"
    assert (await list_reminders.list_reminders(list_reminders.NoInput()))["reminders"] == []
    with pytest.raises(ValueError, match="no pending reminder"):
        await cancel_reminder.cancel_reminder(cancel_reminder.CancelInput(reminder_id=out["id"]))


async def test_due_reminders_are_sent_once_and_retried_if_delivery_fails(tracer):
    await reminders.create("scaduto", NOW - timedelta(minutes=1))
    await reminders.create("futuro", NOW + timedelta(hours=1))
    sent: list[str] = []

    async def failing(text: str) -> None:
        raise RuntimeError("telegram down")

    async def working(text: str) -> None:
        sent.append(text)

    assert await reminders.deliver_due(failing, NOW) == 0  # not marked as sent
    assert await reminders.deliver_due(working, NOW) == 1
    assert await reminders.deliver_due(working, NOW) == 0  # never twice
    assert sent == ["⏰ Promemoria: scaduto"]
    assert [r.text for r in await reminders.pending()] == ["futuro"]


# ---------- web ----------


async def test_web_search_needs_a_key_and_returns_short_untrusted_results(monkeypatch):
    monkeypatch.setattr(web_search, "get_settings", lambda: SimpleNamespace(tavily_api_key=""))
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await web_search.web_search(web_search.SearchInput(query="meteo roma"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        item = {"title": "Meteo", "url": "https://x.example/m", "content": "sole " * 200}
        return httpx.Response(200, json={"results": [item]})

    monkeypatch.setattr(web_search, "get_settings", lambda: SimpleNamespace(tavily_api_key="k"))
    monkeypatch.setattr(
        web_search, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    out = await web_search.web_search(web_search.SearchInput(query="meteo roma"))
    assert out["results"][0]["url"] == "https://x.example/m"
    assert (
        len(out["results"][0]["snippet"]) == web_search.MAX_SNIPPET and "untrusted" in out["note"]
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://localhost/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
async def test_read_webpage_refuses_private_and_non_web_addresses(url):
    with pytest.raises(ValueError):
        await read_webpage.ensure_public(url)


async def test_read_webpage_extracts_text_and_checks_every_redirect(monkeypatch):
    checked: list[str] = []

    async def fake_public(url: str) -> None:
        checked.append(url)

    monkeypatch.setattr(read_webpage, "ensure_public", fake_public)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        page = (
            "<html><head><title>Ricetta</title><style>x{}</style></head><body><nav>menu</nav>"
            "<script>evil()</script><p>Guanciale, uova</p><p>pecorino</p></body></html>"
        )
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    monkeypatch.setattr(
        read_webpage,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    out = await read_webpage.read_webpage(read_webpage.PageInput(url="https://x.example/old"))
    assert out["title"] == "Ricetta" and out["text"] == "Guanciale, uova pecorino"
    assert checked == ["https://x.example/old", "https://x.example/new"]


async def test_read_webpage_refuses_binary_content(monkeypatch):
    async def fake_public(url: str) -> None: ...

    monkeypatch.setattr(read_webpage, "ensure_public", fake_public)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    monkeypatch.setattr(
        read_webpage, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError, match="not a text page"):
        await read_webpage.read_webpage(read_webpage.PageInput(url="https://x.example/f.pdf"))


# ---------- deploy ----------


def _catalog(monkeypatch, **extra):
    products = [Product(name="App", github_repo="me/app", **extra), Product(name="Bare")]
    monkeypatch.setattr(trigger_deploy, "get_catalog", lambda: Catalog(products=products))


async def test_trigger_deploy_dispatches_only_the_catalog_workflow(monkeypatch):
    _catalog(monkeypatch, deploy_workflow="deploy.yml")
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content and __import__("json").loads(request.content)
        seen.append((request.method, request.url.path, body))
        if request.method == "POST":
            return httpx.Response(204)
        run = {"html_url": "https://github.com/me/app/actions/runs/1", "status": "queued"}
        return httpx.Response(200, json={"workflow_runs": [run]})

    monkeypatch.setattr(
        github_write,
        "client",
        lambda: httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        ),
    )
    out = await trigger_deploy.trigger_deploy(trigger_deploy.DeployInput(product="App"))
    assert seen[0] == (
        "POST",
        "/repos/me/app/actions/workflows/deploy.yml/dispatches",
        {"ref": "main"},
    )
    assert out["dispatched"] and out["latest_run_status"] == "queued"
    assert "deploy.yml" in trigger_deploy.describe(trigger_deploy.DeployInput(product="App"))


async def test_trigger_deploy_refuses_products_without_a_deploy_workflow(monkeypatch):
    _catalog(monkeypatch)
    with pytest.raises(ValueError, match="deploy_workflow"):
        await trigger_deploy.trigger_deploy(trigger_deploy.DeployInput(product="App"))
    with pytest.raises(ValueError, match="Unknown product"):
        await trigger_deploy.trigger_deploy(trigger_deploy.DeployInput(product="Altro"))


def test_trigger_deploy_is_a_confirm_tool_and_web_tools_are_read_only():
    assert trigger_deploy.TOOL.permission == "confirm"
    assert web_search.TOOL.permission == read_webpage.TOOL.permission == "read"
    assert set_reminder.TOOL.permission == cancel_reminder.TOOL.permission == "write"
