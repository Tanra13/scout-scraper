"""Unstop public API: internships + jobs."""
from __future__ import annotations

from datetime import datetime, timezone

from config import JSON_HEADERS

from .common import fetch, make_listing, make_session, polite, raise_if_blocked, register

API = "https://unstop.com/api/public/opportunity/search-result"


def _money(v) -> str | None:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return None


def _stipend(item: dict, per: str) -> str | None:
    jd = item.get("jobDetail") or {}
    lo, hi = _money(jd.get("min_salary")), _money(jd.get("max_salary"))
    if lo and hi and lo != hi:
        return f"₹ {lo} - {hi} {per}"
    if lo or hi:
        return f"₹ {lo or hi} {per}"
    return None


def _location(item: dict) -> str:
    addr = item.get("address_with_country_logo") or {}
    city = addr.get("city") if isinstance(addr, dict) else None
    if city:
        return city
    return "Remote" if (item.get("region") or "").lower() == "online" else (item.get("region") or "India")


def _expired(item: dict) -> bool:
    end = item.get("end_date")
    if not end:
        return False
    try:
        return datetime.fromisoformat(end) < datetime.now(timezone.utc)
    except ValueError:
        return False


def _crawl(kind: str, opp: str, per: str, pages: int) -> list[dict]:
    s = make_session()
    s.headers.update(JSON_HEADERS)
    rows: list[dict] = []
    for p in range(1, pages + 1):
        r = fetch(s, API, params={"opportunity": opp, "page": p, "per_page": 20, "oppstatus": "open"},
                  tag=f"unstop:{opp}")
        if r is None:
            break
        try:
            items = ((r.json() or {}).get("data") or {}).get("data") or []
        except ValueError:
            break
        if not items:
            break
        for it in items:
            if _expired(it):
                continue
            pub = (it.get("public_url") or "").lstrip("/")
            if not pub:
                continue
            # public_url already starts with "internships/" or "jobs/" — do not prefix it again.
            row = make_listing(
                title=it.get("title"), url=f"https://unstop.com/{pub}", platform="unstop", type=kind,
                company=(it.get("organisation") or {}).get("name"),
                stipend=_stipend(it, per), location=_location(it),
                category=(it.get("category_name") or opp),
            )
            if row:
                rows.append(row)
        polite(0.8, 1.4)
    raise_if_blocked(s, len(rows))
    return rows


@register("unstop-internships", "internship", "unstop")
def internships(pages: int) -> list[dict]:
    return _crawl("internship", "internships", "/month", pages)


@register("unstop-jobs", "job", "unstop")
def jobs(pages: int) -> list[dict]:
    return _crawl("job", "jobs", "/year", pages)
