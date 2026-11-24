"""MongoDB persistence: document shapes, the store wrapper, and the API fallback.

No MongoDB server is needed. The store is exercised through a duck-typed fake
collection, and the API tests swap `main.store_db` for a stub, which is exactly
the seam the real client sits behind.
"""

import pytest

from app import main, persistence
from app.jobs import Job


# --------------------------------------------------------------------------- #
#  A tiny stand-in for pymongo's async collection API
# --------------------------------------------------------------------------- #

class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: d.get(key) or "", reverse=direction == -1)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def create_index(self, *args, **kwargs):
        return "index"

    async def update_one(self, flt, update, upsert=False):
        key = next(iter(flt.values()))
        doc = self.docs.get(key, {})
        doc.update(update.get("$set", {}))
        self.docs[key] = doc

    async def find_one(self, flt, projection=None):
        key = next(iter(flt.values()))
        return self.docs.get(key)

    def find(self, flt, projection=None):
        return FakeCursor(self.docs.values())

    async def delete_one(self, flt):
        key = next(iter(flt.values()))
        self.docs.pop(key, None)


class FakeDB:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeClient:
    def __init__(self):
        self.db = FakeDB()
        self.closed = False

    def __getitem__(self, name):
        return self.db

    async def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
#  Job <-> document
# --------------------------------------------------------------------------- #

def test_a_job_round_trips_through_a_document():
    job = Job(id="abc123", handles=["nasa", "isro"], strategy="search")
    job.status = "done"
    job.progress = 100
    job.collected = 7
    job.note = "reached the start of the range"
    job.per_account = {"nasa": {"collected": 4, "status": "done"}}

    doc = job.to_doc()
    assert doc["id"] == "abc123"
    assert doc["handles"] == ["nasa", "isro"]
    assert doc["posts"] == 0          # posts live in their own collection
    assert "tweets" not in doc

    back = Job.from_doc(doc)
    assert back.id == job.id
    assert back.status == "done"
    assert back.collected == 7
    assert back.note == job.note
    assert back.per_account == job.per_account


def test_a_partial_document_still_builds_a_job():
    """Old or hand-edited documents must not crash the history list."""
    job = Job.from_doc({"id": "x1"})
    assert job.id == "x1"
    assert job.status == "queued"
    assert job.handles == []


# --------------------------------------------------------------------------- #
#  The store wrapper
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_saving_a_job_upserts_by_id():
    store = persistence.MongoStore(FakeClient(), "xtimeline")
    await store.save_job({"id": "j1", "status": "running", "_id": "mongo-internal"})
    await store.save_job({"id": "j1", "status": "done"})

    doc = await store.get_job("j1")
    assert doc["status"] == "done"
    assert "_id" not in doc


@pytest.mark.asyncio
async def test_posts_are_stored_per_job():
    store = persistence.MongoStore(FakeClient(), "xtimeline")
    await store.save_tweets("j1", [{"id": "1"}, {"id": "2"}])

    assert len(await store.get_tweets("j1")) == 2
    assert await store.get_tweets("nope") == []


@pytest.mark.asyncio
async def test_history_is_newest_first():
    store = persistence.MongoStore(FakeClient(), "xtimeline")
    await store.save_job({"id": "old", "created_at": "2026-01-01T00:00:00+00:00"})
    await store.save_job({"id": "new", "created_at": "2026-06-01T00:00:00+00:00"})

    docs = await store.list_jobs()
    assert [d["id"] for d in docs] == ["new", "old"]


@pytest.mark.asyncio
async def test_deleting_a_job_removes_its_posts_too():
    store = persistence.MongoStore(FakeClient(), "xtimeline")
    await store.save_job({"id": "j1"})
    await store.save_tweets("j1", [{"id": "1"}])

    await store.delete_job("j1")
    assert await store.get_job("j1") is None
    assert await store.get_tweets("j1") == []


@pytest.mark.asyncio
async def test_memory_mode_opens_no_store(monkeypatch):
    monkeypatch.setenv("XTIMELINE_STORE", "memory")
    assert await persistence.open_store() is None


def test_credentials_are_redacted_in_logs():
    assert persistence._redact("mongodb://user:pw@host:27017/db") == \
        "mongodb://***@host:27017/db"
    assert persistence._redact("mongodb://127.0.0.1:27017") == \
        "mongodb://127.0.0.1:27017"


# --------------------------------------------------------------------------- #
#  The API serving jobs it did not run
# --------------------------------------------------------------------------- #

