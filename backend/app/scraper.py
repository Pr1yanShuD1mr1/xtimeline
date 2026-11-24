"""Playwright-only extraction.

There is no twscrape here and no HTTP/GraphQL client - every byte comes out of
a real browser driving the logged-in session saved by `save_session.py`.

Two strategies, chosen per request:

  search   x.com/search?q=from:handle since:YYYY-MM-DD until:YYYY-MM-DD&f=live
           X filters by date server-side, so this is the fast, precise path and
           the default.
  profile  x.com/handle, scroll the profile timeline. Slower and you pay for
           every post between "now" and your start date, but it is the most
           faithful "what is on this account's page" read.
  auto     search first; if it renders nothing, fall back to profile.

X virtualizes its timeline: only a small window of posts exists in the DOM at
any moment and the nodes are recycled as you scroll. So we scroll in small
steps and harvest whatever is rendered, keyed by post id, and stop as soon as
we scroll past the start of the requested range (or hit the per-account cap).

Every selector is anchored to `data-testid` attributes and the <time> element,
never to X's obfuscated CSS classes - that keeps this working across X's
frequent restyles and makes it language-independent.

Failure modes are named, because the UI shows them to a human:
  SessionMissing    no x_state.json - run save_session.py
  SessionExpired    the saved login is dead (login wall / knowledge check)
  RateLimited       X's "Something went wrong. Try reloading." wall
  AccountUnavailable  protected, suspended or non-existent account
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

log = logging.getLogger("xtimeline.scraper")

BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BACKEND_DIR / "x_state.json"
PROFILE_DIR = BACKEND_DIR / ".x_profile"
OUTPUT_DIR = BACKEND_DIR / "output"

TWEET_SEL = 'article[data-testid="tweet"]'

# Turn off the obvious automation tells so X is less likely to challenge us.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
]
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

# Playwright's bundled headless shell announces itself as
# "...HeadlessChrome/141.0.0.0..." in the User-Agent. X answers that with a
# search page whose shell renders but whose timeline never mounts: the title
# and URL look perfect and every account comes back with zero posts. Sending a
# normal desktop Chrome UA fixes that specific failure, which is why it is a
# default rather than a knob you have to discover.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def user_agent() -> str:
    return os.environ.get("XTIMELINE_USER_AGENT") or DEFAULT_UA


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Politeness knobs. A real browser is much easier for X to fingerprint than a
# bare HTTP client, so slow and jittered beats fast and regular.
SCROLL_DELAY_MS = _env_int("XTIMELINE_SCROLL_DELAY_MS", 1200)
NAV_TIMEOUT_MS = _env_int("XTIMELINE_NAV_TIMEOUT_MS", 60000)
WAIT_TWEETS_MS = _env_int("XTIMELINE_WAIT_TWEETS_MS", 20000)


# --------------------------------------------------------------------------- #
#  Errors
# --------------------------------------------------------------------------- #

class ScrapeError(RuntimeError):
    """Base class for the named failure modes below."""


class SessionMissing(ScrapeError):
    pass


class SessionExpired(ScrapeError):
    pass


class RateLimited(ScrapeError):
    pass


class AccountUnavailable(ScrapeError):
    pass


@dataclass
class ScrapeResult:
    tweets: list[dict] = field(default_factory=list)
    note: str = ""              # human-readable explanation when empty/partial
    strategy: str = ""          # which page produced it
    debug_shot: str = ""


# --------------------------------------------------------------------------- #
#  URLs
# --------------------------------------------------------------------------- #

def base_url() -> str:
    """Where the site lives. Overridable so tests can point at a local mock."""
    return os.environ.get("XTIMELINE_BASE_URL", "https://x.com").rstrip("/")


def search_url(handle: str, start: datetime, end: datetime,
               base: Optional[str] = None) -> str:
    """X's own search, restricted to one account and the date range."""
    base = (base or base_url()).rstrip("/")
    since = start.astimezone(timezone.utc).date().isoformat()
    # X's `until:` is exclusive, so ask for the day after the last wanted day.
    until = (end.astimezone(timezone.utc).date() + timedelta(days=1)).isoformat()
    query = f"from:{handle} since:{since} until:{until}"
    return (
        base + "/search?q=" + quote(query, safe="")
        + "&src=typed_query&f=live"
    )


