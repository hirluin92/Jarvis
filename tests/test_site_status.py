import httpx
import pytest

from app.catalog import Catalog, Product
from app.tools.builtin import site_status
from app.tools.builtin.site_status import SiteStatusInput


@pytest.fixture
def catalog(monkeypatch):
    cat = Catalog(
        products=[
            Product(name="Up", urls=["https://up.example"]),
            Product(name="Down", urls=["http://down.example"]),
            Product(name="Broken", urls=["http://broken.example"]),
        ]
    )
    monkeypatch.setattr(site_status, "get_catalog", lambda: cat)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "down.example":
            raise httpx.ConnectError("connection refused")
        return httpx.Response(500 if host == "broken.example" else 200)

    monkeypatch.setattr(
        site_status, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    async def cert(host, port):
        return 5

    monkeypatch.setattr(site_status, "_cert_days_left", cert)


def _by_product(out):
    return {p["product"]: p["urls"][0] for p in out["products"]}


async def test_all_products_reports_up_down_and_http_error(catalog):
    res = _by_product(await site_status.site_status(SiteStatusInput()))
    assert res["Up"]["ok"] is True and res["Up"]["status"] == 200
    assert res["Down"]["ok"] is False and "ConnectError" in res["Down"]["error"]
    assert res["Broken"]["ok"] is False and res["Broken"]["status"] == 500


async def test_expiring_certificate_is_flagged_only_for_https(catalog):
    res = _by_product(await site_status.site_status(SiteStatusInput()))
    assert res["Up"]["cert_days_left"] == 5 and res["Up"]["cert_expiring"] is True
    assert "cert_days_left" not in res["Down"]


async def test_single_product_and_unknown_product(catalog):
    out = await site_status.site_status(SiteStatusInput(product="up"))
    assert [p["product"] for p in out["products"]] == ["Up"]
    with pytest.raises(ValueError, match="Unknown product"):
        await site_status.site_status(SiteStatusInput(product="Zeta"))


async def test_certificate_check_failure_does_not_break_the_result(catalog, monkeypatch):
    async def bad(host, port):
        raise OSError("handshake failed")

    monkeypatch.setattr(site_status, "_cert_days_left", bad)
    res = _by_product(await site_status.site_status(SiteStatusInput(product="Up")))
    assert res["Up"]["ok"] is True and "handshake failed" in res["Up"]["cert_error"]
