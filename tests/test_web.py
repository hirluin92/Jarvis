import httpx
import pytest
from fastapi import FastAPI

from app.web import router


@pytest.fixture
async def client(tracer):
    app = FastAPI()
    app.include_router(router)
    app.state.tracer = tracer
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _make_run(tracer) -> int:
    rid = await tracer.start_run("che ore <b>sono</b>?", "claude-sonnet-5-5")
    await tracer.add_step(rid, 0, "llm_call", duration_ms=12, input_tokens=100, output_tokens=20)
    await tracer.add_step(
        rid,
        1,
        "tool_call",
        tool_name="get_current_time",
        input={"timezone": "Asia/Tokyo"},
        output={"iso": "2026-11-16T17:17"},
        duration_ms=3,
    )
    await tracer.add_step(rid, 2, "tool_call", tool_name="http_health_check", error="boom")
    await tracer.finish_run(rid, "completed", "Sono le 17:17", 100, 20)
    return rid


async def test_list_page_shows_runs_with_cost(client, tracer):
    rid = await _make_run(tracer)
    r = await client.get("/ui/runs")
    assert r.status_code == 200
    assert f'href="/ui/runs/{rid}"' in r.text
    assert "completed" in r.text and "$0.0004" in r.text


async def test_detail_page_shows_steps_duration_tokens_and_cost(client, tracer):
    rid = await _make_run(tracer)
    r = await client.get(f"/ui/runs/{rid}")
    assert r.status_code == 200
    for expected in ["get_current_time", "Asia/Tokyo", "12 ms", "100 / 20", "boom", "$0.0004"]:
        assert expected in r.text


async def test_user_text_is_escaped(client, tracer):
    rid = await _make_run(tracer)
    page = (await client.get(f"/ui/runs/{rid}")).text
    assert "<b>sono</b>" not in page and "&lt;b&gt;sono&lt;/b&gt;" in page


async def test_unknown_run_is_404(client):
    assert (await client.get("/ui/runs/999")).status_code == 404


async def test_the_product_brief_can_be_read_and_edited_from_the_web(tracer, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import advisor, web
    from app.catalog import Catalog, Product

    cat = Catalog(products=[Product(name="QuadroRW")])
    monkeypatch.setattr(advisor, "get_catalog", lambda: cat)
    monkeypatch.setattr(web, "get_catalog", lambda: cat)
    app = FastAPI()
    app.include_router(web.router)
    app.state.csrf_token = "tok"
    app.state.tracer = tracer
    client = TestClient(app)

    assert "QuadroRW" in client.get("/ui/briefs").text
    page = client.get("/ui/brief/QuadroRW")
    assert page.status_code == 200 and 'name="key_numbers"' in page.text
    assert client.post("/ui/brief/QuadroRW", data={"token": "wrong"}).status_code == 403
    done = client.post(
        "/ui/brief/QuadroRW",
        data={"token": "tok", "objective": "Aiutare i commercialisti", "key_numbers": "12 clienti"},
        follow_redirects=False,
    )
    assert done.status_code == 303
    saved = await advisor.get_brief("QuadroRW")
    assert saved.objective == "Aiutare i commercialisti" and saved.key_numbers == "12 clienti"
    assert "12 clienti" in client.get("/ui/brief/QuadroRW").text
    assert client.get("/ui/brief/Boh").status_code == 404
