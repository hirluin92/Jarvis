"""Access token for the web pages (runs, memory, voice).

Off when ACCESS_TOKEN is empty (local use). When set, every route except /health needs the
token, given as `Authorization: Bearer <token>`, as the cookie `jarvis_token`, or once as
`?token=<token>` (which stores the cookie and redirects to the clean URL).
"""

import hmac
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

COOKIE = "jarvis_token"
OPEN_PATHS = {"/health"}


def _valid(given: str | None, token: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token.encode())


def access_middleware(get_token: Callable[[], str]):
    """Function for `app.middleware("http")`; the token is read per request (settings are lazy)."""

    async def check(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        token = get_token()
        if not token or request.url.path in OPEN_PATHS:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else None
        if _valid(bearer, token) or _valid(request.cookies.get(COOKIE), token):
            return await call_next(request)
        if _valid(request.query_params.get("token"), token):
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            target = request.url.path + (f"?{urlencode(rest)}" if rest else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(
                COOKIE,
                token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                max_age=60 * 60 * 24 * 90,
            )
            return response
        return PlainTextResponse("Accesso negato", status_code=401)

    return check
