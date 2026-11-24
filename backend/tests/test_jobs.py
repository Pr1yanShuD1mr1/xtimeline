"""Job lifecycle with the browser stubbed out.

These run the real request validation, job store, background task, progress
snapshot, per-account isolation, cancel and download code - only the
Playwright call itself is faked, so nothing here needs X or a login.
"""

import asyncio
import time

import pytest

from app import main, scraper

START = "2026-01-01T00:00:00Z"
END = "2026-01-31T23:59:59Z"


def _tweets(handle: str, n: int = 2) -> list[dict]:
    return [
        {
            "id": f"{handle}-{i}",
            "datetime": f"2026-01-{10 + i:02d}T09:00:00+00:00",
            "text": f"post {i} from {handle}",
            "url": f"https://x.com/{handle}/status/{handle}{i}",
            "author": f"@{handle}",
            "account": handle,
            "pinned": False,
            "is_repost": False,
            "metrics": {"replies": 1, "reposts": 2, "likes": 3, "bookmarks": 4},
        }
        for i in range(n)
    ]


class FakeExtractor:
    """Stands in for scraper.Extractor."""

    instances: list["FakeExtractor"] = []
    delay = 0.0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.job_id = ""
        FakeExtractor.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scrape(self, handle, start, end, strategy="search", on_progress=None):
        # Honour the job's cancel flag the way the real extractor does: stop
        # between scrolls and keep whatever was already collected.
        cancel = self.kwargs.get("cancel_check")
        for _ in range(max(1, int(self.delay / 0.02))):
            if cancel and cancel():
                return scraper.ScrapeResult([], "cancelled", "search")
            await asyncio.sleep(0.02 if self.delay else 0)
        if on_progress:
            on_progress(0.5, "halfway")
        if handle == "broken":
            raise RuntimeError("X is being difficult")
        if handle == "empty":
            return scraper.ScrapeResult([], "X returned no results for this search", "search")
        return scraper.ScrapeResult(_tweets(handle), "reached the start of the range", "search")


@pytest.fixture
def fake_browser(monkeypatch):
    FakeExtractor.instances = []
    FakeExtractor.delay = 0.0
    monkeypatch.setattr(main.scraper, "Extractor", FakeExtractor)
    return FakeExtractor


