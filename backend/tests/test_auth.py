"""Auth: the API must be closed, and the token must work where headers can't."""

import time

import pytest

from app import auth


async def test_health_is_public_and_says_playwright_only(client):
    res = await client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["extractor"] == "playwright"
    assert body["auth_required"] is True


async def test_protected_endpoints_need_a_token(client):
    for path in ("/api/jobs", "/api/session", "/api/auth/me"):
        res = await client.get(path)
        assert res.status_code == 401, path
        assert res.headers.get("www-authenticate") == "Bearer"


async def test_wrong_password_is_rejected(client):
    res = await client.post("/api/auth/login", json={"password": "nope"})
    assert res.status_code == 401
    assert "wrong password" in res.json()["detail"]


async def test_login_returns_a_token_and_me_accepts_it(client):
    res = await client.post("/api/auth/login", json={"password": "test-password"})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_at"] > time.time()

    me = await client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["authenticated"] is True


async def test_token_in_query_string_works_for_sse_and_downloads(client, token):
    """EventSource and <a download> can't set headers, so ?token= must work."""
    res = await client.get(f"/api/jobs?token={token}")
    assert res.status_code == 200
    assert "jobs" in res.json()


async def test_tampered_token_is_rejected(client, token):
    body, _, sig = token.partition(".")
    forged = body + "." + ("A" * len(sig))
    res = await client.get("/api/jobs", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401
    assert "signature" in res.json()["detail"]


async def test_expired_token_is_rejected(client):
    expired, _ = auth.issue_token(ttl=-60)
    res = await client.get("/api/jobs", headers={"Authorization": f"Bearer {expired}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"]


async def test_failed_logins_are_throttled(client):
    for _ in range(auth.MAX_FAILURES):
        assert (await client.post("/api/auth/login",
                                  json={"password": "nope"})).status_code == 401
    res = await client.post("/api/auth/login", json={"password": "nope"})
    assert res.status_code == 429
    # ...and the lockout holds even for the right password.
    res = await client.post("/api/auth/login", json={"password": "test-password"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


async def test_successful_login_clears_the_failure_count(client):
    for _ in range(2):
        await client.post("/api/auth/login", json={"password": "nope"})
    ok = await client.post("/api/auth/login", json={"password": "test-password"})
    assert ok.status_code == 200
    assert auth.remaining_attempts("127.0.0.1") == auth.MAX_FAILURES


async def test_logout_is_a_no_op_204(client, token):
    res = await client.post("/api/auth/logout",
                            headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 204


@pytest.mark.parametrize("bad", ["", "abc", "abc.def"])
async def test_garbage_tokens_are_rejected(client, bad):
    res = await client.get("/api/jobs", headers={"Authorization": f"Bearer {bad}"})
    assert res.status_code == 401
