"""The GitHub calls of the worker, with the dedicated BUILD_GITHUB_TOKEN."""

import httpx


def _client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=20,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


async def create_draft_pr(
    token: str, repo: str, title: str, head: str, base: str, body: str
) -> str:
    """Open a pull request as a DRAFT and return its URL."""
    async with _client(token) as c:
        r = await c.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": True},
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("draft"):
        raise RuntimeError("GitHub did not create the pull request as a draft")
    return data["html_url"]
