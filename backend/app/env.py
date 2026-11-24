"""Load `backend/.env` into the environment before anything reads config.

A small, dependency-free reader, because this file is on the startup path of
every entry point and deserves no third-party surprises.

Rules, in the order they matter:

  * real environment variables always win - a `.env` never overrides the shell,
    so CI and one-off `$env:XTIMELINE_STORE=mongo` still behave
  * `KEY=value`, `KEY = value`, `export KEY=value` and `KEY="quoted value"` all
    parse; `#` starts a comment when it is at the start of a line or follows a
    value that is not quoted
  * a missing file is not an error: the app has sensible defaults
  * `XTIMELINE_SKIP_DOTENV=1` disables loading entirely (the tests use it)

Nothing here logs a value - only the count and the path.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Optional

log = logging.getLogger("xtimeline.env")

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BACKEND_DIR / ".env"

_loaded: Optional[Path] = None
_count = 0


def parse(text: str) -> dict[str, str]:
    """Parse dotenv text into a dict. Pure, so it is easy to test."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]          # quoted: keep everything inside
        else:
            # unquoted: a " #" starts a trailing comment
            hash_at = value.find(" #")
            if hash_at != -1:
                value = value[:hash_at].rstrip()

        values[key] = value
    return values


def load_dotenv(path: Optional[Path] = None, override: bool = False) -> Optional[Path]:
    """Read the file into os.environ. Returns the path when it was loaded."""
    global _loaded, _count

    if os.environ.get("XTIMELINE_SKIP_DOTENV", "").lower() in ("1", "true", "yes"):
        return None

    target = Path(path) if path else DEFAULT_PATH
    if not override and _loaded == target:
        return target          # already applied; do not re-read or re-warn
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:  # pragma: no cover - unreadable file
        log.warning("could not read %s (%s) - using the real environment only",
                    target.name, exc)
        return None

    values = parse(text)
    applied = 0
    for key, value in values.items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied += 1

    _warn_if_world_readable(target)
    _loaded, _count = target, applied
    return target


def loaded_from() -> Optional[Path]:
    """The .env that was read, if any - for the startup banner."""
    return _loaded


def loaded_count() -> int:
    return _count


def _warn_if_world_readable(path: Path) -> None:
    """A file full of credentials should not be readable by everyone."""
    if os.name == "nt":          # NTFS uses ACLs; the POSIX bits mean little
        return
    try:
        mode = path.stat().st_mode
    except OSError:              # pragma: no cover
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        log.warning(
            "%s is readable by other users - `chmod 600 %s` (it holds "
            "credentials).", path.name, path.name,
        )
