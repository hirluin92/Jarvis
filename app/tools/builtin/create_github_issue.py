from pydantic import BaseModel, Field

from app.tools import github_write
from app.tools.registry import Tool


class IssueInput(BaseModel):
    product: str
    title: str = Field(min_length=1, max_length=200)
    body: str = ""


async def create_github_issue(inp: IssueInput) -> dict[str, object]:
    repo = github_write.repo_for(inp.product)
    async with github_write.client() as c:
        created = await c.post(f"/repos/{repo}/issues", json={"title": inp.title, "body": inp.body})
        created.raise_for_status()
        number = created.json()["number"]
        check = await c.get(f"/repos/{repo}/issues/{number}")  # read back the real state
        check.raise_for_status()
        issue = check.json()
    return {
        "repo": repo,
        "number": issue["number"],
        "url": issue["html_url"],
        "title": issue["title"],
        "state": issue["state"],
    }


TOOL = Tool(
    "create_github_issue",
    "Open an issue on the GitHub repo of a catalog product. Reversible (the issue can be closed).",
    IssueInput,
    "write",
    create_github_issue,
)
