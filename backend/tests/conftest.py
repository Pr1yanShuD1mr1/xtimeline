"""Shared fixtures.

The tests never touch X: the API tests stub the Playwright Extractor, and the
DOM tests drive a real browser against a local HTML page that mimics X's
markup. Nothing here needs a logged-in session.
"""

import os

# Never read a developer's real backend/.env during tests.
os.environ.setdefault("XTIMELINE_SKIP_DOTENV", "1")

# Must be set before app.auth reads them.
os.environ.setdefault("XTIMELINE_PASSWORD", "test-password")
os.environ.setdefault("XTIMELINE_SECRET", "test-only-secret-not-for-production")
# No politeness pause between accounts in tests.
os.environ.setdefault("XTIMELINE_ACCOUNT_PAUSE_MAX_MS", "0")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import auth
from app.jobs import store
from app.main import app

PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def _clean_state():
    """Isolate the module-global login throttle and job store between tests."""
    auth._failures.clear()
    store.clear()
    yield
    auth._failures.clear()
    store.clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def token(client):
    res = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
#  A real browser, for the DOM tests
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def page():
    """A headless page, or skip if no browser is available in this sandbox."""
    from playwright.async_api import async_playwright

    opts: dict = {"headless": True}
    exe = os.environ.get("XTIMELINE_CHROME_PATH")
    if exe:
        opts["executable_path"] = exe
    if os.environ.get("XTIMELINE_NO_SANDBOX", "").lower() in ("1", "true", "yes"):
        opts["args"] = ["--no-sandbox"]

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(**opts)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no usable Chromium for DOM tests: {exc}")

    pg = await browser.new_page()
    try:
        yield pg
    finally:
        await browser.close()
        await pw.stop()
