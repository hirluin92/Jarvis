from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.catalog import Catalog, Product
from app.db import session as db
from app.db.models import Note, Todo
from app.tools import github_write
from app.tools.builtin import (
    add_todo,
    create_github_issue,
    create_note,
    merge_pull_request,
)
from app.tools.builtin.create_github_issue import IssueInput
from app.tools.builtin.merge_pull_request import MergeInput
from app.tools.registry import ToolRegistry


async def test_create_note_stores_and_returns_the_real_row(tracer):
    out = await create_note.create_note(
        create_note.NoteInput(text="comprare il latte", title="spesa")
    )
    assert out["title"] == "spesa" and out["chars"] == len("comprare il latte")
    async with db.session_maker()() as s:
        rows = list((await s.execute(select(Note))).scalars())
    assert [(r.id, r.body) for r in rows] == [(out["id"], "comprare il latte")]


async def test_add_todo_stores_the_due_date(tracer):
    out = await add_todo.add_todo(add_todo.TodoInput(text="chiamare Mario", due=date(2026, 12, 1)))
    assert out["due"] == "2026-12-01"
    async with db.session_maker()() as s:
        todo = (await s.execute(select(Todo))).scalar_one()
    assert todo.text == "chiamare Mario" and todo.due == date(2026, 12, 1) and todo.done is False


def test_todo_rejects_a_malformed_date_and_empty_text():
    reg = ToolRegistry()
    reg.register(add_todo.TOOL)
    with pytest.raises(Exception, match="due"):
        reg.validate("add_todo", {"text": "x", "due": "domani"})
    with pytest.raises(Exception, match="text"):
        reg.validate("add_todo", {"text": ""})


@pytest.fixture
def github(monkeypatch):
    calls: list[tuple[str, str]] = []
    state = {"merged": True, "merge_status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "POST" and path.endswith("/issues"):
            return httpx.Response(201, json={"number": 7})
        if path.endswith("/issues/7"):
            return httpx.Response(
                200,
                json={"number": 7, "html_url": "https://github.com/me/app/issues/7",
                      "title": "Bug", "state": "open"},
            )  # fmt: skip
        if request.method == "PUT" and path.endswith("/merge"):
            return httpx.Response(state["merge_status"], json={"message": "x"})
        if path.endswith("/pulls/3"):
            return httpx.Response(
                200,
                json={"number": 3, "merged": state["merged"], "merge_commit_sha": "abc123",
                      "html_url": "https://github.com/me/app/pull/3"},
            )  # fmt: skip
        return httpx.Response(404)

    cat = Catalog(products=[Product(name="App", github_repo="me/app"), Product(name="NoRepo")])
    monkeypatch.setattr(github_write, "get_catalog", lambda: cat)
    monkeypatch.setattr(
        github_write,
        "client",
        lambda: httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        ),
    )
    return SimpleNamespace(calls=calls, state=state)


async def test_issue_is_created_then_read_back_as_proof(github):
    out = await create_github_issue.create_github_issue(IssueInput(product="App", title="Bug"))
    assert out == {
        "repo": "me/app",
        "number": 7,
        "url": "https://github.com/me/app/issues/7",
        "title": "Bug",
        "state": "open",
    }
    assert github.calls == [("POST", "/repos/me/app/issues"), ("GET", "/repos/me/app/issues/7")]


async def test_issue_on_product_without_repo_is_an_error(github):
    with pytest.raises(ValueError, match="no github_repo"):
        await create_github_issue.create_github_issue(IssueInput(product="NoRepo", title="x"))


async def test_merge_reports_the_verified_state(github):
    out = await merge_pull_request.merge_pull_request(MergeInput(product="App", pr_number=3))
    assert out["merged"] is True and out["merge_commit_sha"] == "abc123"
    assert github.calls[0] == ("PUT", "/repos/me/app/pulls/3/merge")


async def test_merge_that_github_refuses_is_an_error(github):
    github.state["merge_status"] = 405  # e.g. not mergeable, or blocked by branch protection
    with pytest.raises(httpx.HTTPStatusError):
        await merge_pull_request.merge_pull_request(MergeInput(product="App", pr_number=3))


async def test_merge_not_confirmed_by_the_readback_is_an_error(github):
    github.state["merged"] = False
    with pytest.raises(RuntimeError, match="not merged"):
        await merge_pull_request.merge_pull_request(MergeInput(product="App", pr_number=3))


async def test_confirmation_text_says_exactly_what_will_happen(github):
    reg = ToolRegistry()
    reg.register(merge_pull_request.TOOL)
    text = await reg.describe(
        "merge_pull_request", {"product": "App", "pr_number": 3, "method": "merge"}
    )
    assert text == "merge della pull request #3 nel repo me/app (metodo: merge)"


async def test_confirmation_text_falls_back_to_the_generic_form(github):
    reg = ToolRegistry()
    reg.register(merge_pull_request.TOOL)
    text = await reg.describe("merge_pull_request", {"product": "Zeta", "pr_number": 3})
    assert text == "merge_pull_request(product='Zeta', pr_number=3)"


def test_write_token_is_required(monkeypatch):
    monkeypatch.setattr(
        github_write, "get_settings", lambda: SimpleNamespace(github_write_token="")
    )
    with pytest.raises(RuntimeError, match="GH_WRITE_TOKEN"):
        github_write.client()


def test_catalog_import_is_used():
    assert Catalog(products=[]).products == []
