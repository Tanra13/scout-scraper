"""Freelance sources: Freelancer.com (public API), Guru, Workana. Truelancer is intentionally off."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import JSON_HEADERS

from .common import (clean, fetch, make_listing, make_session, polite, raise_if_blocked, register,
                     register_disabled)

# NOTE: platform names here are capitalised on purpose — the app's onboarding stores
# freelance_platforms as 'Truelancer' / 'Guru' / 'Workana' and filterByPlatform() matches exactly.


# ── Freelancer.com — public read API (no key needed for active projects) ────

@register("freelancer", "freelance", "freelancer")
def scrape_freelancer(pages: int) -> list[dict]:
    s = make_session()
    s.headers.update(JSON_HEADERS)
    rows: list[dict] = []
    per_page = 50
    for p in range(max(pages, 1) * 2):  # API pages are small; take 2x
        r = fetch(
            s, "https://www.freelancer.com/api/projects/0.1/projects/active/",
            params={"limit": per_page, "offset": p * per_page, "compact": "true",
                    "job_details": "true", "full_description": "false"},
            tag="freelancer",
        )
        if r is None:
            break
        try:
            projects = ((r.json() or {}).get("result") or {}).get("projects") or []
        except ValueError:
            break
        if not projects:
            break
        for pr in projects:
            seo = pr.get("seo_url")
            if not seo:
                continue
            b, cur = pr.get("budget") or {}, pr.get("currency") or {}
            sign = cur.get("sign") or (cur.get("code", "") + " ")
            lo, hi = b.get("minimum"), b.get("maximum")
            budget = None
            if lo is not None and hi is not None:
                budget = f"{sign}{lo:,.0f} - {hi:,.0f}" + (" /hr" if pr.get("type") == "hourly" else "")
            skills = ", ".join(j.get("name", "") for j in (pr.get("jobs") or []) if j.get("name"))
            row = make_listing(
                title=pr.get("title"), url=f"https://www.freelancer.com/projects/{seo}",
                platform="Freelancer", type="freelance", stipend=budget, location="Remote",
                category=skills,
            )
            if row:
                rows.append(row)
        polite(0.8, 1.4)
    raise_if_blocked(s, len(rows))
    return rows


# ── Guru ─────────────────────────────────────────────────────────────────────

@register("guru", "freelance", "guru")
def scrape_guru(pages: int) -> list[dict]:
    """Guru paginates client-side now, so ?page=N repeats page 1: stop when the lead job repeats."""
    s = make_session()
    rows: list[dict] = []
    lead_seen = None
    for page in range(1, pages + 1):
        url = "https://www.guru.com/d/jobs/" if page == 1 else f"https://www.guru.com/d/jobs/?page={page}"
        r = fetch(s, url, tag="guru")
        if r is None:
            break
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select("div.jobRecord")
        if not cards:
            break
        lead = cards[0].get("data-gid")
        if lead is not None and lead == lead_seen:
            break
        lead_seen = lead
        for c in cards:
            a = c.select_one("h2.jobRecord__title a")
            if not a:
                continue
            bud = c.select_one(".jobRecord__budget")
            emp = c.select_one(".employerName, .employer")
            skills = ", ".join(clean(x.get_text()) for x in c.select(".skillsList a, .skills a"))
            row = make_listing(
                title=a.get_text(), url=urljoin("https://www.guru.com", a.get("href", "")),
                platform="Guru", type="freelance", company=emp.get_text() if emp else None,
                stipend=bud.get_text() if bud else None, location="Remote", category=skills,
            )
            if row:
                rows.append(row)
        polite(2.0, 3.0)
    raise_if_blocked(s, len(rows))
    return rows


# ── Workana ──────────────────────────────────────────────────────────────────
# Workana renders listings with a front-end framework and its class names change. We try the
# known card selectors first, then fall back to "any link to /job/<slug>" so a CSS rename
# degrades to fewer fields instead of zero listings. Check the health table after the first run.

_BUDGET = re.compile(r"(?:US\$|\$|₹)\s?[\d,.]+(?:\s?[-–]\s?(?:US\$|\$|₹)?\s?[\d,.]+)?", re.I)


def _workana_parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()

    def push(a, card):
        href = a.get("href") or ""
        m = re.search(r"/job/[^/?#]+", href)
        if not m:
            return
        url = "https://www.workana.com" + m.group(0)
        title = clean(a.get_text())
        if url in seen or len(title) < 5:
            return
        seen.add(url)
        budget, cat = None, ""
        if card is not None:
            bm = _BUDGET.search(card.get_text(" "))
            budget = bm.group(0) if bm else None
            cat = ", ".join(clean(x.get_text()) for x in card.select(".skills a, .skill, .tags a, .label"))[:200]
        row = make_listing(title=title, url=url, platform="Workana", type="freelance",
                           stipend=budget, location="Remote", category=cat)
        if row:
            out.append(row)

    cards = soup.select("div.project-item, article.project, li[class*='project']")
    if cards:
        for c in cards:
            a = c.select_one("h2 a, h3 a, .project-title a") or c.select_one('a[href*="/job/"]')
            if a:
                push(a, c)
    if not out:  # fallback: any /job/ link on the page
        for a in soup.select('a[href*="/job/"]'):
            push(a, a.find_parent(["article", "li", "div"]))
    return out


@register("workana", "freelance", "workana")
def scrape_workana(pages: int) -> list[dict]:
    s = make_session()
    rows: list[dict] = []
    for page in range(1, pages + 1):
        r = fetch(s, "https://www.workana.com/jobs", params={"language": "en", "page": page}, tag="workana")
        if r is None:
            break
        page_rows = _workana_parse(r.text)
        if not page_rows:
            break
        rows += page_rows
        polite(1.5, 2.5)
    raise_if_blocked(s, len(rows))
    return rows


# ── Truelancer — OFF by design ───────────────────────────────────────────────
register_disabled(
    "truelancer", "freelance",
    "Behind a Vercel bot-detection checkpoint (HTTP 403/429 even for ordinary fetches). "
    "Getting past it means defeating bot protection, which this scraper does not do. "
    "Needs an official API / partner feed, or a manual export.",
)
