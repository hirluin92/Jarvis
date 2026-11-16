from types import SimpleNamespace

import httpx
import pytest

from app.catalog import Catalog, Product
from app.tools.builtin import github_activity
from app.tools.builtin.github_activity import GithubActivityInput

COMMIT = {"sha": "abcdef1234567", "commit": {"message": "Fix bug\n\nbody", "author": {"name": "G"}}}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "/missing/" in path:
        return httpx.Response(404, json={"message": "Not Found"})
    if "/empty/" in path and path.endswith("/commits"):
        return httpx.Response(409, json={"message": "Git Repository is empty."})
    if path.endswith("/commits"):
        return httpx.Response(200, json=[COMMIT, COMMIT])
    if path.endswith("/pulls"):
        return httpx.Response(200, json=[{"title": "Add feature"}])
    runs = [
        {"name": "CI", "conclusion": "success", "status": "completed"},
        {"name": "CI", "conclusion": "failure", "status": "completed"},
        {"name": "Deploy", "conclusion": None, "status": "in_progress"},
    ]
    return httpx.Response(200, json={"workflow_runs": runs})


@pytest.fixture
def setup(monkeypatch):
    cat = Catalog(
        products=[
            Product(name="Alpha", github_repo="me/alpha"),
            Product(name="NoRepo"),
            Product(name="Gone", github_repo="me/missing"),
            Product(name="Fresh", github_repo="me/empty"),
        ]
    )
    monkeypatch.setattr(github_activity, "get_catalog", lambda: cat)
    monkeypatch.setattr(
        github_activity, "get_settings", lambda: SimpleNamespace(github_read_token="tok")
    )
    monkeypatch.setattr(
        github_activity,
        "_client",
        lambda token: httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(_handler)
        ),
    )


async def _all():
    out = await github_activity.github_activity(GithubActivityInput())
    return {p["product"]: p for p in out["products"]}


async def test_summarises_commits_pulls_and_workflows(setup):
    a = (await _all())["Alpha"]
    assert a["commits"]["count"] == 2
    assert a["commits"]["latest"][0] == {"sha": "abcdef1", "message": "Fix bug", "author": "G"}
    assert a["open_pulls"] == {"count": 1, "titles": ["Add feature"]}
    assert a["workflows"]["by_result"] == {"success": 1, "failure": 1, "in_progress": 1}
    assert a["workflows"]["failed"] == ["CI"]


async def test_missing_repo_and_failing_repo_do_not_hide_the_others(setup):
    res = await _all()
    assert "no github_repo" in res["NoRepo"]["error"]
    assert "404" in res["Gone"]["error"]
    assert "error" not in res["Alpha"]


async def test_empty_repo_means_zero_commits_not_an_error(setup):
    fresh = (await _all())["Fresh"]
    assert "error" not in fresh
    assert fresh["commits"]["count"] == 0 and fresh["commits"]["latest"] == []


async def test_missing_token_is_a_tool_error(setup, monkeypatch):
    monkeypatch.setattr(
        github_activity, "get_settings", lambda: SimpleNamespace(github_read_token="")
    )
    with pytest.raises(RuntimeError, match="GITHUB_READ_TOKEN"):
        await github_activity.github_activity(GithubActivityInput())
