"""One-time Gmail authorization: opens the browser, saves GOOGLE_REFRESH_TOKEN in .env.

Run once with:  uv run python -m app.auth_gmail
gmail.compose (drafts, sending) and gmail.readonly (the customers inbox, label-limited in code).
The refresh token is written to .env and never printed.
"""

import base64
import hashlib
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# compose: drafts and sending. readonly: reading, needed by the customers inbox (phase 13). Jarvis
# only ever reads the messages that carry the label INBOX_LABEL: that limit is in the code, because
# Google has no per-label read scope. A dedicated mailbox is the strictest option.
SCOPE = (
    "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly"
)
WAIT_SECONDS = 240


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def set_env_value(env_path: Path, key: str, value: str) -> None:
    """Replace the KEY=... line in .env, or append it."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


class _Handler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        type(self).result = q
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in q
        self.wfile.write(
            ("<h3>Autorizzazione completata: puoi chiudere questa scheda.</h3>" if ok
             else "<h3>Autorizzazione non riuscita: torna al terminale.</h3>").encode()
        )  # fmt: skip

    def log_message(self, *_: object) -> None:
        pass


def main() -> int:
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        print("Mancano GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET in .env")
        return 1
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.timeout = WAIT_SECONDS
    redirect = f"http://127.0.0.1:{server.server_port}"
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": s.google_client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )
    print("Apro il browser per l'autorizzazione di Gmail (permesso: creare bozze e inviare)...")
    print(f"Se non si apre, vai su:\n{url}")
    webbrowser.open(url)
    server.handle_request()
    res = _Handler.result
    if res.get("state") != state or "code" not in res:
        print(f"Autorizzazione non completata ({res.get('error', 'nessuna risposta in tempo')}).")
        return 1
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": res["code"],
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=20,
    )
    token = r.json().get("refresh_token") if r.status_code == 200 else None
    if not token:
        print(f"Scambio del codice fallito (HTTP {r.status_code}): {r.json().get('error')}")
        return 1
    set_env_value(Path(".env"), "GOOGLE_REFRESH_TOKEN", token)
    print("Fatto: GOOGLE_REFRESH_TOKEN salvato in .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
