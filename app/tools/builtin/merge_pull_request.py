from typing import Literal

from pydantic import BaseModel

from app.tools import github_write
from app.tools.registry import Tool


class MergeInput(BaseModel):
    product: str
    pr_number: int
    method: Literal["merge", "squash", "rebase"] = "squash"


def describe(inp: MergeInput) -> str:
    repo = github_write.repo_for(inp.product)
    return f"merge della pull request #{inp.pr_number} nel repo {repo} (metodo: {inp.method})"


async def merge_pull_request(inp: MergeInput) -> dict[str, object]:
    repo = github_write.repo_for(inp.product)
    async with github_write.client() as c:
        done = await c.put(
            f"/repos/{repo}/pulls/{inp.pr_number}/merge", json={"merge_method": inp.method}
        )
        done.raise_for_status()
        check = await c.get(f"/repos/{repo}/pulls/{inp.pr_number}")  # read back the real state
        check.raise_for_status()
        pr = check.json()
    if not pr["merged"]:
        raise RuntimeError(f"GitHub accepted the merge but PR #{inp.pr_number} is not merged")
    return {
        "repo": repo,
        "number": pr["number"],
        "merged": True,
        "merge_commit_sha": pr["merge_commit_sha"],
        "url": pr["html_url"],
    }


TOOL = Tool(
    "merge_pull_request",
    "Merge a pull request of a catalog product's GitHub repo. Irreversible: needs user "
    "confirmation.",
    MergeInput,
    "confirm",
    merge_pull_request,
    describe,
)
