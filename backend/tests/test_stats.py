"""The archive-wide stats endpoint, and the account-existence check.

No browser and no MongoDB: live jobs are put in the in-memory store directly,
stored jobs come from a stub at the same seam main.py uses, and the profile
classifier is exercised against a local page.
"""

from datetime import datetime, timezone

import pytest

from app import main, scraper
from app.jobs import store


def _tweet(handle: str, day: str, likes: int, views: int) -> dict:
    return {
        "id": f"{handle}-{day}",
        "datetime": f"{day}T10:00:00+00:00",
        "text": "hello",
        "url": f"https://x.com/{handle}/status/1",
        "author": f"@{handle}",
        "account": handle,
        "pinned": False,
        "is_repost": False,
        "metrics": {"replies": 1, "reposts": 2, "likes": likes,
                    "bookmarks": 3, "views": views},
    }


def _live_job(handles, tweets, status="done"):
    job = store.create(handles, "search")
    job.status = status
    job.tweets = tweets
    job.collected = len(tweets)
    return job


@pytest.mark.asyncio
async def test_stats_counts_every_post_in_memory(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "store_db", None)
    today = datetime.now(timezone.utc).date().isoformat()
    _live_job(["nasa"], [_tweet("nasa", today, 10, 100), _tweet("nasa", today, 5, 50)])
    _live_job(["isro"], [_tweet("isro", today, 1, 10)], status="error")

    body = (await client.get("/api/stats", headers=auth_headers)).json()

    assert body["runs"] == 2
    assert body["posts"] == 3
    assert body["failed"] == 1
    nasa = next(a for a in body["accounts"] if a["handle"] == "nasa")
    assert nasa["posts"] == 2 and nasa["likes"] == 15 and nasa["views"] == 150
    assert body["daily"][-1]["total"] == 3


@pytest.mark.asyncio
async def test_stats_merges_stored_runs_without_double_counting(client, auth_headers,
                                                                monkeypatch):
    class Stub:
        host = "stub"
        db_name = "stub"
        uri = "mongodb://stub"

        def __init__(self):
            self.tweets = {}

        async def list_jobs(self, limit=1000):
            return [
                {"id": "old", "handles": ["nasa"], "status": "done"},
                {"id": "live1", "handles": ["nasa"], "status": "done"},  # also live
            ]

        async def get_tweets(self, job_id):
            return self.tweets.get(job_id, [])

        async def save_job(self, doc): ...
        async def save_tweets(self, job_id, tweets): ...

    stub = Stub()
    today = datetime.now(timezone.utc).date().isoformat()
    stub.tweets["old"] = [_tweet("nasa", today, 7, 70)]

    monkeypatch.setattr(main, "store_db", stub)
    live = _live_job(["nasa"], [_tweet("nasa", today, 3, 30)])
    live.id = "live1"

    body = (await client.get("/api/stats", headers=auth_headers)).json()

    # "old" from the archive plus "live1" from memory, counted once each
    assert body["runs"] == 2
    assert body["posts"] == 2
    nasa = next(a for a in body["accounts"] if a["handle"] == "nasa")
    assert nasa["runs"] == 2          # both runs asked for nasa


@pytest.mark.asyncio
async def test_stats_respects_the_day_window(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "store_db", None)
    today = datetime.now(timezone.utc).date().isoformat()
    _live_job(["nasa"], [_tweet("nasa", today, 1, 1), _tweet("nasa", "2020-01-01", 9, 9)])

    body = (await client.get("/api/stats?days=7", headers=auth_headers)).json()
    assert body["posts"] == 1


# --------------------------------------------------------------------------- #
#  Account check
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_account_check_normalises_handles(client, auth_headers, monkeypatch):
    seen = {}

    async def fake_check(handles):
        seen["handles"] = handles
        return {h: "ok" for h in handles}

    monkeypatch.setattr(scraper, "check_accounts", fake_check)

    body = (await client.post("/api/accounts/check", headers=auth_headers,
                              json={"handles": ["@nasa", "nasa", " isro "]}))
    assert body.status_code == 200
    assert seen["handles"] == ["nasa", "isro"]
    assert body.json()["results"]["nasa"] == "ok"


@pytest.mark.asyncio
async def test_account_check_surfaces_a_browser_failure(client, auth_headers, monkeypatch):
    async def boom(handles):
        raise RuntimeError("browser closed")

    monkeypatch.setattr(scraper, "check_accounts", boom)

    res = await client.post("/api/accounts/check", headers=auth_headers,
                            json={"handles": ["nasa"]})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_profile_classification(page):
    async def classify(html: str) -> str:
        await page.set_content(html)
        return await scraper.classify_profile(page)

    assert await classify(
        '<main><div data-testid="UserName">nasa</div></main>'
    ) == "ok"

    # A UserName outside the main column (the signed-in account in the sidebar)
    # must not make a deleted handle look real.
    assert await classify(
        '<nav><div data-testid="UserName">me</div></nav>'
        '<main><div data-testid="emptyState">This account doesn\u2019t exist</div></main>'
    ) == "missing"

    assert await classify(
        '<div data-testid="emptyState">This account doesn\'t exist</div>'
    ) == "missing"

    assert await classify(
        '<div data-testid="emptyState">Account suspended</div>'
    ) == "missing"

    # the exact wording on the page that fooled it: typographic apostrophe
    assert await classify(
        "<main><h2>This account doesn\u2019t exist</h2>"
        "<p>Try searching for another.</p></main>"
    ) == "missing"

    # No marker either way: we do not claim the account is gone.
    assert await classify("<div>something else entirely</div>") == "unknown"
