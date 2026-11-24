"""Reproduce the crash and prove the fix survives it.

The reported failure was:

    playwright._impl._errors.Error: Page.query_selector: Execution context was
    destroyed, most likely because of a navigation

...raised by save_session.py the moment Enter was pressed, because X was still
redirecting the browser after login.

This serves a /slow page that takes 250 ms to answer, so a navigation can be
caught with the old document mid-teardown - the same state the script was in.
Then it probes the DOM in that window with the old call and with the new
helper, and compares.
"""

import http.server
import sys
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent   # the backend folder
sys.path.insert(0, str(BACKEND))

from playwright.sync_api import Error as PWError          # noqa: E402
from playwright.sync_api import sync_playwright           # noqa: E402

import save_session as ss                                 # noqa: E402

PAGES = {
    "/login": "<html><body><h1>Sign in to X</h1>"
              '<div data-testid="loginButton">Log in</div></body></html>',
    "/home": "<html><body><h1>Home</h1><article>a post</article>"
             '<div data-testid="SideNav_AccountSwitcher_Button">acct</div>'
             "</body></html>",
    "/logged-out": "<html><body><h1>Home</h1>"
                   '<div data-testid="loginButton">Log in</div></body></html>',
}


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: str):
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/slow":          # answer late: a catchable navigation
            import time
            time.sleep(0.25)
            self._send("<html><body><h1>Home</h1></body></html>")
            return
        body = PAGES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self._send(body)

    def log_message(self, *a):
        pass


def drain(page) -> None:
    """Let any in-flight navigation finish so the next goto isn't interrupted.

    wait_for_load_state() is no good here: the *old* document is already loaded,
    so it returns instantly while /slow is still in flight.
    """
    for _ in range(60):
        try:
            if page.url.endswith("/slow"):
                page.wait_for_timeout(100)
                return
        except PWError:
            pass
        try:
            page.wait_for_timeout(50)
        except PWError:
            return


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    rounds = 15
    old_crashes = new_crashes = unknown = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()

        # --- the old, unguarded call: reproduce the traceback -----------------
        for _ in range(rounds):
            page.goto(base + "/login", wait_until="load")
            # the page itself kicks off the redirect 50 ms from now, so the
            # probe loop below straddles the document swap
            page.evaluate("setTimeout(() => { location.href = '/slow'; }, 50)")
            for _ in range(700):
                try:
                    page.query_selector(ss.LOGIN_BUTTON)
                except PWError as exc:
                    if "Execution context was destroyed" in str(exc):
                        old_crashes += 1
                        break
                page.wait_for_timeout(1)
            drain(page)

        # --- the new helper under exactly the same race -----------------------
        for _ in range(rounds):
            page.goto(base + "/login", wait_until="load")
            page.evaluate("setTimeout(() => { location.href = '/slow'; }, 50)")
            try:
                if ss._looks_logged_out(page) is None:
                    unknown += 1
            except PWError:
                new_crashes += 1
            drain(page)

        # --- correctness once the page has settled ----------------------------
        page.goto(base + "/login", wait_until="load")
        on_login = ss._looks_logged_out(page)
        page.goto(base + "/home", wait_until="load")
        on_home = ss._looks_logged_out(page)
        page.goto(base + "/logged-out", wait_until="load")
        on_logged_out = ss._looks_logged_out(page)
        missing = ss._probe(page, '[data-testid="nope"]')

        browser.close()

    print(f"old page.query_selector() crashes : {old_crashes}/{rounds} rounds")
    print(f"new _looks_logged_out() crashes   : {new_crashes}/{rounds} rounds")
    print(f"  ('cannot tell' -> saves anyway  : {unknown}x)")
    print(f"settled /login        -> logged_out={on_login}")
    print(f"settled /home         -> logged_out={on_home}")
    print(f"settled /logged-out   -> logged_out={on_logged_out}")
    print(f"missing selector      -> {missing}")

    ok = (
        new_crashes == 0
        and on_login is True
        and on_home is False
        and on_logged_out is True
        and missing is False
    )
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
