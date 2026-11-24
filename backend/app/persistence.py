"""MongoDB persistence for jobs and their posts.

The in-memory store stays the source of truth while a job is running - it is
what the SSE stream reads and what the browser task mutates. This module is the
write-through copy: every meaningful state change is upserted into MongoDB, so
the History section survives a restart and any past result can be reopened.

Two collections:

    jobs    one document per job (no posts), indexed by id
    tweets  one document per job, {job_id, tweets:[...]}

Posts live in their own collection because a 5-account run at the 5000-post cap
would otherwise push a single document toward MongoDB's 16 MB limit.

Works against a local mongod or MongoDB Atlas - the difference is only the
connection string:

    local   mongodb://127.0.0.1:27017
    atlas   mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/?retryWrites=true

Configuration:

    XTIMELINE_MONGO_URI   default mongodb://127.0.0.1:27017
    XTIMELINE_MONGO_DB    default: the database named in the URI, else xtimeline
    XTIMELINE_STORE       auto (default) | mongo | memory

`auto` tries MongoDB and falls back to memory-only with a warning, so the app
still runs on a machine that has no database. `mongo` makes a missing database
a startup error instead of a silent downgrade.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Optional

log = logging.getLogger("xtimeline.store")

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def mongo_uri() -> str:
    return os.environ.get("XTIMELINE_MONGO_URI", "mongodb://127.0.0.1:27017")


def store_mode() -> str:
    return os.environ.get("XTIMELINE_STORE", "auto").strip().lower() or "auto"


def _is_local(uri: str) -> bool:
    host_part = uri.split("://", 1)[-1].split("/", 1)[0]
    host = host_part.rsplit("@", 1)[-1].split(",")[0]
    host = host.split(":", 1)[0].strip("[]").lower()
    return host in LOCAL_HOSTS


def _db_from_uri(uri: str) -> str:
    """The database named in the connection string, if any.

    Parsed by hand rather than with pymongo's parser, because that one resolves
    SRV records - and we want to read the URI even when the network is down.
    """
    if "://" not in uri:
        return ""
    tail = uri.split("://", 1)[1].split("?", 1)[0]
    if "/" not in tail:
        return ""
    return tail.split("/", 1)[1].strip()


def mongo_db_name(uri: Optional[str] = None) -> str:
    return os.environ.get("XTIMELINE_MONGO_DB") or _db_from_uri(uri or mongo_uri()) or "xtimeline"


def server_timeout_ms(uri: Optional[str] = None) -> int:
    """Local servers answer instantly; a remote cluster needs patience.

    Too short a timeout against Atlas produces a ServerSelectionTimeout that
    looks like "wrong password" but is really "not yet connected".
    """
    return 2000 if _is_local(uri or mongo_uri()) else 8000


def explain(exc: Exception, uri: Optional[str] = None) -> str:
    """Turn a driver exception into the one thing the operator should check."""
    text = f"{type(exc).__name__}: {exc}".lower()
    remote = not _is_local(uri or mongo_uri())

    if "authentication failed" in text or "bad auth" in text or "code: 18" in text:
        return (
            "the database user or password was rejected - check Atlas > Database "
            "Access, and URL-encode any special characters in the password "
            "(@ becomes %40, : becomes %3A)"
        )
    if "srv" in text or "dns" in text or "resolution lifetime" in text \
            or "name or service not known" in text:
        return (
            "the SRV/DNS lookup failed - check the cluster hostname in the "
            "connection string, and that this machine can resolve DNS at all "
            "(a VPN or a locked-down network often blocks it)"
        )
    if "serverselectiontimeout" in text or "timed out" in text:
        if remote:
            return (
                "could not reach the cluster - on Atlas this is almost always "
                "Network Access: add your current IP, or 0.0.0.0/0 for a quick "
                "experiment. Also check the hostname matches Atlas > Connect > "
                "Drivers"
            )
        return "could not reach mongod on this machine - is the service running?"
    if "invaliduri" in text or "invalid uri" in text or "must be" in text:
        return (
            "the connection string is malformed - copy it again from Atlas > "
            "Connect > Drivers (it should start mongodb+srv://)"
        )
    if "ssl" in text or "tls" in text or "certificate" in text:
        return (
            "the TLS handshake failed - check the system clock and any corporate "
            "proxy or antivirus that inspects HTTPS"
        )
    if "not authorized" in text or "code: 13" in text:
        return "the database user is not authorised for this database"
    return "see the driver message above"


class MongoStore:
    """Thin async wrapper - the only place that knows Mongo's shape."""

    def __init__(self, client: Any, db_name: str, uri: str = "") -> None:
        self._client = client
        db = client[db_name]
        self.jobs = db["jobs"]
        self.tweets = db["tweets"]
        self.db_name = db_name
        self.uri = uri
        self.host = _host_label(uri)

    # -- lifecycle ---------------------------------------------------------

    async def ensure_indexes(self) -> None:
        await self.jobs.create_index("id", unique=True)
        await self.jobs.create_index([("created_at", -1)])
        await self.tweets.create_index("job_id", unique=True)

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- writes ------------------------------------------------------------

    async def save_job(self, doc: dict) -> None:
        """Upsert one job. `_id` is Mongo's; the app's key is `id`."""
        payload = {k: v for k, v in doc.items() if k != "_id"}
        await self.jobs.update_one({"id": payload["id"]}, {"$set": payload}, upsert=True)

    async def save_tweets(self, job_id: str, tweets: list[dict]) -> None:
        await self.tweets.update_one(
            {"job_id": job_id},
            {"$set": {"job_id": job_id, "tweets": tweets}},
            upsert=True,
        )

    # -- reads -------------------------------------------------------------

    async def get_job(self, job_id: str) -> Optional[dict]:
        return await self.jobs.find_one({"id": job_id}, {"_id": 0})

    async def get_tweets(self, job_id: str) -> list[dict]:
        doc = await self.tweets.find_one({"job_id": job_id}, {"_id": 0, "tweets": 1})
        return (doc or {}).get("tweets", []) or []

    async def list_jobs(self, limit: int = 100) -> list[dict]:
        cursor = self.jobs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def delete_job(self, job_id: str) -> None:
        await self.jobs.delete_one({"id": job_id})
        await self.tweets.delete_one({"job_id": job_id})

    # -- diagnostics (used by check_mongo.py) -------------------------------

    async def ping(self) -> dict:
        info = await self._client.server_info()
        return {
            "version": info.get("version", "?"),
            "host": self.host,
            "db": self.db_name,
        }

    async def counts(self) -> dict:
        jobs = await self.jobs.count_documents({})
        posts = sum(
            len(doc.get("tweets") or [])
            for doc in await self.tweets.find({}, {"tweets": 1}).to_list(length=None)
        )
        return {"jobs": jobs, "posts": posts}


