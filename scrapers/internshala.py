"""Internshala: internships + fresher jobs, global feed + work-from-home + category pages."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import (clean, fetch, make_listing, make_session, polite, raise_if_blocked, register)

BASE = "https://internshala.com"

# Category slugs (same slugs the DB already carries in listings.category). A 404 just ends that category.
CATEGORIES = [
    "web-development", "graphic-design", "content-writing", "digital-marketing",
    "social-media-marketing", "data-science", "machine-learning", "mobile-app-development",
    "ui-ux-design", "animation", "photography", "finance",
]

_DURATION = re.compile(r"\b\d+\s*(?:months?|weeks?)\b", re.I)
_CARD = re.compile(r"individual_internship|internship_meta|individual_job")


def _parse(html: str, category: str | None) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.individual_internship") or soup.select(".internship_meta")
    out: list[dict] = []
    seen: set[str] = set()

    def add(a, card):
        href = (a.get("href") or "").split("?")[0]
        if not href or ("/internship/detail/" not in href and "/job/detail/" not in href):
            return
        url = urljoin(BASE, href)
        if url in seen:
            return
        title = clean(a.get_text())
        if not title:
            return
        seen.add(url)
        kind = "job" if "/job/detail/" in url else "internship"
        company = loc = stipend = duration = None
        if card is not None:
            el = card.select_one(".company-name")
            company = el.get_text() if el else None
            locs = [x.get_text() for x in card.select(".locations a, .location_link")]
            loc = ", ".join(clean(x) for x in locs if clean(x)) or None
            el = card.select_one(".stipend, .salary, .ctc")
            stipend = el.get_text() if el else None
            m = _DURATION.search(card.get_text(" "))
            duration = m.group(0) if m else None
        if not loc:
            loc = "Work from home" if "work-from-home" in url else "India"
        row = make_listing(title=title, url=url, platform="internshala", type=kind, company=company,
                           stipend=stipend, duration=duration if kind == "internship" else None,
                           location=loc, category=category)
        if row:
            out.append(row)

    if cards:
        for card in cards:
            a = card.select_one("a.job-title-href") or card.select_one(
                'a[href*="/internship/detail/"], a[href*="/job/detail/"]')
            if a:
                add(a, card)
    else:  # selector drift fallback: take title anchors straight off the page
        for a in soup.select("a.job-title-href"):
            add(a, a.find_parent(class_=_CARD))
    return out


def _crawl(s, url_for_page, pages: int, category: str | None, tag: str) -> list[dict]:
    rows: list[dict] = []
    for p in range(1, pages + 1):
        r = fetch(s, url_for_page(p), tag=tag)
        if r is None:
            break
        page_rows = _parse(r.text, category)
        if not page_rows:
            break
        rows += page_rows
        polite(1.0, 1.8)
    return rows


@register("internshala-internships", "internship", "internshala")
def internships(pages: int) -> list[dict]:
    s, rows = make_session(), []
    rows += _crawl(s, lambda p: f"{BASE}/internships/page-{p}/", pages, None, "internshala:all")
    rows += _crawl(s, lambda p: f"{BASE}/internships/work-from-home-internships/page-{p}/",
                   pages, None, "internshala:wfh")
    for slug in CATEGORIES:
        rows += _crawl(s, lambda p, slug=slug: f"{BASE}/internships/{slug}-internship/page-{p}/",
                       min(pages, 3), slug, f"internshala:{slug}")
    raise_if_blocked(s, len(rows))
    return rows


@register("internshala-jobs", "job", "internshala")
def jobs(pages: int) -> list[dict]:
    s, rows = make_session(), []
    rows += _crawl(s, lambda p: f"{BASE}/jobs/page-{p}/", pages, None, "internshala-jobs:all")
    rows += _crawl(s, lambda p: f"{BASE}/jobs/work-from-home-jobs/page-{p}/", pages, None, "internshala-jobs:wfh")
    for slug in CATEGORIES:
        rows += _crawl(s, lambda p, slug=slug: f"{BASE}/jobs/{slug}-jobs/page-{p}/",
                       min(pages, 2), slug, f"internshala-jobs:{slug}")
    raise_if_blocked(s, len(rows))
    return rows
