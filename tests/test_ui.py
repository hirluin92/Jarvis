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


def test_3d_orb_files_are_vendored_and_wired_into_the_page():
    from pathlib import Path

    ui_dir = Path(ui.__file__).parent / "ui"
    page = (ui_dir / "jarvis.html").read_text(encoding="utf-8")
    assert '"three":"/jarvis/ui/vendor/three.module.js"' in page
    assert "/jarvis/ui/orb3d.js" in page and 'id="gl"' in page
    orb = (ui_dir / "orb3d.js").read_text(encoding="utf-8")
    for line in orb.splitlines():
        if line.startswith("import ") and "./vendor/" in line:
            rel = line.split('"')[1]
            assert (ui_dir / rel).is_file(), rel  # no CDN: everything is served by Jarvis
    assert (ui_dir / "vendor" / "three.module.js").is_file()
    assert (ui_dir / "vendor" / "LICENSE-three.txt").is_file()


def test_installable_app_files():
    import json
    from pathlib import Path

    ui_dir = Path(ui.__file__).parent / "ui"
    manifest = json.loads((ui_dir / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/jarvis"
    for icon in manifest["icons"]:
        assert (ui_dir / icon["src"].removeprefix("/jarvis/ui/")).is_file(), icon["src"]
    assert (ui_dir / "icons" / "apple-touch-icon.png").is_file()
    sw = client([]).get("/jarvis-sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/jarvis"