async def with_retry(fn: Callable[[], Any], attempts: int = 3, base: float = 0.4):
    """Retry a database call a couple of times before giving up.

    Atlas drops idle connections and occasionally stalls; a single failure in a
    long scrape should not cost that run its history.
    """
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(base * (2 ** attempt))
    assert last is not None
    raise last


async def open_store() -> Optional[MongoStore]:
    """Return a connected MongoStore, or None for memory-only mode."""
    mode = store_mode()
    if mode == "memory":
        log.info("XTIMELINE_STORE=memory - history will not survive a restart.")
        return None

    uri = mongo_uri()
    db_name = mongo_db_name(uri)
    try:
        from pymongo import AsyncMongoClient  # pymongo >= 4.9
    except ImportError as exc:
        hint = (
            "pymongo is not installed - pip install 'pymongo>=4.9'"
            if "No module named" in str(exc)
            else "pymongo is too old - pip install -U 'pymongo>=4.9'"
        )
        if mode == "mongo":
            raise RuntimeError(hint) from exc
        log.warning("%s - history will be memory-only.", hint)
        return None

    try:
        client = AsyncMongoClient(
            uri,
            serverSelectionTimeoutMS=server_timeout_ms(uri),
            appname="xtimeline-extractor",
        )
        await client.admin.command("ping")
        store = MongoStore(client, db_name, uri)
        await store.ensure_indexes()
        log.info("MongoDB connected: %s (db=%s) - history persists.",
                 _redact(uri), store.db_name)
        return store
    except Exception as exc:
        if mode == "mongo":
            raise
        log.warning(
            "MongoDB unavailable at %s - %s. History will be memory-only.",
            _redact(uri), explain(exc, uri),
        )
        return None


def _redact(uri: str) -> str:
    """Never log credentials embedded in a connection string."""
    if "@" not in uri:
        return uri
    head, tail = uri.rsplit("@", 1)
    scheme = head.split("://", 1)[0] if "://" in head else ""
    return f"{scheme}://***@{tail}"


def _host_label(uri: str) -> str:
    """Just the host part, for /api/health - never the credentials."""
    if "://" not in uri:
        return uri
    host = uri.split("://", 1)[1].split("/", 1)[0]
    return host.rsplit("@", 1)[-1]
