"""The DOM harvest loop, driven against a local page that mimics X's markup.

This is the part that actually decides which posts count, so it gets real
tests: a real Chromium, real `<article>`/`<time>`/`aria-label` markup copied
from X's structure, and the real harvest() function. Only the network is fake.
"""

from datetime import datetime, timezone

import pytest

from app import scraper

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)


def article(tid: str, dt: str, *, text: str = "hello world",
            pinned: bool = False, context: str = "", handle: str = "nasa",
            replies: int = 4, reposts: int = 12, likes: int = 1234,
            bookmarks: int = 7, time_element: bool = True,
            display_name: str = "", avatar: str = "",
            media: tuple = (), verified: bool = False,
            views: int = 0) -> str:
    """One <article> shaped like X's, with the attributes we actually read."""
    ctx = context or ("Pinned" if pinned else "")
    ctx_html = f'<div data-testid="socialContext">{ctx}</div>' if ctx else ""
    time_html = (
        f'<a href="https://x.com/{handle}/status/{tid}?s=20">'
        f'<time datetime="{dt}">Jan 1</time></a>'
        if time_element else ""
    )
    name_html = f"<span>{display_name}</span>" if display_name else ""
    avatar_html = (
        f'<div data-testid="Tweet-User-Avatar"><img src="{avatar}"></div>'
        if avatar else ""
    )
    media_html = "".join(
        f'<div data-testid="tweetPhoto"><img src="{src}"></div>' for src in media
    )
    verified_html = '<svg data-testid="icon-verified"></svg>' if verified else ""
    views_html = (
        f'<button data-testid="analytics" aria-label="{views:,} views"></button>'
        if views else ""
    )
    return f"""
    <article data-testid="tweet" role="article">
      {ctx_html}
      {avatar_html}
      <div data-testid="User-Name">{name_html}<span>@{handle}</span></div>
      {verified_html}
      <div data-testid="tweetText">{text}</div>
      {media_html}
      {time_html}
      <div role="group">
        <button data-testid="reply" aria-label="{replies} replies"></button>
        <button data-testid="retweet" aria-label="{reposts} reposts"></button>
        <button data-testid="like" aria-label="{likes:,} likes"></button>
        <button data-testid="bookmark" aria-label="{bookmarks} bookmarks"></button>
        {views_html}
      </div>
    </article>
    """


def page_html(*articles: str) -> str:
    return "<html><body><main>" + "".join(articles) + "</main></body></html>"


async def _harvest(page, html, **kwargs):
    await page.set_content(page_html(html) if isinstance(html, str) else html)
    tweets: dict[str, dict] = {}
    stats: dict = {}
    state = await scraper.harvest(
        page, tweets, kwargs.pop("start", START), kwargs.pop("end", END),
        "nasa", stats=stats, **kwargs,
    )
    return state, tweets, stats


# --------------------------------------------------------------------------- #
#  Range filtering
# --------------------------------------------------------------------------- #

async def test_only_posts_inside_the_range_are_kept(page):
    html = (
        article("1", "2026-01-15T10:00:00.000Z", text="in range")
        + article("2", "2025-12-01T10:00:00.000Z", text="too old")
        + article("3", "2026-02-10T10:00:00.000Z", text="too new")
    )
    state, tweets, _ = await _harvest(page, html)

    assert list(tweets) == ["1"]
    assert tweets["1"]["text"] == "in range"
    assert tweets["1"]["datetime"] == "2026-01-15T10:00:00+00:00"
    assert tweets["1"]["account"] == "nasa"
    assert state == "continue"     # one stray old post is not "we are done"


async def test_a_run_of_old_posts_ends_the_scroll(page):
    html = (
        article("1", "2026-01-15T10:00:00.000Z")
        + "".join(article(str(i), "2025-11-01T10:00:00.000Z") for i in range(20, 26))
    )
    state, tweets, _ = await _harvest(page, html)
    assert list(tweets) == ["1"]
    assert state == "past_start"


