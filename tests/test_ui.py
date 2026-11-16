import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ui
from app.events import EventBus
from app.report import REPORT_PROMPT


def run(id, msg, status="completed", answer=None):
    return SimpleNamespace(
        id=id, user_message=msg, status=status, final_answer=answer, started_at=datetime.now(UTC)
    )


class FakeTracer:
    def __init__(self, runs):
        self.runs = runs

    async def list_runs(self, limit):
        return self.runs


def client(runs, realtime=False) -> TestClient:
    app = FastAPI()
    app.include_router(ui.router)
    app.state.tracer = FakeTracer(runs)
    app.state.events = EventBus()
    if realtime:
        app.state.realtime = object()
    return TestClient(app)


def test_page_is_served():
    r = client([]).get("/jarvis")
    assert r.status_code == 200 and "JARVIS" in r.text and "EventSource" in r.text


def test_panels_data(monkeypatch):
    async def sites():
        return [{"product": "Jarvis", "url": "http://x", "ok": True, "ms": 12, "cert_days": None}]

    monkeypatch.setattr(ui, "sites_panel", sites)
    runs = [
        run(5, "Usa echo_confirm_demo", "awaiting_confirmation"),
        run(4, REPORT_PROMPT, answer="Tutto ok"),
        run(3, "[summary]"),
        run(2, "Ciao", answer="Ciao!"),
    ]
    d = client(runs, realtime=True).get("/jarvis/data").json()
    assert d["sites"][0]["ok"] is True
    assert d["report"]["answer"] == "Tutto ok"
    assert [w["id"] for w in d["waiting"]] == [5]
    assert [r["id"] for r in d["recent"]] == [5, 2]  # no summaries, no morning report
    assert d["realtime"] is True


async def test_event_bus_fans_out_and_keeps_recent():
    bus = EventBus(history=2)
    a, b = bus.listen(), bus.listen()
    bus.publish("heard", text="ciao")
    bus.publish("thinking")
    bus.publish("done")
    assert (await a.get())["type"] == "heard" and (await b.get())["text"] == "ciao"
    assert [e["type"] for e in bus.recent] == ["thinking", "done"]
    bus.unlisten(a)
    bus.publish("say", text="x")
    assert a.qsize() == 2 and b.qsize() == 3  # a stopped listening: it got no "say"


async def test_slow_listener_loses_old_events_instead_of_blocking():
    bus = EventBus()
    q = bus.listen()
    for i in range(150):
        bus.publish("say", text=str(i))
    assert q.qsize() == 100
    first = await asyncio.wait_for(q.get(), 1)
    assert first["text"] == "50"
