"""A tiny in-memory job store.

A scrape takes minutes, so the API hands back a job id immediately and the work
runs in the background. Swap this for Redis / SQLite if you need jobs to
survive a restart or to run more than one worker.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    handles: list[str]                 # the accounts requested for this job
    strategy: str = "search"
    seq: int = 0              # monotonic creation order (created_at is 1s-granular)
    status: str = "queued"
    progress: int = 0
    collected: int = 0
    message: str = ""
    tweets: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    note: str = ""            # extra diagnosis (e.g. why 0 tweets)
    cancelled: bool = False
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # per-account tallies for the UI: {handle: {"collected": n, "status": ...}}
    per_account: dict[str, dict[str, Any]] = field(default_factory=dict)
    # bumped whenever progress changes, so the SSE stream can wake up
    updated: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def handle(self) -> str:
        """Back-compat display string used across the API and file names."""
        return ", ".join(self.handles)

    def touch(self) -> None:
        self.updated.set()
        self.updated.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handle": self.handle,
            "handles": self.handles,
            "status": self.status,
            "progress": self.progress,
            "collected": self.collected,
            "message": self.message,
            "strategy": self.strategy,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "note": self.note,
            "per_account": self.per_account,
        }

    def summary(self) -> dict[str, Any]:
        """Lighter shape for the history list (no per-account detail)."""
        snap = self.snapshot()
        snap.pop("per_account", None)
        return snap

    # -- persistence ------------------------------------------------------- #

    def to_doc(self) -> dict[str, Any]:
        """The MongoDB document for this job. Posts are stored separately."""
        doc = self.snapshot()
        doc["seq"] = self.seq
        doc["cancelled"] = self.cancelled
        doc["posts"] = len(self.tweets)
        return doc

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "Job":
        """Rebuild a Job from a stored document (posts loaded separately)."""
        job = cls(
            id=doc["id"],
            handles=list(doc.get("handles") or []),
            strategy=doc.get("strategy") or "search",
        )
        for field in ("seq", "status", "progress", "collected", "message",
                      "error", "note", "created_at", "started_at", "finished_at"):
            if doc.get(field) is not None:
                setattr(job, field, doc[field])
        job.per_account = doc.get("per_account") or {}
        job.cancelled = bool(doc.get("cancelled"))
        return job


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._seq = 0

    def create(self, handles: list[str], strategy: str = "search") -> Job:
        jid = uuid.uuid4().hex[:12]
        self._seq += 1
        job = Job(id=jid, handles=list(handles), strategy=strategy, seq=self._seq)
        self._jobs[jid] = job
        return job

    def get(self, jid: str) -> Optional[Job]:
        return self._jobs.get(jid)

    def all(self) -> list[Job]:
        """Newest first."""
        return sorted(self._jobs.values(), key=lambda j: j.seq, reverse=True)

    def clear(self) -> None:
        self._jobs.clear()


store = JobStore()
