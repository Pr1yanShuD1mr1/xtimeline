"""Is the MongoDB history store reachable, and what is in it?

    python check_mongo.py

Reads the same environment variables the backend does, so run it with the same
shell you start `run.py` from:

    XTIMELINE_MONGO_URI   mongodb://127.0.0.1:27017  or an Atlas mongodb+srv://
    XTIMELINE_MONGO_DB    the database (default: the one in the URI, else xtimeline)
    XTIMELINE_STORE       auto | mongo | memory

It prints the server version, the host, the database, how many jobs and posts
are stored, and the newest job. On failure it prints the one thing to check
instead of a driver stack trace.
"""

import asyncio
import sys

from app import env as persistence_env, persistence


async def main() -> int:
    uri = persistence.mongo_uri()
    print("-" * 62)
    source = "backend/.env" if persistence_env.loaded_from() else "environment only"
    print(f"config    : {source}")
    print(f"mode      : {persistence.store_mode()}")
    print(f"uri       : {persistence._redact(uri)}")
    print(f"database  : {persistence.mongo_db_name(uri)}")
    print(f"timeout   : {persistence.server_timeout_ms(uri)} ms")
    print("-" * 62)

    try:
        store = await persistence.open_store()
    except Exception as exc:
        print(f"VERDICT: could not open the store - {persistence.explain(exc, uri)}")
        print("-" * 62)
        return 2

    if store is None:
        print("VERDICT: no database in use - history is memory-only.")
        print("Fix: start mongod, or set XTIMELINE_MONGO_URI to your Atlas string,")
        print("     then run this again. XTIMELINE_STORE=mongo makes it fatal.")
        print("-" * 62)
        return 1

    try:
        info = await store.ping()
        counts = await store.counts()
        newest = (await store.list_jobs(limit=1) or [None])[0]
    except Exception as exc:
        print(f"VERDICT: connected, but the query failed - {persistence.explain(exc, uri)}")
        print("-" * 62)
        return 2

    print(f"server    : MongoDB {info['version']}")
    print(f"host      : {info['host']}")
    print(f"jobs      : {counts['jobs']}")
    print(f"posts     : {counts['posts']}")
    if newest:
        print(f"newest    : {newest.get('id')}  {newest.get('status')}  "
              f"{newest.get('collected')} posts  {newest.get('created_at')}")
    print("-" * 62)
    print("VERDICT: history is being stored. Past runs will appear in History")
    print("         and can be reopened from /jobs/<id>.")
    print("-" * 62)
    await store.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
