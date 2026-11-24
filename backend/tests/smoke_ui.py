"""End-to-end smoke test of the built UI: real browser, real backend, real SSE.

    python tests/smoke_ui.py          (from inside the backend folder)

It builds nothing: run `npm run build` in ../frontend first, so there is a
dist/ for the backend to serve. Then it starts uvicorn in a thread inside this
process - which lets it stub the Playwright extractor, the one piece that would
need a live X login - and drives the real bundle with a real browser.

What it proves, in order: an unauthenticated deep link is redirected to /login;
a wrong password is refused; a good one lands on the page you originally asked
for; the token is kept across a refresh; the extractor runs end to end over SSE;
history and the /jobs/<id> permalink work; a tampered token is bounced; sign-out
clears the token; and /api answers 401 without one.

Set XTIMELINE_CHROME_PATH if Playwright's own Chromium is not installed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
import threading
import time
import traceback
from pathlib import Path

os.environ["XTIMELINE_PASSWORD"] = "e2e-pass"
os.environ["XTIMELINE_SECRET"] = "e2e-secret"
os.environ["XTIMELINE_ACCOUNT_PAUSE_MAX_MS"] = "0"
if os.environ.get("XTIMELINE_NO_SANDBOX") is None and sys.platform.startswith("linux"):
    os.environ["XTIMELINE_NO_SANDBOX"] = "1"   # containers usually need it

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
if not (ROOT / "frontend" / "dist" / "index.html").exists():
    sys.exit("No frontend/dist - run `npm run build` in ../frontend first.")

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app import main as app_main, scraper  # noqa: E402

PORT = int(os.environ.get("SMOKE_PORT", "8123"))
BASE = f"http://127.0.0.1:{PORT}"
results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((ok, name if not detail else f"{name} — {detail}"))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))


class FakeExtractor:
    """Two posts per account, no X involved."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.job_id = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scrape(self, handle, start, end, strategy="search", on_progress=None):
        await asyncio.sleep(0.4)          # let the SSE stream push an update
        if on_progress:
            on_progress(0.5, "halfway there")
        pixel = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAY"
                 "AAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        posts = [
            {
                "id": f"{handle}{i}",
                "datetime": (datetime.now(timezone.utc)
                             - timedelta(days=i)).isoformat(),
                "text": f"post {i} from {handle}",
                "url": f"https://x.com/{handle}/status/{handle}{i}",
                "author": f"@{handle}",
                "account": handle,
                "display_name": f"{handle.title()} Inc",
                "avatar": pixel,
                "verified": True,
                "media": [pixel] if i == 0 else [],
                "pinned": False,
                "is_repost": False,
                "metrics": {"replies": 1, "reposts": 2, "likes": 30 + i,
                            "bookmarks": 4, "views": 6945},
            }
            for i in range(2)
        ]
        return scraper.ScrapeResult(posts, "reached the start of the range", strategy)


