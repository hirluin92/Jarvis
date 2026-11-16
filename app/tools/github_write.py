import httpx

from app.catalog import get_catalog
from app.config import get_settings


def client() -> httpx.AsyncClient:
    """GitHub client authenticated with the WRITE token (used only by write/confirm tools)."""
    token = get_settings().github_write_token
    if not token:
        raise RuntimeError("GH_WRITE_TOKEN is not configured")
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=15,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def repo_for(product: str) -> str:
    """The GitHub repo (owner/name) of a catalog product."""
    (found,) = get_catalog().select(product)
    if not found.github_repo:
        raise ValueError(f"Product {found.name!r} has no github_repo in the catalog")
    return found.github_repo
