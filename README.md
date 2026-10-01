# Scout scraper

Scrapes internships, freelance gigs and jobs into `public.listings` (Supabase), twice a day via GitHub Actions.

## Sources
Run `python main.py --list`. Summary:

| Source | Type | Notes |
|---|---|---|
| internshala-internships / -jobs | internship, job | global feed + work-from-home + 12 category pages |
| unstop-internships / -jobs | internship, job | public API |
| freelancer | freelance | public read API, no key |
| workana | freelance | HTML; selectors unverified, falls back to `/job/` links |
| guru | freelance | HTML; only ~20 listings (site paginates client-side) |
| freshersworld | job | HTML |
| weworkremotely, jobicy, remoteok | job | remote boards; senior roles filtered out |
| truelancer | freelance | **OFF**: Vercel bot-detection checkpoint. Not bypassed. Needs an official feed. |

## Run
```
pip install -r requirements.txt
python main.py --dry-run                 # health table + samples, writes nothing
python main.py --only freelancer,workana --dry-run
python main.py                           # real run (needs SUPABASE_URL, SUPABASE_SERVICE_KEY)
```
On GitHub: Actions > Scout Scraper > Run workflow > tick `dry_run` first. The run summary shows a per-source
table (OK / EMPTY / BLOCKED / ERROR). An EMPTY or BLOCKED source gets a yellow annotation; a run with zero
listings, or one where the database write fails completely, fails red.

## What it does to the database
- Upserts on `url` in batches of 200 (PostgREST, no client library).
- Sets `internal_category` itself (port of the classify-listing edge function) and hides blocklisted roles.
- Hides listings not re-seen for 21 days (`STALE_DAYS`).
- Platform naming: internships/jobs lowercase (`internshala`, `unstop`), freelance capitalised
  (`Workana`, `Guru`, `Freelancer`) because the app's onboarding filter matches those exact strings.
