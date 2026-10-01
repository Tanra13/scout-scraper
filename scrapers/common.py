"""Shared helpers: HTTP session, listing builder, classifier, source registry."""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import HEADERS


# ── HTTP ─────────────────────────────────────────────────────────────────────

class SourceBlocked(Exception):
    """Site answered 401/403/429 (bot protection or rate limit). We do NOT try to evade it."""


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=1.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",), respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    s.last_status = None  # type: ignore[attr-defined]
    return s


def fetch(s: requests.Session, url: str, *, params=None, headers=None, timeout: int = 25,
          tag: str = "") -> Optional[requests.Response]:
    """GET. Returns the response only for HTTP 200, else None (status kept in s.last_status)."""
    try:
        r = s.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        s.last_status = None  # type: ignore[attr-defined]
        print(f"  [{tag or url}] request failed: {type(e).__name__}: {str(e)[:120]}")
        return None
    s.last_status = r.status_code  # type: ignore[attr-defined]
    if r.status_code != 200:
        print(f"  [{tag or url}] HTTP {r.status_code}")
        return None
    return r


def raise_if_blocked(s: requests.Session, found: int) -> None:
    if found == 0 and getattr(s, "last_status", None) in (401, 403, 429):
        raise SourceBlocked(f"HTTP {s.last_status} — bot protection / rate limit")


def polite(lo: float = 1.0, hi: float = 2.0) -> None:
    time.sleep(random.uniform(lo, hi))


# ── Listing builder ──────────────────────────────────────────────────────────

_WS = re.compile(r"\s+")


def clean(text) -> str:
    return _WS.sub(" ", str(text or "")).strip()


def make_listing(*, title, url, platform, type, company=None, stipend=None, duration=None,
                 location=None, category=None) -> Optional[dict]:
    """Normalised row. Keys match public.listings columns (always all keys: PostgREST bulk upsert)."""
    t, u = clean(title), clean(url)
    if not t or not u.startswith("http"):
        return None
    return {
        "title": t[:300],
        "company": clean(company)[:200] or None,
        "stipend": clean(stipend)[:100] or None,
        "duration": clean(duration)[:100] or None,
        "location": clean(location)[:200] or "Remote",
        "platform": platform[:100],
        "category": clean(category)[:200] or None,
        "type": type,
        "url": u[:500],
    }


# ── Classification (port of supabase/functions/classify-listing, word-aware) ─

BLOCKLIST = [
    "telecaller", "bpo", "insurance agent", "field sales", "door to door", "mlm",
    "network marketing", "direct sales", "cold calling", "telesales", "field work",
    "sales executive", "business development executive",
]

SKILL_CATEGORY_MAP = {
    "content-writing": ["content", "writing", "writer", "copywriter", "blog", "article", "copywriting", "seo", "editor"],
    "digital-marketing": ["marketing", "seo", "sem", "social media", "ads", "campaign", "growth"],
    "graphic-design": ["design", "graphic", "creative", "illustrat", "figma", "photoshop", "canva"],
    "video-editing": ["video", "editor", "editing", "reel", "youtube", "motion", "premiere", "after effects"],
    "web-development": ["web", "developer", "frontend", "front-end", "backend", "react", "next.js", "javascript", "html", "css", "node"],
    "ui-ux": ["ui", "ux", "product design", "wireframe", "prototype", "user experience"],
    "data-analysis": ["data", "analyst", "excel", "sql", "tableau", "power bi"],
    "social-media": ["social media", "instagram", "twitter", "linkedin", "community"],
    "python": ["python", "django", "flask", "machine learning", "data science"],
    "flutter": ["flutter", "dart", "mobile", "android", "ios", "app development"],
}


def _rx(words):
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + ")", re.I)


_BLOCK_RX = _rx(BLOCKLIST)
_CAT_RX = {k: _rx(v) for k, v in SKILL_CATEGORY_MAP.items()}


def classify(title: str, category: str = "") -> tuple[str, bool]:
    """Returns (internal_category, blocked)."""
    combined = f"{title} {category}"
    if _BLOCK_RX.search(combined):
        return "blocked", True
    for slug, rx in _CAT_RX.items():
        if rx.search(combined):
            return slug, False
    return "general", False


SENIOR_RX = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head of|director|vp|vice president|chief|architect|"
    r"manager|\d{1,2}\+?\s*years?)\b", re.I)


def junior_friendly(title: str) -> bool:
    """For global remote boards: drop obviously senior roles (students can't apply)."""
    return not SENIOR_RX.search(title or "")


# ── Source registry ──────────────────────────────────────────────────────────

@dataclass
class Source:
    name: str                 # shown in the health table
    type: str                 # internship | freelance | job
    group: str                # sources in one group run sequentially (same domain)
    fn: Optional[Callable[[int], list]] = None
    disabled: Optional[str] = None   # reason, if intentionally off


SOURCES: list[Source] = []


def register(name: str, type: str, group: str, disabled: Optional[str] = None):
    def deco(fn):
        SOURCES.append(Source(name, type, group, fn, disabled))
        return fn
    return deco


def register_disabled(name: str, type: str, reason: str) -> None:
    SOURCES.append(Source(name, type, name, None, reason))
