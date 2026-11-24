"""FastAPI app: auth, scrape jobs, SSE progress, results, and the built UI.

Everything under /api is protected except the login endpoint and a minimal
health probe, so the React app's protected routes have something real to gate
on: a 401 from here is what bounces the browser back to /login.
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, persistence, scraper
from .jobs import store
from .models import ScrapeRequest

ROOT_DIR = Path(__file__).resolve().parents[2]      # project root
BACKEND_DIR = ROOT_DIR / "backend"
OUTPUT_DIR = BACKEND_DIR / "output"
DIST_DIR = ROOT_DIR / "frontend" / "dist"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("xtimeline")

# Keep strong references to running tasks - otherwise the event loop may
# garbage-collect them mid-flight and the job hangs in "running" forever.
_tasks: set[asyncio.Task] = set()

# MongoDB write-through store. None means memory-only (no database, or
# XTIMELINE_STORE=memory); the app is fully usable either way.
store_db: Optional[persistence.MongoStore] = None


async def _persist(job) -> None:
    """Upsert the job (and, once it is finished, its posts) into MongoDB."""
    if store_db is None:
        return
    try:
        # Retried: a remote cluster can stall for a second and a lost write
        # would cost this run its place in History.
        await persistence.with_retry(lambda: store_db.save_job(job.to_doc()))
        if job.status in ("done", "cancelled", "error"):
            await persistence.with_retry(
                lambda: store_db.save_tweets(job.id, job.tweets)
            )
    except Exception as exc:  # never let a database hiccup sink a scrape
        log.warning("could not persist job %s - %s", job.id,
                    persistence.explain(exc, store_db.uri))


async def _stored_job(jid: str) -> Optional[dict]:
    if store_db is None:
        return None
    try:
        return await store_db.get_job(jid)
    except Exception as exc:
        log.warning("could not read job %s from MongoDB (%s)", jid, exc)
        return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if sys.platform == "win32":
        loop_name = type(asyncio.get_running_loop()).__name__
        if "Proactor" in loop_name:
            log.info("Windows Proactor event loop detected - Playwright can launch.")
        else:
            log.warning(
                "Event loop is %s. Playwright needs a Proactor loop on Windows; "
                "start the backend with `python run.py`, not `uvicorn ... --reload`.",
                loop_name,
            )
    if auth.using_default_password():
        log.warning(
            "the login password is the default '%s' - set XTIMELINE_PASSWORD "
            "in backend/.env before exposing this anywhere.",
            auth.DEFAULT_PASSWORD,
        )
    log.info("Playwright-only build. Frontend dist: %s", DIST_DIR if DIST_DIR.exists() else "(not built)")

    global store_db
    store_db = await persistence.open_store()

    yield

    if store_db is not None:
        await store_db.close()


app = FastAPI(title="X Timeline Extractor", version="0.3.0", lifespan=lifespan)

# The Vite dev server runs on a different port; allow it during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Auth
# --------------------------------------------------------------------------- #

public = APIRouter(prefix="/api")
protected = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])


class LoginRequest(BaseModel):
    password: str = ""


@public.post("/auth/login")
async def login(body: LoginRequest, request: Request):
    client = auth.client_ip(request)
    auth.throttle_check(client)
    if not auth.check_password(body.password):
        auth.note_failure(client)
        remaining = auth.remaining_attempts(client)
        raise HTTPException(
            status_code=401,
            detail=f"wrong password ({remaining} attempt(s) left before a short lockout)",
        )
    auth.clear_failures(client)
    token, exp = auth.issue_token()
    log.info("login ok from %s", client)
    return {"token": token, "token_type": "bearer", "expires_at": exp,
            "expires_in": max(0, exp - int(time.time()))}


@protected.get("/auth/me")
async def me(payload: dict = Depends(auth.require_auth)):
    return {
        "authenticated": True,
        "subject": payload.get("sub", "operator"),
        "expires_at": payload.get("exp"),
        "issued_at": payload.get("iat"),
    }


@public.post("/auth/logout", status_code=204)
async def logout():
    """Stateless: the token is signed, not stored, so the client just drops it."""
    return None


# --------------------------------------------------------------------------- #
#  The scrape job
# --------------------------------------------------------------------------- #

def _account_pause() -> float:
    """Jittered pause between accounts, in seconds.

    Read at call time (not import time) so tests and operators can tune it with
    XTIMELINE_ACCOUNT_PAUSE_MIN_MS / _MAX_MS; 0 disables it.
    """
    try:
        lo = int(os.environ.get("XTIMELINE_ACCOUNT_PAUSE_MIN_MS", "1500"))
        hi = int(os.environ.get("XTIMELINE_ACCOUNT_PAUSE_MAX_MS", "3500"))
    except ValueError:
        lo, hi = 1500, 3500
    if hi <= 0:
        return 0.0
    return random.uniform(min(lo, hi), hi) / 1000.0


async def _run_job(job, req: ScrapeRequest) -> None:
    """Run one Playwright job across every requested account, in order.

    A failure on one account (expired session, protected, rate-limited) is
    recorded against that account and the run continues with the rest, so a
    single bad handle can't sink the whole batch. One browser serves the whole
    job - launching Chromium per account is slow and looks robotic.
    """
    from datetime import datetime, timezone

    try:
        handles = req.handles
        total = len(handles)
        collected: list[dict] = []
        failures: list[str] = []
        notes: list[str] = []

        job.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        job.message = "launching browser"
        job.touch()

        extractor = scraper.Extractor(
            headless=not req.headed,
            max_scrolls=req.max_scrolls,
            limit=req.limit,
            include_retweets=req.include_retweets,
            cancel_check=lambda: job.cancelled,
        )
        extractor.job_id = job.id

        async with extractor as ex:
            for idx, handle in enumerate(handles):
                if job.cancelled:
                    break

                job.per_account[handle] = {"collected": 0, "status": "running"}
                job.message = f"({idx + 1}/{total}) opening @{handle}"
                job.touch()

                def on_progress(frac: float, msg: str, idx=idx, handle=handle):
                    job.progress = int(((idx + frac) / total) * 100)
                    job.message = f"({idx + 1}/{total}) @{handle} - {msg}"
                    job.touch()

                try:
                    result = await ex.scrape(
                        handle, req.start, req.end, req.strategy,
                        on_progress=on_progress,
                    )
                    collected.extend(result.tweets)
                    job.per_account[handle] = {
                        "collected": len(result.tweets),
                        # An account interrupted mid-scroll reports "cancelled"
                        # rather than "done" - it is partial, not finished.
                        "status": "cancelled" if (job.cancelled and result.note == "cancelled") else "done",
                        "strategy": result.strategy,
                        "note": result.note,
                        "debug": bool(result.debug_shot),
                    }
                    if result.note:
                        notes.append(f"@{handle}: {result.note}")
                except Exception as exc:  # noqa: BLE001 - per-account isolation
                    log.exception("account @%s failed in job %s", handle, job.id)
                    detail = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                    job.per_account[handle] = {
                        "collected": 0,
                        "status": "error",
                        "error": detail or type(exc).__name__,
                    }
                    failures.append(f"@{handle}: {detail or type(exc).__name__}")

                job.collected = len(collected)
                job.progress = int((idx + 1) / total * 100)
                job.touch()
                await _persist(job)

                # A short, jittered pause between accounts.
                pause = _account_pause()
                if idx < total - 1 and not job.cancelled and pause:
                    job.message = f"({idx + 1}/{total}) done - waiting before the next account"
                    job.touch()
                    await asyncio.sleep(pause)

        job.tweets = sorted(collected, key=lambda t: t["datetime"], reverse=True)
        job.collected = len(job.tweets)
        job.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if job.cancelled:
            job.status = "cancelled"
            job.progress = 100
            job.message = f"cancelled - {len(job.tweets)} posts kept"
        elif not job.tweets and failures:
            # Every account failed - surface the reasons as the job error.
            job.status = "error"
            job.error = "; ".join(failures)
            job.message = job.error
        else:
            job.status = "done"
            job.progress = 100
            job.message = (
                f"done - {len(job.tweets)} posts across {total} account(s)"
            )
            if failures:
                job.note = "some accounts failed - " + "; ".join(failures)
            elif notes:
                job.note = "; ".join(notes)

        out = OUTPUT_DIR / f"{job.id}.json"
        out.write_text(json.dumps(job.tweets, indent=2), encoding="utf-8")
    except asyncio.CancelledError:
        job.status = "error"
        job.message = "job was cancelled - did the server reload?"
        job.error = job.message
        raise
    except Exception as exc:  # noqa: BLE001 - last resort, surface to the UI
        # Print the FULL traceback to the uvicorn terminal, and make sure the UI
        # always gets a non-empty reason even for message-less exceptions.
        log.exception("scrape job %s failed", job.id)
        detail = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
        job.status = "error"
        job.error = detail
        job.message = detail or type(exc).__name__
        job.finished_at = job.finished_at or job.started_at
    finally:
        job.touch()
        await _persist(job)


@protected.post("/scrape")
async def start_scrape(req: ScrapeRequest):
    job = store.create(req.handles, req.strategy)
    job.status = "running"
    job.message = "queued"
    log.info(
        "starting job %s for %s (strategy=%s, limit=%s, range=%s..%s)",
        job.id, ", ".join(req.handles), req.strategy, req.limit,
        req.start.isoformat(), req.end.isoformat(),
    )
    await _persist(job)
    task = asyncio.create_task(_run_job(job, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id, "status": job.status}


@protected.get("/jobs")
async def list_jobs():
    """Live jobs plus everything MongoDB remembers, newest first."""
    live = {j.id: j.summary() for j in store.all()}
    stored: list[dict] = []
    if store_db is not None:
        try:
            stored = await store_db.list_jobs(limit=200)
        except Exception as exc:
            log.warning("could not read job history (%s)", exc)

    merged: dict[str, dict] = {doc["id"]: doc for doc in stored}
    for jid, snap in live.items():
        merged[jid] = {**merged.get(jid, {}), **snap}

    ordered = sorted(
        merged.values(),
        key=lambda d: (d.get("created_at") or "", d.get("seq") or 0),
        reverse=True,
    )
    return {"jobs": ordered[:100]}


@protected.get("/jobs/{jid}")
async def get_job(jid: str):
    job = store.get(jid)
    if job:
        return job.snapshot()
    doc = await _stored_job(jid)
    if doc:
        return doc
    raise HTTPException(status_code=404, detail="job not found")


@protected.post("/jobs/{jid}/cancel")
async def cancel_job(jid: str):
    job = store.get(jid)
    if not job:
        # Only a live job can be cancelled; a stored one has already finished.
        if await _stored_job(jid):
            raise HTTPException(status_code=409, detail="job already finished")
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in ("done", "error", "cancelled"):
        raise HTTPException(status_code=409, detail=f"job already {job.status}")
    job.cancelled = True
    job.message = "cancelling - finishing the current scroll"
    job.touch()
    return {"ok": True, "status": job.status}


@protected.get("/jobs/{jid}/result")
async def get_result(jid: str):
    job = store.get(jid)
    if job:
        return {"status": job.status, "tweets": job.tweets, "note": job.note}

    doc = await _stored_job(jid)
    if doc:
        tweets = await store_db.get_tweets(jid)
        return {"status": doc.get("status", "done"), "tweets": tweets,
                "note": doc.get("note", "")}
    raise HTTPException(status_code=404, detail="job not found")


@protected.get("/jobs/{jid}/download")
async def download(jid: str):
    job = store.get(jid)
    path = OUTPUT_DIR / f"{jid}.json"

    if job:
        if job.status not in ("done", "cancelled") or not path.exists():
            raise HTTPException(status_code=404, detail="result not ready")
        safe = "".join(c if c.isalnum() else "_" for c in job.handle)[:60] or "result"
        return FileResponse(path, media_type="application/json",
                            filename=f"{safe}_{jid}.json")

    # A job from a previous run: rebuild the file from MongoDB, so the download
    # works even after output/ has been cleared.
    doc = await _stored_job(jid)
    if not doc:
        raise HTTPException(status_code=404, detail="job not found")
    tweets = await store_db.get_tweets(jid)
    handles = doc.get("handle") or doc.get("id")
    safe = "".join(c if c.isalnum() else "_" for c in str(handles))[:60] or "result"
    return JSONResponse(
        tweets,
        headers={"Content-Disposition": f'attachment; filename="{safe}_{jid}.json"'},
    )


@protected.get("/jobs/{jid}/debug")
async def debug_shot(jid: str, handle: Optional[str] = None):
    """The screenshot of whatever the browser actually saw."""
    if not store.get(jid) and not await _stored_job(jid):
        raise HTTPException(status_code=404, detail="job not found")
    candidates = []
    if handle:
        candidates += sorted(OUTPUT_DIR.glob(f"debug_{jid}_{handle}_*.png"))
    candidates.append(OUTPUT_DIR / f"debug_{jid}.png")
    for path in candidates:
        if path.exists():
            return FileResponse(path, media_type="image/png",
                                filename=path.name)
    raise HTTPException(status_code=404, detail="no debug screenshot for this job")


@protected.get("/jobs/{jid}/events")
async def events(jid: str):
    """Server-Sent Events: push a progress snapshot whenever it changes."""
    job = store.get(jid)
    if not job:
        # A finished job from a previous run: one frame, then close.
        doc = await _stored_job(jid)
        if not doc:
            raise HTTPException(status_code=404, detail="job not found")

        async def once():
            yield f"data: {json.dumps(doc)}\n\n"
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(once(), media_type="text/event-stream")

    async def stream():
        last = None
        while True:
            snap = job.snapshot()
            if snap != last:
                yield f"data: {json.dumps(snap)}\n\n"
                last = snap
            if job.status in ("done", "error", "cancelled"):
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
#  Session / health
# --------------------------------------------------------------------------- #

@protected.get("/session")
async def session():
    return scraper.session_status()


class AccountCheckRequest(BaseModel):
    handles: list[str] = Field(default_factory=list)


@protected.get("/stats")
async def stats(days: int = 0):
    """Aggregate the whole archive - live jobs and everything MongoDB holds.

    The dashboard used to add this up in the browser from the twenty runs it
    could be bothered to fetch, which made every count a "in view" number. This
    is the honest version: every run, every post, memory and stored alike.
    """
    live = {job.id: job for job in store.all()}
    stored: list[dict] = []
    if store_db is not None:
        try:
            stored = await store_db.list_jobs(limit=1000)
        except Exception as exc:
            log.warning("stats: could not read history (%s)", exc)

    cutoff = None
    if days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    accounts: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    runs = failed = total = 0

    def bucket(handle: str) -> dict:
        return accounts.setdefault(
            handle, {"handle": handle, "posts": 0, "likes": 0, "views": 0,
                     "runs": 0, "last": None},
        )

    def add_posts(tweets, fallback_handle: str) -> None:
        nonlocal total
        for tweet in tweets or []:
            at = tweet.get("datetime") or ""
            if cutoff is not None:
                try:
                    if datetime.fromisoformat(at.replace("Z", "+00:00")) < cutoff:
                        continue
                except ValueError:
                    continue
            metrics = tweet.get("metrics") or {}
            handle = tweet.get("account") or fallback_handle
            row = bucket(handle)
            row["posts"] += 1
            row["likes"] += metrics.get("likes") or 0
            row["views"] += metrics.get("views") or 0
            if at and (row["last"] is None or at > row["last"]):
                row["last"] = at

            day = at[:10]
            if day:
                entry = daily.setdefault(day, {"day": day, "total": 0, "byAccount": {}})
                entry["total"] += 1
                entry["byAccount"][handle] = entry["byAccount"].get(handle, 0) + 1
            total += 1

    for job in live.values():
        runs += 1
        if job.status == "error":
            failed += 1
        for handle in job.handles or []:
            bucket(handle)["runs"] += 1
        add_posts(job.tweets, (job.handles or ["unknown"])[0])

    for doc in stored:
        if doc.get("id") in live:
            continue                      # the live copy is newer
        runs += 1
        if doc.get("status") == "error":
            failed += 1
        for handle in doc.get("handles") or []:
            bucket(handle)["runs"] += 1
        if store_db is not None:
            try:
                add_posts(await store_db.get_tweets(doc["id"]),
                          (doc.get("handles") or ["unknown"])[0])
            except Exception as exc:
                log.warning("stats: could not read posts for %s (%s)", doc.get("id"), exc)

    return {
        "days": days,
        "runs": runs,
        "posts": total,
        "failed": failed,
        "accounts": sorted(accounts.values(),
                           key=lambda a: (-a["posts"], -a["runs"], a["handle"])),
        "daily": [daily[k] for k in sorted(daily)],
        "scanned": {"live": len(live), "stored": len(stored)},
    }


@protected.post("/accounts/check")
async def accounts_check(body: AccountCheckRequest):
    """Do these handles exist? One browser, one profile each."""
    handles = []
    for raw in body.handles:
        handle = (raw or "").strip().lstrip("@")
        if handle and handle not in handles:
            handles.append(handle)
    if not handles:
        return {"results": {}}
    if len(handles) > 25:
        raise HTTPException(status_code=422, detail="check at most 25 handles at once")

    try:
        results = await scraper.check_accounts(handles)
    except Exception as exc:                      # noqa: BLE001 - surface to the UI
        log.warning("account check failed: %s", exc)
        raise HTTPException(status_code=503,
                            detail=f"could not check accounts: {exc}") from exc
    return {"results": results}


@public.get("/health")
async def health():
    return {
        "ok": True,
        "version": app.version,
        "auth_required": True,
        "session_saved": scraper.STATE_FILE.exists(),
        "extractor": "playwright",
        "store": "mongo" if store_db is not None else "memory",
        "store_host": getattr(store_db, "host", "") if store_db else "",
        "store_db": getattr(store_db, "db_name", "") if store_db else "",
    }


app.include_router(public)
app.include_router(protected)


# --------------------------------------------------------------------------- #
#  The built React app (single origin, so protected routes work on refresh)
# --------------------------------------------------------------------------- #

if DIST_DIR.exists():
    assets = DIST_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """Serve index.html for client-side routes like /history or /jobs/abc.

        StaticFiles(html=True) would 404 those, which breaks a deep link or a
        page refresh on a protected route.
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        if full_path:
            candidate = (DIST_DIR / full_path).resolve()
            if candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def no_frontend():
        """Opening :8000 before building is a common first-run stumble."""
        return HTMLResponse(
            "<body style='font-family:system-ui;background:#0b0f14;color:#e8eef5;"
            "padding:40px;line-height:1.6'>"
            "<h2>Frontend not built</h2>"
            "<p>The API is up. Build the UI once with "
            "<code>npm install &amp;&amp; npm run build</code> in <code>frontend/</code> "
            "and reload this page &mdash; or run <code>npm run dev</code> and use "
            "<a style='color:#1d9bf0' href='http://localhost:5173'>"
            "http://localhost:5173</a>.</p>"
            "<p>The API itself is at <a style='color:#1d9bf0' href='/docs'>/docs</a>."
            "</p></body>"
        )