def start_server():
    config = uvicorn.Config(app_main.app, host="127.0.0.1", port=PORT,
                            log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


def main() -> int:
    app_main.scraper.Extractor = FakeExtractor

    async def fake_check(handles):
        out = {}
        for h in handles:
            if h.startswith("ghost"):
                out[h] = "missing"
            elif h.startswith("slow"):
                out[h] = "unknown"
            else:
                out[h] = "ok"
        return out

    app_main.scraper.check_accounts = fake_check

    server = start_server()
    try:
        with sync_playwright() as p:
            launch: dict = {"args": ["--no-sandbox"]}
            if os.environ.get("XTIMELINE_CHROME_PATH"):
                launch["executable_path"] = os.environ["XTIMELINE_CHROME_PATH"]
            browser = p.chromium.launch(**launch)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            # 1. an unauthenticated deep link is bounced to /login
            page.goto(f"{BASE}/history")
            page.wait_for_url("**/login", timeout=15000)
            check("protected route redirects to /login", page.url.endswith("/login"),
                  page.url)
            check("login form is shown",
                  page.locator("input[type=password]").is_visible())

            # 2. wrong password -> error, still on /login
            page.fill("input[type=password]", "nope")
            page.click("button[type=submit]")
            page.wait_for_selector(".login-error", timeout=10000)
            err = page.inner_text(".login-error")
            check("wrong password is rejected", "wrong password" in err.lower(), err)
            check("still on /login after a bad password",
                  page.url.endswith("/login"), page.url)

            # 3. right password -> lands on the page we originally asked for
            page.fill("input[type=password]", "e2e-pass")
            page.click("button[type=submit]")
            page.wait_for_url("**/history", timeout=15000)
            check("sign-in returns to the originally requested route",
                  page.url.endswith("/history"), page.url)
            token = page.evaluate("() => localStorage.getItem('xtimeline.token')")
            check("token is stored in localStorage", bool(token))

            # 4. the extractor itself
            page.click(".masthead-nav a[href='/']")
            page.wait_for_selector("#username-input")
            page.fill("#username-input", "nasa")
            page.press("#username-input", "Enter")
            page.fill("#username-input", "isro")
            page.press("#username-input", "Enter")
            rows = page.locator(".acct-line").all_inner_texts()
            # ---- account existence check --------------------------------
            page.fill("#username-input", "ghost_account")
            page.press("#username-input", "Enter")
            page.wait_for_selector(".acct-line .h.missing", timeout=15000)
            check("an unknown handle is struck through",
                  page.locator(".acct-line .h.missing").count() == 1)
            page.click(".acct-line:has(.h.missing) .x")
            page.wait_for_timeout(200)
            check("removing it clears the warning",
                  page.locator(".acct-line .h.missing").count() == 0)

            # "could not verify" must not read as verified
            page.fill("#username-input", "slow_account")
            page.press("#username-input", "Enter")
            page.wait_for_selector(".log-line", timeout=10000)
            page.wait_for_timeout(900)
            outbox = page.inner_text(".log")
            tail = outbox[outbox.rindex("checking @slow_account"):]
            check("an unverifiable handle is reported as such",
                  "could not verify" in tail and "accounts verified · 2" not in tail,
                  tail.replace("\n", " | "))
            page.click(".acct-line:has-text('slow_account') .x")
            page.wait_for_timeout(200)

            check("usernames become account rows",
                  len(rows) == 2 and "nasa" in rows[0], str(rows))

            page.click("button[type=submit]")
            page.wait_for_selector(".status-strip a.btn", timeout=30000)
            check("the outbox logged the run",
                  page.locator(".log-line").count() >= 3,
                  str(page.locator(".log-line").count()))
            check("home stays a workbench — no results under the form",
                  page.locator(".post").count() == 0,
                  str(page.locator(".post").count()))

            page.click(".status-strip a.btn")
            page.wait_for_selector(".post", timeout=15000)
            check("results open on their own route",
                  "/jobs/" in page.url, page.url)
            rows = page.locator(".post").count()
            check("results feed renders 4 posts", rows == 4, f"{rows} posts")
            check("posts are tagged per account",
                  "nasa" in page.inner_text(".deck.results"),
                  page.inner_text(".deck.results")[:200])
            check("post stats are not buttons",
                  page.locator(".metrics button").count() == 0,
                  str(page.locator(".metrics button").count()))
            check("each post has a permalink instead of save/share",
                  page.locator(".metrics .open a").count() == rows,
                  str(page.locator(".metrics .open a").count()))

            check("a CSV export button sits in the results rail",
                  page.get_by_role("button", name="Download CSV").is_visible())
            check("posts render X-style detail",
                  page.locator(".entry-name").first.inner_text().strip() != ""
                  and page.locator(".entry-tick").count() > 0,
                  page.locator(".entry-name").first.inner_text())
            check("media images are shown",
                  page.locator(".entry-media img").count() >= 1,
                  str(page.locator(".entry-media img").count()))
            check("the timestamp line is rendered",
                  "·" in page.inner_text(".entry-stamp"),
                  page.inner_text(".entry-stamp")[:60])

            with page.expect_download() as dl:
                page.get_by_role("button", name="Download CSV").click()
            name = dl.value.suggested_filename
            check("the CSV button downloads a .csv",
                  name.endswith(".csv") and "4posts" in name, name)



            json_href = page.get_by_role("link", name="Download JSON").get_attribute("href")
            check("download link carries the token", "token=" in json_href, json_href)

            # ---- the dashboard reads the archive ---------------------------
            page.click(".masthead-nav a[href='/dashboard']")
            page.wait_for_selector(".post-history", timeout=25000)
            page.click(".masthead-nav a[href='/']")
            page.wait_for_selector("#username-input")
            page.wait_for_timeout(400)
            booted = page.inner_text(".log").count("api up")
            check("the boot report is logged once, not per visit", booted == 1,
                  f"{booted} occurrences")

            page.click(".masthead-nav a[href='/dashboard']")
            page.wait_for_selector(".post-history", timeout=25000)
            check("the current section is marked as current",
                  page.get_attribute(".masthead-nav a[href='/dashboard']",
                                     "aria-current") == "page")
            tabs = page.locator(".ph-tabs button").count()
            check("the dashboard lists the accounts it collected", tabs >= 2,
                  f"{tabs} tabs")
            cards = page.locator(".ph-cards .entry").count()
            check("post history renders post cards", cards >= 1, f"{cards} cards")
            bars = page.locator(".chart rect.bar").count()
            check("the volume chart draws stacked bars", bars >= 1, f"{bars} bars")
            rows = page.locator(".usage-row").count()
            check("accounts extracted lists every handle", rows >= 2, f"{rows} rows")
            page.click(".ph-sortbar .dir-btn")
            page.wait_for_timeout(250)
            check("the dashboard sort direction toggles",
                  "ascending" in page.inner_text(".ph-sortbar .dir-btn").lower())

            # 5. refresh keeps the session (token validated via /api/auth/me)
            page.goto(f"{BASE}/history")
            page.wait_for_selector("table.ledger tbody tr", timeout=15000)
            page.reload()
            page.wait_for_selector("table.ledger tbody tr", timeout=15000)
            check("a refresh stays signed in", page.url.endswith("/history"),
                  page.url)
            job_rows = page.locator("table.ledger tbody tr").count()
            check("history lists the finished job", job_rows >= 1, f"{job_rows}")

            # 6. the job permalink is a protected route that survives a reload
            page.click("table.ledger tbody tr .open-btn")
            page.wait_for_selector(".post", timeout=15000)
            job_url = page.url
            check("job permalink is /jobs/<id>", "/jobs/" in job_url, job_url)
            page.reload()
            page.wait_for_selector(".post", timeout=15000)
            check("job permalink survives a refresh", "/jobs/" in page.url, page.url)

            # 7. a dead token kicks you out on the next request
            page.evaluate("() => localStorage.setItem('xtimeline.token', 'garbage.token')")
            page.goto(f"{BASE}/history")
            page.wait_for_url("**/login", timeout=15000)
            check("an invalid token bounces to /login", page.url.endswith("/login"),
                  page.url)

            # 8. signing out clears the token
            page.fill("input[type=password]", "e2e-pass")
            page.click("button[type=submit]")
            page.wait_for_selector(".masthead-nav")
            before = page.evaluate("() => document.documentElement.dataset.theme")
            page.click(".theme-btn")
            page.wait_for_timeout(150)
            after = page.evaluate("() => document.documentElement.dataset.theme")
            check("the header theme toggle flips the theme",
                  before != after and after in ("day", "night"),
                  f"{before} -> {after}")
            check("the header carries no session-expiry label",
                  page.locator(".session-exp").count() == 0)
            page.click("button.signout")
            page.wait_for_url("**/login", timeout=15000)
            gone = page.evaluate("() => localStorage.getItem('xtimeline.token')")
            check("sign out returns to /login and clears the token",
                  page.url.endswith("/login") and not gone, f"{page.url} {gone!r}")

            # 9. the API itself is closed to anonymous callers
            api_status = page.evaluate(
                """async () => {
                    const r = await fetch('/api/jobs');
                    return r.status;
                }"""
            )
            check("the API answers 401 without a token", api_status == 401, str(api_status))

            check("no uncaught page errors", not errors, "; ".join(errors[:3]))
            browser.close()
    finally:
        server.should_exit = True
        time.sleep(0.6)

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