class StubStore:
    """Stands in for MongoStore at the seam main.py uses."""

    def __init__(self, jobs=None, tweets=None):
        self.jobs = jobs or {}
        self.tweets = tweets or {}
        self.saved: list[dict] = []
        self.host = "stub-host"
        self.db_name = "stub-db"
        self.uri = "mongodb://stub"

    async def save_job(self, doc):
        self.saved.append(doc)

    async def save_tweets(self, job_id, tweets):
        self.tweets[job_id] = tweets

    async def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def get_tweets(self, job_id):
        return self.tweets.get(job_id, [])

    async def list_jobs(self, limit=100):
        return sorted(self.jobs.values(), key=lambda d: d.get("created_at") or "",
                      reverse=True)[:limit]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_a_stored_job_can_be_opened(client, auth_headers, monkeypatch):
    stored = {
        "id": "oldjob", "handle": "nasa", "handles": ["nasa"], "status": "done",
        "progress": 100, "collected": 2, "message": "done",
        "created_at": "2026-01-01T00:00:00+00:00", "note": "from the archive",
    }
    monkeypatch.setattr(main, "store_db",
                        StubStore({"oldjob": stored}, {"oldjob": [{"id": "1"}]}))

    snap = await client.get("/api/jobs/oldjob", headers=auth_headers)
    assert snap.status_code == 200
    assert snap.json()["status"] == "done"

    res = await client.get("/api/jobs/oldjob/result", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["tweets"] == [{"id": "1"}]
    assert res.json()["note"] == "from the archive"

    dl = await client.get("/api/jobs/oldjob/download", headers=auth_headers)
    assert dl.status_code == 200
    assert "oldjob" in dl.headers["content-disposition"]


@pytest.mark.asyncio
async def test_history_merges_stored_and_live_jobs(client, auth_headers, monkeypatch):
    stored = {"id": "oldjob", "status": "done", "created_at": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(main, "store_db", StubStore({"oldjob": stored}))

    # one live job, created now
    res = await client.post(
        "/api/scrape",
        headers=auth_headers,
        json={"handles": ["nasa"], "start": "2026-01-01T00:00:00Z",
              "end": "2026-01-02T00:00:00Z", "strategy": "search"},
    )
    assert res.status_code == 200, res.text

    listing = await client.get("/api/jobs", headers=auth_headers)
    ids = [j["id"] for j in listing.json()["jobs"]]
    assert "oldjob" in ids and res.json()["job_id"] in ids


@pytest.mark.asyncio
async def test_a_cancelled_stored_job_cannot_be_cancelled_again(client, auth_headers,
                                                                monkeypatch):
    monkeypatch.setattr(main, "store_db",
                        StubStore({"oldjob": {"id": "oldjob", "status": "done"}}))
    res = await client.post("/api/jobs/oldjob/cancel", headers=auth_headers)
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_health_reports_the_store(client, monkeypatch):
    monkeypatch.setattr(main, "store_db", None)
    assert (await client.get("/api/health")).json()["store"] == "memory"

    monkeypatch.setattr(main, "store_db", StubStore())
    assert (await client.get("/api/health")).json()["store"] == "mongo"


# --------------------------------------------------------------------------- #
#  Atlas-shaped configuration
# --------------------------------------------------------------------------- #

ATLAS_URI = "mongodb+srv://demo:pw@cluster0.abcd123.mongodb.net/xtimeline?retryWrites=true"


def test_a_remote_cluster_gets_a_longer_timeout(monkeypatch):
    monkeypatch.delenv("XTIMELINE_MONGO_URI", raising=False)
    assert persistence.server_timeout_ms() == 2000          # local mongod
    assert persistence.server_timeout_ms(ATLAS_URI) == 8000  # Atlas


def test_the_database_can_come_from_the_uri(monkeypatch):
    monkeypatch.delenv("XTIMELINE_MONGO_DB", raising=False)
    assert persistence.mongo_db_name(ATLAS_URI) == "xtimeline"
    assert persistence.mongo_db_name("mongodb://127.0.0.1:27017") == "xtimeline"
    assert persistence.mongo_db_name("mongodb://h:27017/other?x=1") == "other"

    monkeypatch.setenv("XTIMELINE_MONGO_DB", "explicit")
    assert persistence.mongo_db_name(ATLAS_URI) == "explicit"


def test_credentials_never_reach_the_health_endpoint():
    assert persistence._host_label(ATLAS_URI) == "cluster0.abcd123.mongodb.net"
    assert "demo" not in persistence._host_label(ATLAS_URI)


@pytest.mark.parametrize("message,expected", [
    ("OperationFailure: Authentication failed.", "database user or password"),
    ("ServerSelectionTimeoutError: timed out", "Network Access"),
    ("ConfigurationError: The resolution lifetime expired", "SRV/DNS lookup failed"),
    ("InvalidURI: must be a valid host", "malformed"),
])
def test_driver_errors_become_advice(message, expected):
    class Boom(Exception):
        pass

    exc = Boom(message)
    assert expected in persistence.explain(exc, ATLAS_URI)


def test_a_local_failure_mentions_mongod():
    class Boom(Exception):
        pass

    advice = persistence.explain(Boom("ServerSelectionTimeoutError: timed out"),
                                 "mongodb://127.0.0.1:27017")
    assert "mongod" in advice


@pytest.mark.asyncio
async def test_retry_recovers_from_a_transient_failure():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("connection reset")
        return "ok"

    assert await persistence.with_retry(flaky, attempts=3, base=0.01) == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_gives_up_and_reraises():
    async def always_fails():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await persistence.with_retry(always_fails, attempts=2, base=0.01)