async def test_pinned_old_post_neither_counts_nor_ends_the_scroll(page):
    """A pinned post sits at the top out of order - it must not trip the exit."""
    html = (
        article("9", "2025-01-01T10:00:00.000Z", pinned=True)
        + article("1", "2026-01-15T10:00:00.000Z")
        + article("2", "2026-01-16T10:00:00.000Z")
    )
    state, tweets, _ = await _harvest(page, html)
    assert sorted(tweets) == ["1", "2"]
    assert state == "continue"


async def test_pinned_in_range_post_is_kept_and_flagged(page):
    html = article("9", "2026-01-10T10:00:00.000Z", pinned=True)
    _, tweets, _ = await _harvest(page, html)
    assert tweets["9"]["pinned"] is True


async def test_boundaries_are_inclusive(page):
    html = (
        article("1", "2026-01-01T00:00:00.000Z")
        + article("2", "2026-01-31T23:59:59.000Z")
    )
    _, tweets, _ = await _harvest(page, html)
    assert sorted(tweets) == ["1", "2"]


# --------------------------------------------------------------------------- #
#  Reposts, ads, metrics
# --------------------------------------------------------------------------- #

async def test_reposts_of_other_people_are_dropped_by_default(page):
    html = (
        article("1", "2026-01-15T10:00:00.000Z")
        + article("2", "2026-01-16T10:00:00.000Z", context="Someone reposted")
    )
    _, tweets, stats = await _harvest(page, html)
    assert list(tweets) == ["1"]
    assert stats["reposts"] == 1


async def test_reposts_can_be_kept_on_request(page):
    html = article("2", "2026-01-16T10:00:00.000Z", context="Someone reposted")
    _, tweets, _ = await _harvest(page, html, include_retweets=True)
    assert tweets["2"]["is_repost"] is True


async def test_rows_without_a_time_element_are_skipped(page):
    html = (
        article("1", "2026-01-15T10:00:00.000Z")
        + article("ad", "", time_element=False)
    )
    _, tweets, stats = await _harvest(page, html)
    assert list(tweets) == ["1"]
    assert stats["skipped"] == 1


async def test_metrics_come_from_aria_labels_not_the_abbreviated_text(page):
    html = article("1", "2026-01-15T10:00:00.000Z", replies=42, reposts=10,
                   likes=1234, bookmarks=3)
    _, tweets, _ = await _harvest(page, html)
    assert tweets["1"]["metrics"] == {
        "replies": 42, "reposts": 10, "likes": 1234, "bookmarks": 3,
        "views": 0,          # absent from the markup, so zero rather than a guess
    }


async def test_the_url_is_the_permalink_without_query_string(page):
    html = article("1234567890", "2026-01-15T10:00:00.000Z")
    _, tweets, _ = await _harvest(page, html)
    assert tweets["1234567890"]["url"] == "https://x.com/nasa/status/1234567890"


async def test_the_same_post_seen_twice_is_kept_once(page):
    html = (
        article("1", "2026-01-15T10:00:00.000Z", text="first render")
        + article("1", "2026-01-15T10:00:00.000Z", text="recycled node")
    )
    _, tweets, _ = await _harvest(page, html)
    assert len(tweets) == 1
    assert tweets["1"]["text"] == "first render"


# --------------------------------------------------------------------------- #
#  The per-account cap
# --------------------------------------------------------------------------- #

async def test_limit_stops_harvesting(page):
    html = "".join(article(str(i), f"2026-01-{10 + i:02d}T10:00:00.000Z")
                   for i in range(5))
    state, tweets, _ = await _harvest(page, html, limit=2)
    assert state == "limit"
    assert len(tweets) == 2


# --------------------------------------------------------------------------- #
#  Walls: what the page says when it isn't a timeline
# --------------------------------------------------------------------------- #