def profile_url(handle: str, base: Optional[str] = None) -> str:
    return f"{(base or base_url()).rstrip('/')}/{handle}"


# --------------------------------------------------------------------------- #
#  DOM helpers
# --------------------------------------------------------------------------- #

async def _metric(article, testid: str, word: str) -> int:
    """Engagement counts live in aria-label ('42 replies, 10 reposts, ...').

    The visible number is abbreviated ('1.2K'), so parse the aria-label.
    """
    el = await article.query_selector(f'[data-testid="{testid}"]')
    if not el:
        return 0
    label = await el.get_attribute("aria-label") or ""
    m = re.search(r"([\d,]+)\s+" + word, label, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else 0


async def _context_label(article) -> str:
    """The little line above a post: 'Pinned' or '<someone> reposted'."""
    el = await article.query_selector('[data-testid="socialContext"]')
    if not el:
        return ""
    try:
        return (await el.inner_text()).strip().lower()
    except Exception:
        return ""


async def _display_name(article, handle: str) -> str:
    """The account's display name ("freeCodeCamp.org"), not the handle."""
    try:
        el = await article.query_selector('[data-testid="User-Name"] span')
        if el:
            name = (await el.inner_text()).strip()
            if name and not name.startswith("@"):
                return name
    except Exception:
        pass
    return handle


async def _avatar(article) -> str:
    """Profile image URL, best effort - a missing avatar is not an error."""
    for sel in ('[data-testid="Tweet-User-Avatar"] img',
                'img[src*="/profile_images/"]'):
        try:
            el = await article.query_selector(sel)
            if el:
                src = await el.get_attribute("src")
                if src:
                    return src
        except Exception:
            continue
    return ""


async def _verified(article) -> bool:
    try:
        return bool(await article.query_selector(
            '[data-testid="icon-verified"], svg[aria-label="Verified account"]'))
    except Exception:
        return False


async def _media(article) -> list[str]:
    """Image (and video poster) URLs, in the order X rendered them."""
    urls: list[str] = []
    try:
        for img in await article.query_selector_all('[data-testid="tweetPhoto"] img'):
            src = await img.get_attribute("src")
            if src and src not in urls:
                urls.append(src)
        if not urls:
            for vid in await article.query_selector_all("video[poster]"):
                src = await vid.get_attribute("poster")
                if src and src not in urls:
                    urls.append(src)
    except Exception:
        pass
    return urls[:4]


async def _parse_article(article, handle: str) -> Optional[dict]:
    """Turn one rendered <article> into a tweet dict, or None if it isn't one.

    Returns a dict with an extra private key `_dt` (a datetime) so the caller
    can do the range comparison without re-parsing.
    """
    time_el = await article.query_selector("time")
    if time_el is None:
        return None  # promoted / ad row has no <time>

    dt_attr = await time_el.get_attribute("datetime")
    if not dt_attr:
        return None
    try:
        dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    link = await time_el.evaluate(
        "e => { const a = e.closest('a'); return a ? a.href : null; }"
    )
    if not link:
        return None
    tid = link.rsplit("/", 1)[-1].split("?")[0]
    if not tid.isdigit():
        return None

    ctx = await _context_label(article)
    pinned = ctx.startswith("pinned")
    is_repost = (not pinned) and ("repost" in ctx or "retweet" in ctx)

    text_el = await article.query_selector('[data-testid="tweetText"]')
    text = (await text_el.inner_text()) if text_el else ""

    return {
        "id": tid,
        "datetime": dt.astimezone(timezone.utc).isoformat(),
        "text": text,
        # The permalink the <time> element points at - canonical, and correct
        # for reposts (where it belongs to the original author, not `handle`).
        "url": link.split("?")[0],
        "author": "@" + handle,
        "account": handle,
        # Presentation extras: enough to render the post the way X does.
        "display_name": await _display_name(article, handle),
        "avatar": await _avatar(article),
        "verified": await _verified(article),
        "media": await _media(article),
        "pinned": pinned,
        "is_repost": is_repost,
        "metrics": {
            "replies": await _metric(article, "reply", "repl"),
            "reposts": await _metric(article, "retweet", "repost"),
            "likes": await _metric(article, "like", "like"),
            "bookmarks": await _metric(article, "bookmark", "bookmark"),
            "views": await _metric(article, "analytics", "view"),
        },
        "_dt": dt,
    }


async def harvest(page, tweets: dict, start: datetime, end: datetime,
                  handle: str, limit: int = 0,
                  include_retweets: bool = False,
                  stats: Optional[dict] = None) -> str:
    """Collect the currently rendered posts into `tweets`.

    Returns one of:
      "continue"    keep scrolling
      "past_start"  we have scrolled past the start of the range - stop
      "limit"       the per-account cap is reached - stop
    """
    stats = stats if stats is not None else {}
    old_streak = 0

    for article in await page.query_selector_all(TWEET_SEL):
        if limit and len(tweets) >= limit:
            return "limit"

        item = await _parse_article(article, handle)
        if item is None:
            stats["skipped"] = stats.get("skipped", 0) + 1
            continue

        dt = item.pop("_dt")
        pinned = item["pinned"]

        # A pinned post sits at the top out of order; if we treated its old
        # timestamp as "we scrolled past the start" we would stop immediately.
        # We also want a short run of old posts before believing we are done:
        # X's "Latest" tab is recency-weighted, not strictly ordered, so a
        # single stray old post must not end the scrape.
        if dt < start and not pinned:
            old_streak += 1
            if old_streak >= 5:
                stats["past_start"] = True
            continue

        if not (start <= dt <= end):
            continue

        old_streak = 0

        if item["is_repost"] and not include_retweets:
            stats["reposts"] = stats.get("reposts", 0) + 1
            continue

        if item["id"] in tweets:
            continue

        tweets[item["id"]] = item
        if limit and len(tweets) >= limit:
            return "limit"

    return "past_start" if stats.get("past_start") else "continue"


async def _page_text(page, limit: int = 6000) -> str:
    try:
        body = await page.inner_text("body")
    except Exception:
        return ""
    # X uses typographic quotes; normalise so the markers below match.
    return (
        body[:limit]
        .replace("\u2019", "'").replace("\u2018", "'")
        .replace("\u201c", '"').replace("\u201d", '"')
        .lower()
    )


async def detect_wall(page) -> Optional[tuple[str, str]]:
    """What is this page, if it isn't a timeline? -> (kind, human label)."""
    try:
        if await page.query_selector('[data-testid="loginButton"]'):
            return ("session", "a login wall - the saved X session has expired")
    except Exception:
        pass

    try:
        url = page.url or ""
    except Exception:
        url = ""
    if "knowledge_check" in url:
        return ("session",
                "X's 'Confirm your account' challenge (session needs a refresh)")

    body = await _page_text(page)
    if not body:
        return None

    markers = [
        ("these posts are protected", "protected", "the account is protected / private"),
        ("this account's posts are protected", "protected", "the account is protected / private"),
        ("account has been suspended", "missing", "the account is suspended"),
        ("account suspended", "missing", "the account is suspended"),
        ("this account doesn't exist", "missing", "this account does not exist (check the spelling)"),
        ("doesn't exist", "missing", "this account does not exist (check the spelling)"),
        ("no results for", "empty", "X returned no results for this search"),
        ("something went wrong. try reloading", "rate", "X's 'Something went wrong' wall (rate limiting)"),
        ("rate limit exceeded", "rate", "X rate-limited the request"),
    ]
    for needle, kind, label in markers:
        if needle in body:
            return (kind, label)

    if "log in" in body and "sign up" in body:
        return ("session", "a login wall - the saved X session has expired")
    return None


# --------------------------------------------------------------------------- #
#  The extractor
# --------------------------------------------------------------------------- #

def _check_event_loop() -> None:
    """Playwright spawns a driver subprocess, which on Windows only a Proactor
    event loop can do. Fail with a clear message instead of a bare
    NotImplementedError deep inside asyncio."""
    if sys.platform != "win32":
        return
    loop_name = type(asyncio.get_running_loop()).__name__
    if "Proactor" not in loop_name:
        raise RuntimeError(
            f"Windows event loop is {loop_name}, which cannot spawn the "
            "Playwright driver subprocess. Start the backend with "
            "`python run.py` instead of `uvicorn app.main:app` "
            "(and do not use --reload)."
        )


def _launch_options(headless: bool) -> dict:
    args = list(LAUNCH_ARGS)
    if os.environ.get("XTIMELINE_NO_SANDBOX", "").lower() in ("1", "true", "yes"):
        args.append("--no-sandbox")
    opts: dict = {
        "headless": headless,
        "args": args,
        "viewport": {"width": 1280, "height": 900},
        "user_agent": user_agent(),
        "locale": "en-US",
    }
    # Full Chromium's "new headless" mode is far harder to fingerprint than the
    # headless shell, so prefer it when the browser is installed. _new_context
    # falls back to the shell if this channel is missing.
    if headless and os.environ.get("XTIMELINE_NO_CHANNEL", "").lower() not in (
        "1", "true", "yes"
    ):
        opts["channel"] = "chromium"

    exe = os.environ.get("XTIMELINE_CHROME_PATH")
    if exe:
        opts["executable_path"] = exe
        opts.pop("channel", None)      # an explicit binary wins
    return opts


class Extractor:
    """One browser for a whole job, one page per account.

    Launching Chromium per account is slow and looks robotic, so the job opens
    a single context and reuses it. Use as an async context manager:

        async with Extractor(headless=True) as ex:
            result = await ex.scrape("nasa", start, end, "search")
    """

    def __init__(self, *, headless: bool = True, max_scrolls: int = 60,
                 limit: int = 0, include_retweets: bool = False,
                 cancel_check: Optional[Callable[[], bool]] = None,
                 scroll_delay_ms: Optional[int] = None,
                 nav_timeout_ms: Optional[int] = None,
                 wait_tweets_ms: Optional[int] = None):
        self.headless = headless
        self.max_scrolls = max_scrolls
        self.limit = limit
        self.include_retweets = include_retweets
        self.cancel_check = cancel_check
        self.scroll_delay_ms = scroll_delay_ms or SCROLL_DELAY_MS
        self.nav_timeout_ms = nav_timeout_ms or NAV_TIMEOUT_MS
        self.wait_tweets_ms = wait_tweets_ms or WAIT_TWEETS_MS
        self._pw = None
        self._browser = None
        self.context = None
        self.job_id = ""     # set by the caller, used to name debug shots

    # -- lifecycle ---------------------------------------------------------- #

    async def __aenter__(self) -> "Extractor":
        from playwright.async_api import async_playwright  # imported lazily

        _check_event_loop()

        if not STATE_FILE.exists() and not PROFILE_DIR.exists():
            raise SessionMissing(
                f"No saved X session at {STATE_FILE}. Run "
                "'python save_session.py' once, log into X, then retry."
            )

        self._pw = await async_playwright().start()
        try:
            self.context = await self._new_context()
            await self.context.add_init_script(STEALTH_JS)
        except Exception:
            await self._pw.stop()
            self._pw = None
            raise
        return self

    async def __aexit__(self, *exc_info) -> bool:
        try:
            if self.context is not None:
                await self.context.close()
            if self._browser is not None:
                await self._browser.close()
        except Exception as exc:  # pragma: no cover - best effort teardown
            log.debug("closing browser: %s", exc)
        finally:
            self.context = None
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
        return False

    async def _new_context(self):
        """Open the browser, preferring the profile save_session.py created.

        Tries the full-Chromium channel first (new headless mode); if that
        build is not installed, retries with Playwright's bundled shell so a
        machine without it still runs - just more visibly automated.
        """
        opts = _launch_options(self.headless)
        try:
            return await self._open(opts)
        except Exception as exc:
            if "channel" not in opts:
                raise
            log.warning(
                "full Chromium unavailable (%s) - falling back to the bundled "
                "headless shell; X may serve it an empty timeline", exc,
            )
            return await self._open({k: v for k, v in opts.items() if k != "channel"})

    async def _open(self, opts: dict):
        if PROFILE_DIR.exists():
            try:
                return await self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR), **opts
                )
            except Exception as exc:
                if not STATE_FILE.exists():
                    raise
                log.warning(
                    "persistent profile unavailable (%s) - falling back to "
                    "x_state.json", exc,
                )
        launch_opts = {k: v for k, v in opts.items()
                       if k not in ("viewport", "user_agent", "locale")}
        self._browser = await self._pw.chromium.launch(**launch_opts)
        return await self._browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=opts["viewport"],
            user_agent=opts.get("user_agent"),
            locale=opts.get("locale"),
        )

    # -- one account -------------------------------------------------------- #

    async def scrape(self, handle: str, start: datetime, end: datetime,
                     strategy: str = "search",
                     on_progress: Optional[Callable[[float, str], None]] = None,
                     ) -> ScrapeResult:
        plan: list[tuple[str, str]] = []
        if strategy in ("search", "auto"):
            plan.append(("search", search_url(handle, start, end)))
        if strategy in ("profile", "auto"):
            plan.append(("profile", profile_url(handle)))
        if not plan:
            raise ValueError(f"unknown strategy {strategy!r}")

        empty_note = ""
        for label, url in plan:
            if self.cancel_check and self.cancel_check():
                return ScrapeResult([], "cancelled", label)
            result = await self._run(handle, label, url, start, end, on_progress)
            if result.tweets:
                return result
            empty_note = result.note or empty_note
            log.info("@%s: %s produced nothing (%s)", handle, label, result.note)

        return ScrapeResult([], empty_note, plan[-1][0])

    async def _run(self, handle: str, label: str, url: str,
                   start: datetime, end: datetime,
                   on_progress: Optional[Callable[[float, str], None]]) -> ScrapeResult:
        assert self.context is not None, "Extractor used outside its context manager"

        page = await self.context.new_page()
        tweets: dict[str, dict] = {}
        stats: dict = {}
        note = ""
        try:
            await self._navigate(page, url)

            # Wait for the timeline to render instead of a blind sleep.
            try:
                await page.wait_for_selector(TWEET_SEL, timeout=self.wait_tweets_ms)
            except Exception:
                pass  # diagnosed below

            wall = await detect_wall(page)
            if wall:
                kind, text = wall
                shot = await self._debug(page, handle, label)
                if kind == "session":
                    raise SessionExpired(
                        f"@{handle}: {text}. Re-run save_session.py, then retry."
                    )
                if kind == "rate":
                    raise RateLimited(
                        f"@{handle}: {text}. Wait a few minutes and retry, or "
                        "raise XTIMELINE_SCROLL_DELAY_MS."
                    )
                if kind == "empty":
                    return ScrapeResult([], text, label, shot)
                raise AccountUnavailable(f"@{handle}: {text}")

            if not await page.query_selector(TWEET_SEL):
                # Some pages only mount the list after a scroll event; one nudge
                # is cheap and rescues a slow render.
                try:
                    await self._scroll(page)
                    await page.wait_for_selector(
                        TWEET_SEL, timeout=min(6000, self.wait_tweets_ms)
                    )
                except Exception:
                    pass

            if not await page.query_selector(TWEET_SEL):
                shot = await self._debug(page, handle, label)
                try:
                    title = await page.title()
                except Exception:
                    title = ""
                why = (
                    "the page loaded but no posts were mounted - a headless "
                    "browser is often served an empty timeline, so retry with "
                    "'Show browser window' ticked"
                    if self.headless else
                    "the page loaded but no posts were mounted"
                )
                note = (
                    f"nothing rendered on the {label} page ({why}; "
                    f"title={title!r}, url={page.url})"
                )
                return ScrapeResult([], note, label, shot)

            stagnant = 0
            for scroll in range(self.max_scrolls):
                if self.cancel_check and self.cancel_check():
                    note = "cancelled"
                    break

                before = len(tweets)
                state = await harvest(
                    page, tweets, start, end, handle,
                    limit=self.limit,
                    include_retweets=self.include_retweets,
                    stats=stats,
                )

                if on_progress:
                    cap = f" of {self.limit}" if self.limit else ""
                    on_progress(
                        min(0.99, (scroll + 1) / self.max_scrolls),
                        f"{label} scroll {scroll + 1}/{self.max_scrolls} - "
                        f"{len(tweets)}{cap} posts in range",
                    )

                if state == "limit":
                    note = f"stopped at the {self.limit}-post limit"
                    break
                if state == "past_start":
                    note = "reached the start of the requested range"
                    break

                stagnant = stagnant + 1 if len(tweets) == before else 0
                if stagnant >= 8:
                    note = "no further posts loaded (end of the timeline)"
                    break

                await self._scroll(page)

                # X can throw its rate-limit wall mid-scroll.
                if (scroll + 1) % 5 == 0:
                    wall = await detect_wall(page)
                    if wall and wall[0] in ("rate", "session"):
                        note = wall[1]
                        if not tweets:
                            raise (RateLimited if wall[0] == "rate" else SessionExpired)(
                                f"@{handle}: {wall[1]}"
                            )
                        break

            if stats.get("reposts"):
                extra = f"{stats['reposts']} reposts skipped"
                note = f"{note} ({extra})" if note else extra
        finally:
            try:
                await page.close()
            except Exception:  # pragma: no cover
                pass

        ordered = sorted(tweets.values(), key=lambda t: t["datetime"], reverse=True)
        log.info("@%s via %s: %d posts in range (%s)",
                 handle, label, len(ordered), note or "no note")
        return ScrapeResult(ordered, note, label)

    # -- page plumbing ------------------------------------------------------ #

    async def _navigate(self, page, url: str) -> None:
        """A slow X load must not sink the job - we read whatever renders."""
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=self.nav_timeout_ms)
        except Exception as exc:
            log.warning("navigation to %s was slow/failed (%s) - continuing", url, exc)

    async def _scroll(self, page) -> None:
        # Randomised nudges keep us under X's rate-limit heuristics.
        try:
            await page.mouse.move(random.randint(320, 900), random.randint(240, 700))
            await page.mouse.wheel(0, random.randint(1500, 2600))
        except Exception:  # pragma: no cover - page may have navigated away
            pass
        await page.wait_for_timeout(self.scroll_delay_ms + random.randint(0, 700))

    async def _debug(self, page, handle: str, label: str) -> str:
        """Screenshot whatever the browser actually saw."""
        try:
            OUTPUT_DIR.mkdir(exist_ok=True)
            stem = f"debug_{self.job_id}_{handle}_{label}" if self.job_id else f"debug_{handle}_{label}"
            out = OUTPUT_DIR / f"{stem}.png"
            await page.screenshot(path=str(out))
            return str(out)
        except Exception as exc:  # pragma: no cover - best effort
            return f"(screenshot failed: {exc})"


