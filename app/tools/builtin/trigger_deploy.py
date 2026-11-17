from pydantic import BaseModel

from app.catalog import get_catalog
from app.tools import github_write
from app.tools.registry import Tool


class DeployInput(BaseModel):
    product: str


def _target(product: str) -> tuple[str, str, str]:
    """(repo, workflow file, branch) of a catalog product. Nothing outside the catalog runs."""
    (p,) = get_catalog().select(product)
    if not p.github_repo or not p.deploy_workflow:
        raise ValueError(f"Product {p.name!r} has no deploy_workflow in the catalog")
    return p.github_repo, p.deploy_workflow, p.deploy_ref


def describe(inp: DeployInput) -> str:
    repo, workflow, ref = _target(inp.product)
    return f"deploy di {inp.product}: avvio del workflow {workflow} su {repo} (ramo {ref})"


async def trigger_deploy(inp: DeployInput) -> dict[str, object]:
    repo, workflow, ref = _target(inp.product)
    async with github_write.client() as c:
        r = await c.post(
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches", json={"ref": ref}
        )
        r.raise_for_status()  # 204 means GitHub accepted the request
        runs = await c.get(
            f"/repos/{repo}/actions/workflows/{workflow}/runs",
            params={"event": "workflow_dispatch", "per_page": 1},
        )
        runs.raise_for_status()
        latest = (runs.json().get("workflow_runs") or [None])[0]
    return {
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
        "dispatched": True,
        # the run may take a moment to appear: this is the most recent one, not always the new one
        "latest_run_url": latest["html_url"] if latest else None,
        "latest_run_status": latest["status"] if latest else None,
    }


TOOL = Tool(
    "trigger_deploy",
    "Start the deploy workflow of a catalog product on GitHub Actions. Changes a live system: "
    "needs user confirmation. Only products with a deploy_workflow in the catalog.",
    DeployInput,
    "confirm",
    trigger_deploy,
    describe,
)
