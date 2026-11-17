"""Access token for the web pages (runs, memory, voice).

Off when ACCESS_TOKEN is empty (local use). When set, every route except /health needs the
token, given as `Authorization: Bearer <token>`, as the cookie `jarvis_token`, or once as
`?token=<token>` (which stores the cookie and redirects to the clean URL).
"""

import hmac
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

COOKIE = "jarvis_token"
OPEN_PATHS = {"/health", "/jarvis-sw.js"}
# the installable-app files hold nothing private and browsers fetch them without cookies
OPEN_PREFIXES = ("/jarvis/ui/icons/", "/jarvis/ui/manifest.webmanifest")

LOGIN_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#03060b;color:#dff8ff;
font:16px system-ui,sans-serif}form{display:grid;gap:14px;width:min(320px,86vw);text-align:center}
h1{letter-spacing:.5em;color:#00d9ff;font-size:22px;text-shadow:0 0 18px #00d9ff}
input,button{font:inherit;padding:12px;border:1px solid #0a8fa8;background:#07141f;
color:inherit;border-radius:6px}
button{background:#00d9ff22;color:#00d9ff;cursor:pointer;letter-spacing:.2em;text-transform:uppercase}</style></head>
<body><form onsubmit="event.preventDefault();
location.href=location.pathname+'?token='+encodeURIComponent(t.value)">
<h1>JARVIS</h1><input id="t" name="t" type="password" placeholder="Password di accesso"
autocomplete="current-password" autofocus required><button>Entra</button></form></body></html>"""


def _valid(given: str | None, token: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token.encode())


def access_middleware(get_token: Callable[[], str]):
    """Function for `app.middleware("http")`; the token is read per request (settings are lazy)."""

    async def check(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        token = get_token()
        path = request.url.path
        if not token or path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
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
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(LOGIN_PAGE, status_code=401)  # a browser: ask for the password
        return PlainTextResponse("Accesso negato", status_code=401)

    return check
