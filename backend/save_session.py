"""Run this ONCE to save your logged-in X session.

    python save_session.py

A browser window opens. Log into X normally. If X shows a "Confirm your
account" knowledge check, fill in your username (your handle, without the @)
and complete it - the Continue button stays greyed out until the box has text.
When you can see your home timeline, come back to the terminal and press Enter.

Two things get written, both in the backend folder:

  .x_profile/    a persistent browser profile - the actual browser that logged
                 in. The scraper reuses this, so X sees one consistent profile
                 instead of a brand-new automated one (which it often answers
                 with a blank page).
  x_state.json   the cookies (storage state), replayed when the profile is
                 unavailable. Treat it like a password.

Why the extra care below: Playwright normally announces itself to X (the
navigator.webdriver flag plus a fresh profile), which is exactly what triggers
those challenges. We launch a persistent profile with the automation flags off
and a small init script that hides the obvious tells.

Re-run this script whenever the app says the session has expired.
"""

import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    # Playwright needs a Proactor loop to spawn its driver on Windows.
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

HERE = Path(__file__).resolve().parent
STATE = HERE / "x_state.json"
PROFILE = HERE / ".x_profile"          # persistent browser profile (reused later)

LOGIN_BUTTON = '[data-testid="loginButton"]'
ACCOUNT_SWITCHER = '[data-testid="SideNav_AccountSwitcher_Button"]'
LOGIN_URL_MARKERS = ("/i/flow/login", "/login", "/i/flow/signup")

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

# Hide the most common automation tells from X's fingerprinting.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def _settle(page) -> None:
    """Give a page that is mid-redirect a chance to land.

    Pressing Enter while X is still navigating (login -> /home is a real
    redirect) means the document can be replaced underneath us a moment later.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightError:
        pass
    try:
        page.wait_for_timeout(600)      # let X's client-side routing finish
    except PlaywrightError:
        pass


def _probe(page, selector: str) -> Optional[bool]:
    """Is `selector` on the page right now?

    Returns True / False, or None when we genuinely could not tell.

    A bare page.query_selector() raises "Execution context was destroyed, most
    likely because of a navigation" if the document is replaced between the
    call being sent and the reply coming back. That is a race, not a verdict:
    the session may be perfectly good. So retry across the navigation instead
    of letting the script die at the last step.
    """
    for attempt in range(4):
        try:
            return page.query_selector(selector) is not None
        except PlaywrightError:
            if attempt == 3:
                return None
            _settle(page)
    return None


def _looks_logged_out(page) -> Optional[bool]:
    """Best-effort 'is this a logged-out page?' that survives redirects."""
    try:
        url = page.url or ""
    except PlaywrightError:             # pragma: no cover - very rare
        url = ""

    if _probe(page, ACCOUNT_SWITCHER):
        return False                    # the account menu is only there when in
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return True

    found = _probe(page, LOGIN_BUTTON)
    if found is None:
        return None
    return found


def main() -> None:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            args=LAUNCH_ARGS,
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()

        # X's page is heavy: its `load` event can take longer than Playwright's
        # 30s default, or never fire at all. We only need the page interactive
        # so you can log in, so wait for the DOM and never treat a slow load as
        # fatal - the browser window is already open either way.
        try:
            page.goto("https://x.com/", wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print("\n(X is taking a while to finish loading - carrying on anyway;")
            print(" the window is open, go ahead and log in.)")
        except Exception as exc:  # pragma: no cover - best effort
            print(f"\n(navigation warning: {exc} - carrying on anyway.)")

        print("A browser window has opened. Log into X.")
        print("If you see 'Confirm your account', type your username (handle,")
        print("no @) and press Continue to clear it.")
        input("When you can see your home timeline, press Enter here... ")

        # Let the redirect that usually follows a login land before we look.
        _settle(page)

        # Guard against saving an empty / logged-out session.
        if "knowledge_check" in page.url:
            print("\nX is still showing the 'Confirm your account' check.")
            input("Finish it in the browser, then press Enter again... ")
            _settle(page)

        logged_out = _looks_logged_out(page)

        if logged_out is None:
            print("\n(Couldn't get a clean look at the page - it kept redirecting.")
            print(" Saving anyway: the persistent profile carries your login, and")
            print(" you can confirm with `python check_session.py`.)")
        elif logged_out:
            print("\nStill looks logged out - no session saved.")
            print("Finish logging in until you can see your home timeline,")
            print("then re-run this script.")
            context.close()
            return

        try:
            context.storage_state(path=str(STATE))
        except Exception as exc:
            print(f"\nCouldn't write {STATE}: {exc}")
            print(f"The persistent profile was still created at {PROFILE},")
            print("so the extractor should still work.")
            context.close()
            return

        print(f"\nSaved session to {STATE}")
        print(f"Persistent profile: {PROFILE}")
        print("You can now close the browser window.")
        context.close()


if __name__ == "__main__":
    main()
