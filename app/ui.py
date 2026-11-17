"""The live screen (/jarvis): page, event stream and the data of its panels."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app import builds
from app.catalog import get_catalog
from app.report import REPORT_PROMPT
from app.tools.builtin.site_status import SiteStatusInput, site_status

router = APIRouter()
PAGE = Path(__file__).parent / "ui" / "jarvis.html"
SITES_TTL = 60.0  # seconds: the panel polls often, the sites do not need checking that often
_sites: dict = {"at": 0.0, "data": []}


async def sites_panel() -> list[dict]:
    """One row per URL: product, url, up/down, milliseconds. Cached for a minute."""
    if time.monotonic() - _sites["at"] < SITES_TTL and _sites["at"]:
        return _sites["data"]
    try:
        if not get_catalog().products:
            return []
        raw = await site_status(SiteStatusInput())
    except Exception:
        return _sites["data"]
    rows = []
    for product in raw["products"]:
        for u in product["urls"]:
            rows.append(
                {
                    "product": product["product"],
                    "url": u.get("url", ""),
                    "ok": bool(u.get("ok")),
                    "ms": u.get("ms"),
                    "cert_days": u.get("cert_days_left"),
                }
            )
    _sites.update(at=time.monotonic(), data=rows)
    return rows


def _run_row(r) -> dict:
    return {
        "id": r.id,
        "at": r.started_at.isoformat(),
        "status": r.status,
        "message": r.user_message[:120],
        "answer": (r.final_answer or "")[:300],
    }


@router.get("/jarvis", response_class=HTMLResponse)
async def screen() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


SERVICE_WORKER = """// Live data: nothing is cached, the worker only makes the page installable.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""


@router.get("/jarvis-sw.js")
async def service_worker() -> Response:
    return Response(
        SERVICE_WORKER,
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/jarvis", "Cache-Control": "no-cache"},
    )


@router.get("/jarvis/data")
async def data(request: Request) -> dict:
    tracer = request.app.state.tracer
    runs = await tracer.list_runs(40)
    now = datetime.now(UTC)
    report = next((r for r in runs if r.user_message == REPORT_PROMPT and r.final_answer), None)
    waiting = [
        _run_row(r)
        for r in runs
        if r.status == "awaiting_confirmation" and now - r.started_at < timedelta(minutes=10)
    ]
    real = [
        r for r in runs if not r.user_message.startswith("[") and r.user_message != REPORT_PROMPT
    ]
    try:
        jobs = await builds.list_jobs(6)
    except Exception:  # the panel must work even if the build tables are not there yet
        jobs = []
    return {
        "builds": [
            {
                "id": j.id,
                "product": j.product,
                "status": j.status,
                "pr_url": j.pr_url,
                "reason": j.stop_reason,
            }
            for j in jobs
        ],
        "sites": await sites_panel(),
        "report": _run_row(report) if report else None,
        "waiting": waiting,
        "recent": [_run_row(r) for r in real[:8]],
        "realtime": getattr(request.app.state, "realtime", None) is not None,
    }


@router.get("/jarvis/events")
async def events(request: Request) -> StreamingResponse:
    bus = request.app.state.events
    q = bus.listen()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            bus.unlisten(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
