from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import access_middleware


def make(token: str) -> TestClient:
    app = FastAPI()
    app.middleware("http")(access_middleware(lambda: token))

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/runs")
    def runs():
        return {"runs": []}

    return TestClient(app)


def test_open_when_no_token_is_set():
    assert make("").get("/runs").status_code == 200


def test_denied_without_token_but_health_stays_open():
    c = make("s3cret")
    assert c.get("/runs").status_code == 401
    assert c.get("/health").status_code == 200


def test_bearer_header_is_accepted_and_wrong_one_is_not():
    c = make("s3cret")
    assert c.get("/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/runs", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_query_token_sets_cookie_then_works_without_it():
    c = make("s3cret")
    r = c.get("/runs?token=s3cret&limit=5", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs?limit=5"
    assert "jarvis_token" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert c.get("/runs?limit=5").status_code == 200  # the client kept the cookie
    assert make("s3cret").get("/runs?token=wrong").status_code == 401
