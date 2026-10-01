"""
Scout scraper — orchestrator.

  python main.py                         # run every source, write to Supabase
  python main.py --dry-run               # scrape, print per-source health + samples, write nothing
  python main.py --only freelancer,workana --dry-run
  python main.py --type freelance        # only freelance sources
  python main.py --list                  # show sources and why any are disabled
  python main.py --pages 8               # more pages per source

Env (only needed when writing): SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

import config
from scrapers import SOURCES
from scrapers.common import SourceBlocked, classify

CHUNK = 200


# ── run sources ──────────────────────────────────────────────────────────────

def run_source(src, pages: int) -> dict:
    t0 = time.time()
    res = {"name": src.name, "type": src.type, "status": "OK", "count": 0, "secs": 0.0, "note": "", "rows": []}
    try:
        rows = src.fn(pages) or []
        res["rows"], res["count"] = rows, len(rows)
        if not rows:
            res["status"] = "EMPTY"
            res["note"] = "0 listings — selector drift, geo-block or site down. Check logs."
    except SourceBlocked as e:
        res["status"], res["note"] = "BLOCKED", str(e)
    except Exception as e:  # one broken source must never kill the run
        res["status"], res["note"] = "ERROR", f"{type(e).__name__}: {str(e)[:160]}"
    res["secs"] = round(time.time() - t0, 1)
    print(f"[{res['status']}] {src.name}: {res['count']} listings in {res['secs']}s {res['note']}")
    return res


def run_group(group_sources, pages: int) -> list[dict]:
    return [run_source(s, pages) for s in group_sources]  # same domain → sequential


# ── write to Supabase (PostgREST, no client library) ─────────────────────────

def _headers(extra=None):
    h = {"apikey": config.SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
         "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def upsert(rows: list[dict]) -> tuple[int, int, str]:
    url = f"{config.SUPABASE_URL}/rest/v1/listings"
    ok = bad = 0
    first_err = ""

    def post(batch):
        return requests.post(url, params={"on_conflict": "url"}, json=batch, timeout=60,
                             headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}))

    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        try:
            r = post(batch)
        except requests.RequestException as e:
            r = None
            first_err = first_err or f"network: {e}"
        if r is not None and r.status_code in (200, 201, 204):
            ok += len(batch)
            continue
        if r is not None:
            first_err = first_err or f"HTTP {r.status_code}: {r.text[:300]}"
        for row in batch:  # isolate the bad row(s) instead of losing the whole chunk
            try:
                rr = post([row])
                if rr.status_code in (200, 201, 204):
                    ok += 1
                else:
                    bad += 1
                    first_err = first_err or f"HTTP {rr.status_code}: {rr.text[:300]}"
            except requests.RequestException as e:
                bad += 1
                first_err = first_err or f"network: {e}"
    return ok, bad, first_err


def expire_stale(days: int, platforms: list[str]) -> str:
    """Hide listings not seen for `days` days, ONLY for platforms this run scraped successfully.
    Rows from other platforms (e.g. LinkedIn, loaded by something else) are never touched."""
    if not platforms:
        return "skipped (no platform scraped OK)"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plist = ",".join('"' + p.replace('"', "") + '"' for p in sorted(platforms))
    r = requests.patch(f"{config.SUPABASE_URL}/rest/v1/listings",
                       params={"is_active": "eq.true", "scraped_at": f"lt.{cutoff}", "platform": f"in.({plist})"},
                       json={"is_active": False}, timeout=60, headers=_headers({"Prefer": "return=minimal"}))
    return f"HTTP {r.status_code} for {len(platforms)} platforms" + ("" if r.status_code in (200, 204) else f" {r.text[:200]}")


# ── reporting ────────────────────────────────────────────────────────────────

def report(results, disabled, extra_lines) -> str:
    lines = ["## Scout scraper run", "", "| Source | Type | Status | Listings | Secs | Note |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['type']} | {r['status']} | {r['count']} | {r['secs']} | {r['note']} |")
    for s in disabled:
        lines.append(f"| {s.name} | {s.type} | OFF | 0 | - | {s.disabled} |")
    lines += [""] + extra_lines
    text = "\n".join(lines)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    for r in results:  # surface problems as GitHub annotations
        if r["status"] != "OK":
            print(f"::warning title=Scraper {r['name']} {r['status']}::{r['note']}")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Scout scraper")
    ap.add_argument("--type", choices=["internship", "freelance", "job", "all"], default="all")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--only", default="", help="comma-separated source names")
    ap.add_argument("--skip", default="", help="comma-separated source names")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-expire", action="store_true")
    args = ap.parse_args()

    if args.list:
        for s in SOURCES:
            print(f"{s.name:26} {s.type:11} {'OFF: ' + s.disabled if s.disabled else 'on'}")
        return 0

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    active, disabled = [], []
    for s in SOURCES:
        if (args.type != "all" and s.type != args.type) or (only and s.name not in only) or s.name in skip:
            continue
        (disabled if s.disabled else active).append(s)

    if not args.dry_run and not (config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY):
        print("SUPABASE_URL and SUPABASE_SERVICE_KEY are required (or use --dry-run)")
        return 2

    print(f"[scout-scraper] {len(active)} sources, pages={args.pages}, dry_run={args.dry_run}")
    groups = defaultdict(list)
    for s in active:
        groups[s.group].append(s)
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = [r for part in ex.map(lambda g: run_group(g, args.pages), groups.values()) for r in part]
    results.sort(key=lambda r: r["name"])

    # merge, dedup by url, classify
    now = datetime.now(timezone.utc).isoformat()
    seen, rows, blocked = set(), [], 0
    for r in results:
        for row in r["rows"]:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            cat, is_blocked = classify(row["title"], row.get("category") or "")
            blocked += is_blocked
            rows.append({**row, "internal_category": cat, "is_active": not is_blocked, "scraped_at": now})

    by_type = defaultdict(int)
    for row in rows:
        by_type[row["type"]] += 1
    extra = [f"Unique listings: **{len(rows)}** ({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}); "
             f"blocklisted: {blocked}"]

    code = 0
    if not rows:
        extra.append("**No listings from any source — failing the run so it is visible.**")
        code = 1
    elif args.dry_run:
        extra.append("Dry run: nothing written.")
        for r in results:
            for row in r["rows"][:2]:
                print(f"  sample [{r['name']}] {row['title']} | {row['company']} | {row['stipend']} | {row['location']} | {row['url']}")
    else:
        ok, bad, err = upsert(rows)
        extra.append(f"Upserted **{ok}**, failed **{bad}**." + (f" First error: `{err}`" if err else ""))
        if ok == 0:
            code = 2
        if not args.no_expire and ok > 0:
            ok_platforms = sorted({row["platform"] for r in results if r["status"] == "OK" for row in r["rows"]})
            extra.append(f"Expired listings not seen for {config.STALE_DAYS} days: {expire_stale(config.STALE_DAYS, ok_platforms)}")

    print("\n" + report(results, disabled, extra))
    return code


if __name__ == "__main__":
    sys.exit(main())
