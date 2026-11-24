"""Is the saved X session actually logging us in?

    python check_session.py            (visible browser)
    python check_session.py --headless (quiet, no window)

Opens x.com/home with the saved session, then reports whether the timeline
rendered, how many posts are on the page, and which wall X is showing if not.
It also saves a screenshot to output/session_check.png.

This separates "is my session valid?" from "is my scraping logic right?" - if
this script shows a healthy timeline, the extractor should work too.
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app import scraper  # noqa: E402

HERE = Path(__file__).resolve().parent
SHOT = HERE / "output" / "session_check.png"


async def main(headless: bool) -> None:
    status = scraper.session_status()
    print("-" * 60)
    print(f"state file      : {scraper.STATE_FILE} "
          f"({'found' if status['saved'] else 'MISSING'})")
    print(f"persistent prof.: {'found' if status['profile'] else 'missing'}")
    if status["state_file_age_minutes"] is not None:
        print(f"state file age  : {status['state_file_age_minutes']} min")
    if not status["saved"] and not status["profile"]:
        print("\nNo session yet - run `python save_session.py` first.")
        print("-" * 60)
        return

    try:
        info = await scraper.check_session(headless=headless)
    except Exception as exc:
        print(f"\ncheck failed: {type(exc).__name__}: {exc}")
        print("-" * 60)
        return

    print(f"url             : {info['url']}")
    print(f"page title      : {info['title']!r}")
    print(f"tweets on page  : {info['tweets_on_page']}")
    if info["wall"]:
        print(f"wall            : {info['wall']}")
    print("-" * 60)
    if info["logged_in"]:
        print("VERDICT: session is healthy - the extractor should work.")
    elif "session" in (info["wall"] or "") or not info["wall"]:
        print("VERDICT: not logged in / no timeline - re-run save_session.py.")
    else:
        print("VERDICT: logged in, but X is not giving us a timeline "
              f"({info['wall']}).")


if __name__ == "__main__":
    asyncio.run(main(headless="--headless" in sys.argv))
