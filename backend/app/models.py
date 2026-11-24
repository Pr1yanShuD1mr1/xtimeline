"""Request / response shapes for the API."""

import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# A handle: 1-15 chars, letters/digits/underscore (X's own rule).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

Strategy = Literal["search", "profile", "auto"]


class ScrapeRequest(BaseModel):
    """What the UI posts to /api/scrape.

    Playwright is the only extraction path now, so there is no `method` field -
    `strategy` picks *which page* the browser drives instead.
    """

    handles: list[str]
    start: datetime
    end: datetime
    # search  -> x.com/search?q=from:handle since:.. until:..   (date filter is
    #            done by X itself, so this is the fast path)
    # profile -> x.com/handle, scroll the profile timeline
    # auto    -> search first, fall back to profile when search renders nothing
    strategy: Strategy = "search"
    limit: int = Field(500, ge=0, le=5000)          # per account, 0 = no cap
    max_scrolls: int = Field(60, ge=1, le=1000)     # safety cap per account
    headed: bool = False                            # show the browser window
    include_retweets: bool = False                  # keep reposts of others

    # Deprecated: the old twscrape API field. Rejected loudly rather than
    # silently ignored, so an old client can't think it got twscrape.
    method: Optional[str] = None

    @field_validator("handles", mode="before")
    @classmethod
    def split_handles(cls, v):
        """Allow a single string ('a, b c') as well as a real list."""
        if v is None:
            return []
        if isinstance(v, str):
            v = re.split(r"[,\s]+", v)
        return v

    @field_validator("handles")
    @classmethod
    def clean_handles(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in v:
            h = str(raw).strip().lstrip("@").strip()
            if not h:
                continue
            if not _HANDLE_RE.match(h):
                raise ValueError(
                    f"'{h}' is not a valid handle (letters, digits and "
                    "underscores only, up to 15 characters)"
                )
            key = h.lower()
            if key in seen:      # de-dupe, case-insensitively
                continue
            seen.add(key)
            cleaned.append(h)
        if not cleaned:
            raise ValueError("at least one handle is required")
        if len(cleaned) > 25:
            raise ValueError("too many handles at once (max 25)")
        return cleaned

    @model_validator(mode="after")
    def check_range(self):
        if self.end < self.start:
            raise ValueError("end must be after start")
        if self.method is not None:
            raise ValueError(
                "the 'method' field is gone - this build is Playwright only. "
                "Use 'strategy': 'search' | 'profile' | 'auto'."
            )
        return self


class Tweet(BaseModel):
    id: str
    datetime: str
    text: str
    url: str
    author: str
    account: str = ""           # which requested handle this came from
    display_name: str = ""      # "freeCodeCamp.org", when X rendered it
    avatar: str = ""            # profile image URL
    verified: bool = False
    media: list[str] = []       # image / video-poster URLs, in order
    pinned: bool = False
    is_repost: bool = False     # a repost of somebody else's post
    metrics: dict = {}          # replies, reposts, likes, bookmarks, views


class JobSnapshot(BaseModel):
    id: str
    handle: str                 # display string, e.g. "a, b, c"
    handles: list[str] = []
    status: str                 # queued | running | done | error | cancelled
    progress: int
    collected: int
    message: str
    strategy: str = ""
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    note: str = ""
    per_account: dict = {}
