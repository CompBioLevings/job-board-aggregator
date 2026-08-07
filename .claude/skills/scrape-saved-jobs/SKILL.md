---
name: scrape-saved-jobs
description: Verify which of your saved/applied jobs are still live and archive their full descriptions to saved_job_postings/. Use when the user wants to check their tracked job postings for staleness or wants a local copy of job descriptions for saved/applied jobs.
---

# Scrape Saved Jobs

Checks every job you've tagged Saved or Applied in the web app, records
whether the posting is still live, and archives the full description text
for the ones that are. Built for periodic runs (every few weeks, a few dozen
jobs at a time) rather than a one-time bulk import.

## Prerequisites

- The job board web app running locally (`http://localhost:8000` via
  `auto_run.sh` or `python -m http.server 8000`)
- Playwright installed in the `claude` conda env (one-time setup, see below)

## Step 1: Export the tracked jobs TSV

In the browser, on the job board page, click **"Export Saved & Applied
(TSV)"**. This downloads `job-tracker-export-<date>.tsv`. Move/save it into
the repo root (or anywhere you'll reference by path).

## Step 2: Run the scraper

```bash
conda run -n claude python3 scripts/fetch_job_descriptions.py \
    job-tracker-export-<date>.tsv --limit 30
```

- `--limit N` caps how many *new* jobs this run processes (default:
  unlimited). Keep this modest (20-40) so a single run finishes in a few
  minutes and is easy to checkpoint.
- Safe to interrupt (Ctrl-C, or just stop) at any point. **Re-run the exact
  same command to continue** - already-checked jobs (tracked in
  `saved_job_postings/_status_report.tsv` by URL) are skipped automatically,
  so nothing is re-fetched or duplicated.
- Add `--force` to re-check everything from scratch (e.g. if you want to
  re-verify jobs that were ACTIVE a while ago).
- Add `--delay 2` to slow down requests further if you want to be extra
  polite to a company's site (default 1.5s between jobs).

Keep re-running with `--limit` until the tool prints `Done this run: N
fetched, M already done` with nothing left to fetch.

## Step 3: Read the results

- `saved_job_postings/_status_report.tsv` - one row per job ever checked:
  company, title, your tag (saved/applied), ACTIVE/INACTIVE/UNKNOWN, notes,
  URL. Scan this first to see what's gone stale.
- `saved_job_postings/*.md` - one file per **ACTIVE** (or ambiguous
  UNKNOWN) job, with the full description text. **Inactive jobs deliberately
  get no file** - just a report row - since there's nothing worth keeping
  from a "this job no longer exists" page.

Typical result: most saved/applied jobs from more than ~2 months ago will
show INACTIVE (postings on Workday, Greenhouse, etc. commonly close or get
pulled within weeks). That's expected, not a bug.

## How it works (for future maintenance)

Every ATS platform behind these URLs (Workday, Greenhouse, Ashby, Oracle
Cloud, BambooHR, UltiPro, Rippling, generic company career pages) was tested
and found to either block plain HTTP requests to their job-detail APIs or
serve a JS-only empty shell. So the script uses a real headless browser
(Playwright/Chromium) to render every page, uniformly, rather than
maintaining a different API integration per platform.

- **Workday** pages get a targeted selector
  (`[data-automation-id="jobPostingDescription"]`) for a cleaner description;
  everything else just takes the full rendered page's visible text.
- **Active vs. inactive** is determined by scanning the rendered text for
  known "this job is gone" phrases (see `INACTIVE_MARKERS` in
  `scripts/fetch_job_descriptions.py`) plus a minimum-length check - very
  short, unrecognized pages are marked `UNKNOWN` rather than guessed.

**If a new ATS platform shows up in your tracked jobs and comes back
UNKNOWN or wrongly ACTIVE/INACTIVE:** open one of its URLs, look at what the
"job removed" page actually says, and add that phrase to `INACTIVE_MARKERS`
in `scripts/fetch_job_descriptions.py`.

## One-time setup (if Playwright isn't installed yet)

```bash
conda run -n claude python -m pip install playwright
conda run -n claude playwright install chromium
```

This lives in the `claude` conda env deliberately, separate from the
project's `jobagg` env used by `scraper.py`/`merge_data.py` - keeps the
core scraping pipeline's dependencies light since this is a heavier,
occasional-use tool.
