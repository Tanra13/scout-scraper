"""Job sources: Freshersworld, WeWorkRemotely (RSS), Jobicy (API), RemoteOK (API)."""
from __future__ import annotations

import re

import feedparser
from bs4 import BeautifulSoup

from config import JSON_HEADERS

from .common import (fetch, junior_friendly, make_listing, make_session, polite, raise_if_blocked,
                     register)

# ── Freshersworld ────────────────────────────────────────────────────────────

@register("freshersworld", "job", "freshersworld")
def scrape_freshersworld(pages: int) -> list[dict]:
    """Title is not in the static HTML; it is recoverable from each card's job_display_url:
    <title-slug>-jobs-opening-in-<company>-at-<location>-<id>."""
    s, rows = make_session(), []
    for page in range(pages):
        r = fetch(s, "https://www.freshersworld.com/jobs", params={"limit": 20, "offset": page * 20},
                  tag="freshersworld")
        if r is None:
            break
        cards = BeautifulSoup(r.text, "lxml").select("div.job-container")
        if not cards:
            break
        for c in cards:
            du = c.get("job_display_url", "")
            m = re.search(r"/jobs/(.+?)-jobs?-opening-in-", du)
            if not m:
                continue
            comp = c.select_one("h3.latest-jobs-title")
            loc = c.select_one(".job-location a")
            exp = c.select_one(".experience")
            row = make_listing(
                title=m.group(1).replace("-", " ").title(), url=du, platform="freshersworld", type="job",
                company=comp.get_text() if comp else None, stipend=exp.get_text() if exp else "Freshers",
                location=loc.get_text() if loc else "India",
            )
            if row:
                rows.append(row)
        polite(1.2, 2.0)
    raise_if_blocked(s, len(rows))
    return rows


# ── WeWorkRemotely (RSS) ─────────────────────────────────────────────────────

WWR_FEEDS = [
    ("https://weworkremotely.com/remote-jobs.rss", None),
    ("https://weworkremotely.com/categories/remote-programming-jobs.rss", "web-development"),
    ("https://weworkremotely.com/categories/remote-design-jobs.rss", "graphic-design"),
    ("https://weworkremotely.com/categories/remote-marketing-jobs.rss", "digital-marketing"),
    ("https://weworkremotely.com/categories/remote-copywriting-jobs.rss", "content-writing"),
    ("https://weworkremotely.com/categories/remote-customer-support-jobs.rss", "customer-support"),
]


@register("weworkremotely", "job", "weworkremotely")
def scrape_wwr(pages: int) -> list[dict]:
    s, rows, seen = make_session(), [], set()
    for feed_url, cat in WWR_FEEDS:
        r = fetch(s, feed_url, tag="wwr")
        if r is None:
            continue
        for e in feedparser.parse(r.content).entries:
            raw = e.get("title", "")
            company, _, title = raw.partition(":")
            if not title:
                company, title = None, raw
            title = title.strip()
            link = e.get("link")
            if not link or link in seen or not junior_friendly(title):
                continue
            seen.add(link)
            row = make_listing(title=title, url=link, platform="weworkremotely", type="job",
                               company=company, location="Remote", category=cat)
            if row:
                rows.append(row)
        polite(0.4, 0.8)
    raise_if_blocked(s, len(rows))
    return rows


# ── Jobicy (public API; attribution = we link to the original jobicy.com URL) ─

_INDIA_OK = re.compile(r"anywhere|worldwide|apac|asia|india", re.I)


@register("jobicy", "job", "jobicy")
def scrape_jobicy(pages: int) -> list[dict]:
    s = make_session()
    s.headers.update(JSON_HEADERS)
    r = fetch(s, "https://jobicy.com/api/v2/remote-jobs", params={"count": 100}, tag="jobicy")
    rows: list[dict] = []
    if r is not None:
        try:
            jobs = (r.json() or {}).get("jobs") or []
        except ValueError:
            jobs = []
        for j in jobs:
            geo = j.get("jobGeo") or ""
            title = j.get("jobTitle") or ""
            if not _INDIA_OK.search(geo) or not junior_friendly(title):
                continue
            ind = j.get("jobIndustry") or []
            cat = ", ".join(ind) if isinstance(ind, list) else str(ind)
            sal = None
            if j.get("salaryMin"):
                sal = f"{j.get('salaryCurrency', '')} {int(j['salaryMin']):,}-{int(j.get('salaryMax') or 0):,} /{j.get('salaryPeriod', 'year')}"
            row = make_listing(title=title, url=j.get("url"), platform="jobicy", type="job",
                               company=j.get("companyName"), stipend=sal, location=f"Remote ({geo})",
                               category=cat)
            if row:
                rows.append(row)
    raise_if_blocked(s, len(rows))
    return rows


# ── RemoteOK (public API; terms: link back to remoteok.com — our url does) ───

@register("remoteok", "job", "remoteok")
def scrape_remoteok(pages: int) -> list[dict]:
    s = make_session()
    s.headers.update(JSON_HEADERS)
    r = fetch(s, "https://remoteok.com/api", tag="remoteok")
    rows: list[dict] = []
    if r is not None:
        try:
            data = r.json()
        except ValueError:
            data = []
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):  # first element is the legal notice
                continue
            loc = j.get("location") or ""
            if loc and not _INDIA_OK.search(loc) and "remote" not in loc.lower():
                continue
            if not junior_friendly(j["position"]):
                continue
            sal = None
            if j.get("salary_min"):
                sal = f"${int(j['salary_min']):,}-{int(j.get('salary_max') or 0):,} /year"
            row = make_listing(title=j["position"], url=j.get("url"), platform="remoteok", type="job",
                               company=j.get("company"), stipend=sal, location=loc or "Remote",
                               category=", ".join(j.get("tags") or [])[:200])
            if row:
                rows.append(row)
    raise_if_blocked(s, len(rows))
    return rows
