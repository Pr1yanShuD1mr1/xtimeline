"""The full Playwright path, against a local mock of X.

Nothing here is stubbed below the browser: Extractor launches Chromium, opens a
page, navigates, waits for the timeline, scrolls, harvests and reports. Only the
site it points at is fake (XTIMELINE_BASE_URL -> a local HTTP server), so this
is the closest thing to a live run that works without a login.
"""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import scraper

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)


def _article(tid: str, dt: str, text: str = "hello") -> str:
    return f"""
    <article data-testid="tweet">
      <div data-testid="tweetText">{text}</div>
      <a href="https://x.com/nasa/status/{tid}">
        <time datetime="{dt}">Jan 10</time>
      </a>
      <div role="group">
        <button data-testid="reply" aria-label="2 replies"></button>
        <button data-testid="like" aria-label="1,000 likes"></button>
      </div>
    </article>
    """


TIMELINE = (
    "<html><body><main>"
    + _article("1", "2026-01-20T10:00:00.000Z", "newest")
    + _article("2", "2026-01-15T10:00:00.000Z", "middle")
    + _article("3", "2026-01-10T10:00:00.000Z", "oldest")
    + "</main></body></html>"
)

LOGIN_WALL = (
    '<html><body><a data-testid="loginButton">Log in</a>'
    "<p>Sign up</p></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib API
        body = LOGIN_WALL if "wall" in self.path else TIMELINE
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def mock_site(monkeypatch, tmp_path):
    """A tiny server standing in for x.com, plus a fake saved session."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    state = tmp_path / "x_state.json"
    state.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    monkeypatch.setattr(scraper, "STATE_FILE", state)
    monkeypatch.setattr(scraper, "PROFILE_DIR", tmp_path / ".no_profile")
    monkeypatch.setattr(scraper, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setenv("XTIMELINE_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _extractor(**kwargs):
    opts = dict(headless=True, max_scrolls=2, scroll_delay_ms=10,
                wait_tweets_ms=5000, nav_timeout_ms=10000)
    opts.update(kwargs)
    return scraper.Extractor(**opts)


async def test_a_whole_account_scrape_runs_through_the_browser(mock_site):
    progress: list[tuple[float, str]] = []

    async with _extractor() as ex:
        result = await ex.scrape(
            "nasa", START, END, "search",
            on_progress=lambda frac, msg: progress.append((frac, msg)),
        )

    assert [t["text"] for t in result.tweets] == ["newest", "middle", "oldest"]
    assert result.strategy == "search"
    assert all(t["account"] == "nasa" for t in result.tweets)
    assert result.tweets[0]["metrics"]["likes"] == 1000
    assert progress, "the caller should get progress callbacks"


async def test_the_search_url_is_what_the_browser_opens(mock_site, monkeypatch):
    opened: list[str] = []
    original = scraper.Extractor._navigate

    async def spy(self, page, url):
        opened.append(url)
        return await original(self, page, url)

    monkeypatch.setattr(scraper.Extractor, "_navigate", spy)

    async with _extractor() as ex:
        await ex.scrape("nasa", START, END, "search")

    assert opened and opened[0].startswith(mock_site + "/search?q=")
    assert "from%3Anasa" in opened[0]


async def test_a_login_wall_raises_session_expired(mock_site, monkeypatch):
    """The 'search' page must come back as a login wall, not as zero posts."""
    monkeypatch.setattr(scraper, "search_url",
                        lambda h, s, e, base=None: base or mock_site_url() + "/wall/search")

    async with _extractor() as ex:
        with pytest.raises(scraper.SessionExpired):
            await ex.scrape("nasa", START, END, "search")


def mock_site_url() -> str:
    import os
    return os.environ["XTIMELINE_BASE_URL"]


async def test_cancel_stops_the_scroll_loop(mock_site):
    state = {"cancelled": False}

    async with _extractor(max_scrolls=50,
                          cancel_check=lambda: state["cancelled"]) as ex:
        state["cancelled"] = True     # already cancelled before the first scroll
        result = await ex.scrape("nasa", START, END, "search")
    # Cancelled before harvesting: the loop breaks with the "cancelled" note.
    assert result.note == "cancelled"
    assert result.tweets == []


async def test_a_missing_session_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "STATE_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(scraper, "PROFILE_DIR", tmp_path / "nope")
    with pytest.raises(scraper.SessionMissing) as exc:
        async with scraper.Extractor(headless=True):
            pass
    assert "save_session.py" in str(exc.value)


# --------------------------------------------------------------------------- #
#  Launch options: the headless tells X actually looks at
# --------------------------------------------------------------------------- #

def test_launch_options_send_a_desktop_user_agent(monkeypatch):
    monkeypatch.delenv("XTIMELINE_USER_AGENT", raising=False)
    opts = scraper._launch_options(headless=True)

    assert "HeadlessChrome" not in opts["user_agent"]
    assert "Chrome/" in opts["user_agent"]
    assert "Safari/537.36" in opts["user_agent"]


def test_headless_prefers_the_full_chromium_channel(monkeypatch):
    monkeypatch.delenv("XTIMELINE_NO_CHANNEL", raising=False)
    monkeypatch.delenv("XTIMELINE_CHROME_PATH", raising=False)

    assert scraper._launch_options(headless=True).get("channel") == "chromium"
    assert "channel" not in scraper._launch_options(headless=False)


def test_the_channel_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("XTIMELINE_NO_CHANNEL", "1")
    assert "channel" not in scraper._launch_options(headless=True)


def test_an_explicit_binary_beats_the_channel(monkeypatch):
    monkeypatch.setenv("XTIMELINE_CHROME_PATH", "/usr/bin/chromium")
    opts = scraper._launch_options(headless=True)

    assert opts["executable_path"] == "/usr/bin/chromium"
    assert "channel" not in opts


def test_the_user_agent_can_be_overridden(monkeypatch):
    monkeypatch.setenv("XTIMELINE_USER_AGENT", "some-agent/1.0")
    assert scraper._launch_options(headless=True)["user_agent"] == "some-agent/1.0"