async def test_login_wall_is_detected(page):
    await page.set_content(
        '<html><body><a data-testid="loginButton">Log in</a></body></html>'
    )
    assert (await scraper.detect_wall(page))[0] == "session"


async def test_protected_account_is_detected(page):
    await page.set_content(
        "<html><body><h2>These posts are protected</h2></body></html>"
    )
    kind, label = await scraper.detect_wall(page)
    assert kind == "protected"
    assert "protected" in label


async def test_rate_limit_wall_is_detected(page):
    await page.set_content(
        "<html><body><span>Something went wrong. Try reloading.</span></body></html>"
    )
    assert (await scraper.detect_wall(page))[0] == "rate"


async def test_missing_account_is_detected(page):
    await page.set_content(
        "<html><body><span>This account doesn&#39;t exist</span></body></html>"
    )
    assert (await scraper.detect_wall(page))[0] == "missing"


async def test_empty_search_is_detected(page):
    await page.set_content(
        "<html><body><span>No results for &#34;from:nasa since:2026-01-01&#34;</span></body></html>"
    )
    assert (await scraper.detect_wall(page))[0] == "empty"


async def test_a_normal_timeline_has_no_wall(page):
    await page.set_content(page_html(article("1", "2026-01-15T10:00:00.000Z")))
    assert await scraper.detect_wall(page) is None


# --------------------------------------------------------------------------- #
#  URLs
# --------------------------------------------------------------------------- #

def test_search_url_uses_x_operators_and_the_live_tab():
    url = scraper.search_url("nasa", START, END)
    assert url.startswith("https://x.com/search?q=")
    assert "from%3Anasa" in url
    assert "since%3A2026-01-01" in url
    # until: is exclusive, so we ask for the day after the last wanted day
    assert "until%3A2026-02-01" in url
    assert "f=live" in url


def test_profile_url_is_plain():
    assert scraper.profile_url("nasa") == "https://x.com/nasa"


def test_session_status_reports_a_missing_session(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "STATE_FILE", tmp_path / "x_state.json")
    monkeypatch.setattr(scraper, "PROFILE_DIR", tmp_path / ".x_profile")
    status = scraper.session_status()
    assert status["saved"] is False
    assert "save_session.py" in status["hint"]


# --------------------------------------------------------------------------- #
#  Presentation fields (so the feed can look like X)
# --------------------------------------------------------------------------- #

async def test_presentation_fields_are_captured(page):
    html = article(
        "9", "2026-01-10T10:00:00.000Z",
        display_name="freeCodeCamp.org",
        avatar="https://pbs.twimg.com/profile_images/1/avatar.jpg",
        media=("https://pbs.twimg.com/media/a.jpg",
               "https://pbs.twimg.com/media/b.jpg"),
        verified=True,
        views=6945,
    )
    _, tweets, _ = await _harvest(page, html)
    t = tweets["9"]

    assert t["display_name"] == "freeCodeCamp.org"
    assert t["avatar"].endswith("avatar.jpg")
    assert t["verified"] is True
    assert t["media"] == ["https://pbs.twimg.com/media/a.jpg",
                          "https://pbs.twimg.com/media/b.jpg"]
    assert t["metrics"]["views"] == 6945


async def test_a_plain_post_degrades_gracefully(page):
    """No avatar, no photo, no verified tick: still a valid post."""
    _, tweets, _ = await _harvest(page, article("10", "2026-01-10T10:00:00.000Z"))

    t = tweets["10"]
    assert t["display_name"] == "nasa"      # falls back to the handle
    assert t["avatar"] == ""
    assert t["media"] == []
    assert t["verified"] is False
    assert t["metrics"]["views"] == 0


async def test_media_is_capped_at_four(page):
    html = article("11", "2026-01-10T10:00:00.000Z",
                   media=tuple(f"https://pbs.twimg.com/media/{i}.jpg"
                               for i in range(6)))
    _, tweets, _ = await _harvest(page, html)
    assert len(tweets["11"]["media"]) == 4