# --------------------------------------------------------------------------- #
#  Session status (used by /api/session and check_session.py)
# --------------------------------------------------------------------------- #

def session_status() -> dict:
    """Cheap, no-browser answer to 'have we got a login to reuse?'."""
    from datetime import datetime as _dt

    saved = STATE_FILE.exists()
    age_min = None
    if saved:
        mtime = _dt.fromtimestamp(STATE_FILE.stat().st_mtime, tz=timezone.utc)
        age_min = int((_dt.now(timezone.utc) - mtime).total_seconds() // 60)
    return {
        "saved": saved,
        "profile": PROFILE_DIR.exists(),
        "state_file_age_minutes": age_min,
        "hint": (
            "" if saved or PROFILE_DIR.exists()
            else "No saved session - run `python save_session.py` in the backend folder."
        ),
    }


async def check_session(headless: bool = True) -> dict:
    """Open x.com/home with the saved session and report what we see."""
    if not STATE_FILE.exists() and not PROFILE_DIR.exists():
        raise SessionMissing("No saved session - run save_session.py first.")
    async with Extractor(headless=headless, max_scrolls=1) as ex:
        page = await ex.context.new_page()
        try:
            await ex._navigate(page, base_url() + "/home")
            try:
                await page.wait_for_selector(TWEET_SEL, timeout=WAIT_TWEETS_MS)
            except Exception:
                pass
            wall = await detect_wall(page)
            try:
                title = await page.title()
            except Exception:
                title = ""
            try:
                count = len(await page.query_selector_all(TWEET_SEL))
            except Exception:
                # The document was replaced mid-probe (a redirect to /login, for
                # instance): there is nothing on this page to count.
                count = 0
            shot = ""
            try:
                OUTPUT_DIR.mkdir(exist_ok=True)
                path = OUTPUT_DIR / "session_check.png"
                await page.screenshot(path=str(path))
                shot = str(path)
            except Exception:  # pragma: no cover - best effort
                pass
            return {
                "logged_in": wall is None and count > 0,
                "wall": wall[1] if wall else "",
                "tweets_on_page": count,
                "title": title,
                "url": page.url,
                "screenshot": shot,
            }
        finally:
            await page.close()


@asynccontextmanager
async def browser_session(headless: bool = True):
    """Small convenience wrapper for one-off scripts."""
    ex = Extractor(headless=headless, max_scrolls=1)
    async with ex:
        yield ex


# --------------------------------------------------------------------------- #
#  Does this account exist?
# --------------------------------------------------------------------------- #

# Phrases X uses on a profile that is gone. The apostrophe in "doesn't" is
# typographic, so everything is normalised before matching.
MISSING_MARKERS = (
    "doesn't exist",
    "does not exist",
    "account suspended",
    "try searching for another",
    "this account doesn",
)


def _normalise(text: str) -> str:
    return (text or "").replace("\u2019", "'").replace("\u2018", "'").lower()


async def classify_profile(page) -> str:
    """-> "ok" | "missing" | "unknown" for the profile page currently open.

    "missing" needs evidence. Anything we cannot read is "unknown", because
    calling a real account deleted is the worse mistake.
    """
    for selector in ('[data-testid="emptyState"]', '[data-testid="error-detail"]'):
        try:
            node = await page.query_selector(selector)
        except Exception:
            node = None
        if node:
            try:
                text = _normalise(await node.inner_text())
            except Exception:
                text = ""
            if any(marker in text for marker in MISSING_MARKERS):
                return "missing"

    body = _normalise(await _page_text(page, limit=4000))
    if any(marker in body for marker in MISSING_MARKERS):
        return "missing"
    if "log in" in body and "sign up" in body and len(body) < 400:
        return "unknown"                      # a login wall says nothing

    # Only the primary column counts: the left sidebar can carry a UserName for
    # the signed-in account, which would otherwise make every handle look real.
    try:
        if await page.query_selector('main [data-testid="UserName"]'):
            return "ok"
    except Exception:
        pass
    return "unknown"


async def check_accounts(handles: list[str], base: Optional[str] = None) -> dict:
    """Open each profile once and say whether the handle resolves."""
    base = (base or base_url()).rstrip("/")
    results: dict[str, str] = {}

    async with Extractor() as ex:
        context = await ex._new_context()
        page = await context.new_page()
        try:
            for handle in handles:
                try:
                    await page.goto(f"{base}/{handle}", wait_until="domcontentloaded",
                                    timeout=ex.nav_timeout_ms)
                    # The profile renders client-side; classify before it has
                    # mounted and every handle comes back "unknown".
                    try:
                        await page.wait_for_selector(
                            '[data-testid="UserName"], [data-testid="emptyState"]',
                            timeout=8000,
                        )
                    except Exception:
                        pass
                    results[handle] = await classify_profile(page)
                except Exception as exc:            # noqa: BLE001 - per handle
                    log.info("could not check @%s (%s)", handle, exc)
                    results[handle] = "unknown"
        finally:
            try:
                await context.close()
            except Exception:
                pass
    return results
