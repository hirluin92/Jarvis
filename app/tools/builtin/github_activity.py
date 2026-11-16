import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from app.catalog import get_catalog
from app.config import get_settings
from app.tools.registry import Tool

WINDOW_HOURS = 24
PAGE_SIZE = 30
FAILED = {"failure", "timed_out", "startup_failure"}


class GithubActivityInput(BaseModel):
    product: str | None = None


def _client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=15,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


async def _get(
    c: httpx.AsyncClient, path: str, params: dict[str, Any], empty_on_409: bool = False
) -> Any:
    r = await c.get(path, params=params)
    if empty_on_409 and r.status_code == 409:  # GitHub answers 409 for a repo with no commits
        return []
    r.raise_for_status()
    return r.json()


async def _repo_activity(c: httpx.AsyncClient, repo: str, since: datetime) -> dict[str, Any]:
    iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    commits, pulls, runs = await asyncio.gather(
        _get(
            c,
            f"/repos/{repo}/commits",
            {"since": iso, "per_page": PAGE_SIZE},
            empty_on_409=True,
        ),
        _get(c, f"/repos/{repo}/pulls", {"state": "open", "per_page": PAGE_SIZE}),
        _get(c, f"/repos/{repo}/actions/runs", {"created": f">={iso}", "per_page": PAGE_SIZE}),
    )
    workflow_runs = runs["workflow_runs"]
    return {
        "repo": repo,
        "window_hours": WINDOW_HOURS,
        "commits": {
            "count": len(commits),
            "truncated": len(commits) >= PAGE_SIZE,
            "latest": [
                {
                    "sha": x["sha"][:7],
                    "message": x["commit"]["message"].splitlines()[0],
                    "author": x["commit"]["author"]["name"],
                }
                for x in commits[:5]
            ],
        },
        "open_pulls": {"count": len(pulls), "titles": [p["title"] for p in pulls[:5]]},
        "workflows": {
            "count": len(workflow_runs),
            "by_result": dict(Counter(w["conclusion"] or w["status"] for w in workflow_runs)),
            "failed": [w["name"] for w in workflow_runs if w["conclusion"] in FAILED],
        },
    }


async def github_activity(inp: GithubActivityInput) -> dict[str, Any]:
    token = get_settings().github_read_token
    if not token:
        raise RuntimeError("GH_READ_TOKEN is not configured")
    products = get_catalog().select(inp.product)
    since = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)

    async with _client(token) as c:

        async def one(name: str, repo: str | None) -> dict[str, Any]:
            if repo is None:
                return {"product": name, "error": "no github_repo configured in the catalog"}
            try:
                return {"product": name, **await _repo_activity(c, repo, since)}
            except Exception as e:
                # one failing repo must not hide the others
                return {"product": name, "repo": repo, "error": f"{type(e).__name__}: {e}"}

        return {
            "products": list(await asyncio.gather(*(one(p.name, p.github_repo) for p in products)))
        }


TOOL = Tool(
    "github_activity",
    "Commits, open pull requests and CI workflow results of the last 24 hours for the GitHub "
    "repo of a product from the catalog. Omit 'product' to cover all products.",
    GithubActivityInput,
    "read",
    github_activity,
)
