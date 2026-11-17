import asyncio
import ssl
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.catalog import get_catalog
from app.tools.registry import Tool

CERT_WARN_DAYS = 14


class SiteStatusInput(BaseModel):
    product: str | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=10)


async def _cert_days_left(host: str, port: int) -> int:
    reader, writer = await asyncio.wait_for(
        # same CA bundle as the HTTP check (certifi), not the incomplete Windows system store
        asyncio.open_connection(host, port, ssl=httpx.create_ssl_context()),
        10,
    )
    try:
        cert = writer.get_extra_info("ssl_object").getpeercert()
    finally:
        writer.close()
        await writer.wait_closed()
    return int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400)


async def _check_url(url: str) -> dict[str, object]:
    result: dict[str, object] = {"url": url}
    start = time.monotonic()
    try:
        async with _client() as c:
            r = await c.get(url)
        result.update(ok=r.status_code < 400, status=r.status_code)
        result["ms"] = int((time.monotonic() - start) * 1000)
    except Exception as e:
        # an unreachable URL is a finding to report, not a tool failure
        result.update(ok=False, error=f"{type(e).__name__}: {e}")
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        try:
            days = await _cert_days_left(parsed.hostname, parsed.port or 443)
            result.update(cert_days_left=days, cert_expiring=days < CERT_WARN_DAYS)
        except Exception as e:
            result["cert_error"] = f"{type(e).__name__}: {e}"
    return result


async def site_status(inp: SiteStatusInput) -> dict[str, object]:
    products = get_catalog().select(inp.product)

    async def one(name: str, urls: list[str]) -> dict[str, object]:
        checks = await asyncio.gather(*(_check_url(u) for u in urls))
        return {"product": name, "urls": list(checks)}

    return {"products": list(await asyncio.gather(*(one(p.name, p.urls) for p in products)))}


TOOL = Tool(
    "site_status",
    "Check whether a product from the catalog is up: reachability, response time and TLS "
    "certificate expiry of its public URLs. Use this for any named product or site of the user "
    "(for example 'Jarvis' or 'Demo Down'); omit 'product' to check all of them.",
    SiteStatusInput,
    "read",
    site_status,
)
