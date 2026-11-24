"""Single-password auth, so the React app can use real protected routes.

There is no user table here: this is a local single-operator tool, so the whole
API sits behind one shared password and hands out a short-lived HMAC-signed
bearer token. The frontend keeps the token in localStorage, sends it as
`Authorization: Bearer <token>`, and drops it on any 401.

Three ways the token can arrive, because not every browser API can set headers:

  * `Authorization: Bearer <token>`  - fetch() from the app
  * `?token=<token>`                 - EventSource (SSE) and <a download> links
  * nothing                          - 401, which the UI turns into a redirect

Set the password with the XTIMELINE_PASSWORD environment variable. If it is not
set the prototype falls back to "changeme" and logs a loud warning at startup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger("xtimeline.auth")

BACKEND_DIR = Path(__file__).resolve().parent.parent
SECRET_FILE = BACKEND_DIR / ".auth_secret"

DEFAULT_PASSWORD = "changeme"
DEFAULT_TTL_MIN = 720  # 12 hours

# Failed-login throttle: max 5 failures per IP per minute.
MAX_FAILURES = 5
FAILURE_WINDOW = 60.0
_failures: dict[str, deque[float]] = defaultdict(deque)


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

def password() -> str:
    """The shared password. Read on every call so tests / env changes apply."""
    return os.environ.get("XTIMELINE_PASSWORD") or DEFAULT_PASSWORD


def using_default_password() -> bool:
    """True when the effective password *is* the default.

    Checking the value rather than "is the variable set" matters once there is
    a .env: copying the example sets XTIMELINE_PASSWORD=changeme, which used to
    silence this warning while leaving the password unchanged.
    """
    return password() == DEFAULT_PASSWORD


def token_ttl() -> int:
    try:
        minutes = int(os.environ.get("XTIMELINE_TOKEN_TTL_MIN", DEFAULT_TTL_MIN))
    except ValueError:
        minutes = DEFAULT_TTL_MIN
    return max(1, minutes) * 60


def _secret() -> bytes:
    """Signing key: env var, else a generated file that survives restarts.

    A per-process random key would log everyone out on every reload, so the
    generated key is persisted (mode 600) next to the app.
    """
    env = os.environ.get("XTIMELINE_SECRET")
    if env:
        return env.encode("utf-8")
    if SECRET_FILE.exists():
        raw = SECRET_FILE.read_bytes().strip()
        if raw:
            return raw
    raw = secrets.token_urlsafe(48).encode("utf-8")
    SECRET_FILE.write_bytes(raw)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:  # pragma: no cover - Windows / odd filesystems
        pass
    return raw


# --------------------------------------------------------------------------- #
#  Tokens
# --------------------------------------------------------------------------- #

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())


def issue_token(ttl: Optional[int] = None) -> tuple[str, int]:
    """Return (token, expires_at_epoch_seconds)."""
    now = int(time.time())
    exp = now + (ttl if ttl is not None else token_ttl())
    payload = {"sub": "operator", "iat": now, "exp": exp,
               "jti": secrets.token_hex(8)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}", exp


def decode_token(token: str) -> dict:
    """Verify signature + expiry. Raises 401 on anything wrong."""
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="malformed token")
    body, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(body)):
        raise HTTPException(status_code=401, detail="invalid token signature")
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        raise HTTPException(status_code=401, detail="malformed token payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="malformed token payload")
    if int(payload.get("exp", 0)) <= time.time():
        raise HTTPException(status_code=401, detail="token expired")
    return payload


def check_password(candidate: str) -> bool:
    return hmac.compare_digest((candidate or "").encode("utf-8"),
                               password().encode("utf-8"))


# --------------------------------------------------------------------------- #
#  Login throttle
# --------------------------------------------------------------------------- #

def _prune(bucket: deque[float], now: float) -> None:
    while bucket and now - bucket[0] > FAILURE_WINDOW:
        bucket.popleft()


def throttle_check(client: str) -> None:
    bucket = _failures[client]
    _prune(bucket, time.time())
    if len(bucket) >= MAX_FAILURES:
        retry = int(FAILURE_WINDOW - (time.time() - bucket[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts - try again in {retry}s",
            headers={"Retry-After": str(retry)},
        )


def note_failure(client: str) -> None:
    bucket = _failures[client]
    now = time.time()
    _prune(bucket, now)
    bucket.append(now)


def clear_failures(client: str) -> None:
    _failures.pop(client, None)


def remaining_attempts(client: str) -> int:
    """How many tries are left before the throttle kicks in."""
    bucket = _failures[client]
    _prune(bucket, time.time())
    return max(0, MAX_FAILURES - len(bucket))


# --------------------------------------------------------------------------- #
#  FastAPI dependency
# --------------------------------------------------------------------------- #

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    token: Optional[str] = Query(
        None, description="Bearer token, for EventSource / download links."
    ),
) -> dict:
    """Gate a route on a valid token. Returns the token payload."""
    raw = creds.credentials if creds else (token or "")
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(raw)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
