import time

import httpx
from pydantic import BaseModel

from app.tools.registry import Tool


class HealthInput(BaseModel):
    url: str


async def http_health_check(inp: HealthInput) -> dict[str, object]:
    start = time.monotonic()
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as c:
        r = await c.get(inp.url)
    return {"url": inp.url, "status": r.status_code, "ms": int((time.monotonic() - start) * 1000)}


TOOL = Tool(
    "http_health_check",
    "GET one arbitrary URL that the user typed or pasted and return HTTP status and response "
    "time in ms. Not for products of the catalog: use site_status for those.",
    HealthInput,
    "read",
    http_health_check,
)
