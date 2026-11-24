"""Start the backend with an event loop that Playwright can use.

    python run.py            (from inside the backend folder)

Why this exists
---------------
Playwright launches a small driver *subprocess*. On Windows only a
ProactorEventLoop can spawn subprocesses; a SelectorEventLoop raises
NotImplementedError from asyncio's _make_subprocess_transport - which is the
error you get from a plain `uvicorn app.main:app`.

The event loop policy has to be set BEFORE uvicorn creates its loop, so it
can't live inside app/main.py (uvicorn imports that too late). Hence this
launcher.

Note: don't add --reload. The reloader runs the app in a fresh child process
that re-creates its own loop without this policy, bringing the error back - and
it also throws away in-memory jobs mid-scrape.
"""

import asyncio
import os
import sys

if sys.platform == "win32":
    # The whole point: a Proactor loop, so Playwright can start its driver.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn  # noqa: E402  (import after the policy is set)

from app.env import load_dotenv, loaded_count, loaded_from  # noqa: E402

# Read backend/.env here as well as in app/__init__: this launcher starts
# uvicorn, which imports the app later, and scraper.py reads its knobs at
# import time. Loading first means .env values are already in the environment.
load_dotenv()

if __name__ == "__main__":
    if loaded_from():
        print(f"[env] using {loaded_from().name} "
              f"({loaded_count()} variable(s) applied)", flush=True)
    else:
        print("[env] no .env found - using defaults and the real environment",
              flush=True)
    host = os.environ.get("XTIMELINE_HOST", "127.0.0.1")
    port = int(os.environ.get("XTIMELINE_PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        loop="asyncio",     # force the stdlib loop, not uvloop
        reload=False,       # see the note above
    )