async def _wait(client, jid, headers, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = (await client.get(f"/api/jobs/{jid}", headers=headers)).json()
        if snap["status"] in ("done", "error", "cancelled"):
            return snap
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {jid} never finished")


async def _start(client, headers, **payload):
    body = {"handles": ["nasa"], "start": START, "end": END, **payload}
    res = await client.post("/api/scrape", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["job_id"]


async def test_multi_account_job_collects_and_tags_every_post(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["nasa", "isro", "esa"])
    snap = await _wait(client, jid, auth_headers)

    assert snap["status"] == "done"
    assert snap["progress"] == 100
    assert snap["collected"] == 6
    assert snap["handles"] == ["nasa", "isro", "esa"]
    assert {k: v["status"] for k, v in snap["per_account"].items()} == {
        "nasa": "done", "isro": "done", "esa": "done",
    }
    assert snap["started_at"] and snap["finished_at"]

    result = (await client.get(f"/api/jobs/{jid}/result", headers=auth_headers)).json()
    accounts = {t["account"] for t in result["tweets"]}
    assert accounts == {"nasa", "isro", "esa"}
    # newest first
    stamps = [t["datetime"] for t in result["tweets"]]
    assert stamps == sorted(stamps, reverse=True)


async def test_one_broken_account_does_not_sink_the_batch(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["nasa", "broken", "isro"])
    snap = await _wait(client, jid, auth_headers)

    assert snap["status"] == "done"
    assert snap["collected"] == 4
    assert snap["per_account"]["broken"]["status"] == "error"
    assert "X is being difficult" in snap["per_account"]["broken"]["error"]
    assert "broken" in snap["note"]


async def test_every_account_failing_is_an_error(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["broken"])
    snap = await _wait(client, jid, auth_headers)
    assert snap["status"] == "error"
    assert "X is being difficult" in snap["error"]


async def test_empty_account_is_not_an_error(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["empty", "nasa"])
    snap = await _wait(client, jid, auth_headers)
    assert snap["status"] == "done"
    assert snap["collected"] == 2
    assert snap["per_account"]["empty"]["status"] == "done"
    assert "no results" in snap["per_account"]["empty"]["note"]


async def test_progress_messages_are_per_account(client, auth_headers, fake_browser):
    FakeExtractor.delay = 0.05
    jid = await _start(client, auth_headers, handles=["nasa", "isro"])
    snap = await _wait(client, jid, auth_headers)
    assert "2 account(s)" in snap["message"]


async def test_download_returns_the_json_file(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["nasa"])
    await _wait(client, jid, auth_headers)
    res = await client.get(f"/api/jobs/{jid}/download", headers=auth_headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert b'"account": "nasa"' in res.content


async def test_cancel_stops_the_run_and_keeps_what_it_has(client, auth_headers, fake_browser):
    FakeExtractor.delay = 0.4
    jid = await _start(client, auth_headers, handles=["nasa", "isro", "esa"])

    # let the first account finish, then pull the plug
    for _ in range(100):
        snap = (await client.get(f"/api/jobs/{jid}", headers=auth_headers)).json()
        if snap["per_account"].get("nasa", {}).get("status") == "done":
            break
        await asyncio.sleep(0.02)

    res = await client.post(f"/api/jobs/{jid}/cancel", headers=auth_headers)
    assert res.status_code == 200

    snap = await _wait(client, jid, auth_headers)
    assert snap["status"] == "cancelled"
    assert snap["collected"] == 2                       # nasa's two posts
    assert snap["per_account"]["nasa"]["status"] == "done"
    assert snap["per_account"]["isro"]["status"] == "cancelled"
    assert "esa" not in snap["per_account"]             # never started


async def test_cancelling_a_finished_job_conflicts(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["nasa"])
    await _wait(client, jid, auth_headers)
    res = await client.post(f"/api/jobs/{jid}/cancel", headers=auth_headers)
    assert res.status_code == 409


async def test_history_lists_jobs_newest_first(client, auth_headers, fake_browser):
    first = await _start(client, auth_headers, handles=["nasa"])
    await _wait(client, first, auth_headers)
    second = await _start(client, auth_headers, handles=["isro"])
    await _wait(client, second, auth_headers)

    jobs = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"]
    assert [j["id"] for j in jobs][:2] == [second, first]
    assert jobs[0]["handles"] == ["isro"]
    assert "per_account" not in jobs[0]      # the summary stays light


async def test_unknown_job_is_404(client, auth_headers):
    assert (await client.get("/api/jobs/nope", headers=auth_headers)).status_code == 404
    assert (await client.get("/api/jobs/nope/result", headers=auth_headers)).status_code == 404


async def test_session_status_endpoint(client, auth_headers):
    body = (await client.get("/api/session", headers=auth_headers)).json()
    assert "saved" in body and "profile" in body


# --------------------------------------------------------------------------- #
#  Validation
# --------------------------------------------------------------------------- #

async def test_handles_accept_a_single_string(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles="nasa, @isro  ESA")
    snap = await _wait(client, jid, auth_headers)
    assert snap["handles"] == ["nasa", "isro", "ESA"]


async def test_duplicate_handles_are_deduped_case_insensitively(client, auth_headers, fake_browser):
    jid = await _start(client, auth_headers, handles=["NASA", "nasa", "Nasa"])
    snap = await _wait(client, jid, auth_headers)
    assert snap["handles"] == ["NASA"]


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"handles": []}, "at least one handle"),
        ({"handles": ["bad handle!"]}, "not a valid handle"),
        ({"handles": ["nasa"], "end": "2025-12-01T00:00:00Z"}, "end must be after start"),
        ({"handles": ["nasa"], "method": "twscrape"}, "Playwright only"),
        ({"handles": ["nasa"], "strategy": "api"}, "strategy"),
        ({"handles": ["nasa"], "limit": 99999}, "limit"),
    ],
)
async def test_bad_requests_are_422(client, auth_headers, payload, reason):
    body = {"start": START, "end": END, **payload}
    res = await client.post("/api/scrape", json=body, headers=auth_headers)
    assert res.status_code == 422
    assert reason.lower() in res.text.lower()
