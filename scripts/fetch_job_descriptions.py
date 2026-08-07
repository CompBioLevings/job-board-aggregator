#!/usr/bin/env python3

"""
Fetch and archive full job descriptions for saved/applied jobs, and flag
postings that are no longer live.

Input: a job-tracker-export-*.tsv file (from the web app's "Export Saved &
Applied (TSV)" button). Columns expected: Status, Company, Title, Location,
ATS, Job Updated, Status Set On, URL.

Output: one Markdown file per job in --out-dir (default saved_job_postings/),
plus a cumulative _status_report.tsv summarizing every job checked so far.

Resumable by design: each job's output filename is derived from a hash of its
URL, so a job already processed (i.e. its file already exists) is skipped on
the next run unless --force is passed. Safe to Ctrl-C or run out of budget
mid-way through — just re-run the same command to continue where it left off.
Use --limit to deliberately cap how many *new* jobs a single run processes.

Runs against real browser rendering (Playwright/Chromium) because most of
these ATS platforms either block plain HTTP requests to their job-detail
APIs or serve an empty JS-only shell with no server-rendered content.

Usage:
    conda run -n claude python3 scripts/fetch_job_descriptions.py \\
        job-tracker-export-2026-08-07.tsv --limit 20
"""

import argparse
import asyncio
import csv
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

# Substrings (checked case-insensitively) that indicate a posting has been
# pulled down, regardless of which ATS is serving the page. Collected by
# spot-checking a real example on each platform in this codebase's job list.
INACTIVE_MARKERS = [
    "no longer open",
    "no longer available",
    "no longer accepting applicant",
    "job you are looking for is no longer open",
    "page you are looking for doesn't exist",
    "job not found",
    "opportunity is currently not available",
    "job post is no longer available",
    "page not found",
    "404 error",
    "this position has been filled",
    "this requisition is no longer active",
]

# Below this many characters, an unrecognized page is treated as ambiguous
# (UNKNOWN) rather than guessed as ACTIVE - real descriptions are long.
MIN_ACTIVE_LENGTH = 200

NAV_TIMEOUT_MS = 25_000
SETTLE_WAIT_MS = 1_500
WORKDAY_SELECTOR_TIMEOUT_MS = 6_000
WORKDAY_DESCRIPTION_SELECTOR = '[data-automation-id="jobPostingDescription"]'


def slugify(text: str, max_len: int = 50) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def output_path(out_dir: Path, company: str, title: str, url: str) -> Path:
    return out_dir / f"{slugify(company)}__{slugify(title)}__{url_hash(url)}.md"


async def fetch_job_page(browser, url: str) -> dict:
    """Returns {status, description, notes}. status in ACTIVE/INACTIVE/UNKNOWN/ERROR."""
    page = await browser.new_page()
    try:
        try:
            await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        except Exception:
            # Some sites never go fully idle (analytics beacons etc.) - a
            # slower "load" wait is a reasonable fallback before giving up.
            await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)

        await page.wait_for_timeout(SETTLE_WAIT_MS)

        description = None
        if "myworkdayjobs.com" in url:
            try:
                await page.wait_for_selector(
                    WORKDAY_DESCRIPTION_SELECTOR, timeout=WORKDAY_SELECTOR_TIMEOUT_MS
                )
                description = await page.inner_text(WORKDAY_DESCRIPTION_SELECTOR)
            except Exception:
                pass  # fall through to full-body text below

        if not description:
            description = await page.inner_text("body")

        lowered = description.lower()
        matched = next((m for m in INACTIVE_MARKERS if m in lowered), None)

        if matched:
            return {"status": "INACTIVE", "description": description, "notes": f'matched: "{matched}"'}
        if len(description.strip()) >= MIN_ACTIVE_LENGTH:
            return {"status": "ACTIVE", "description": description, "notes": ""}
        return {"status": "UNKNOWN", "description": description, "notes": "short/unrecognized page - check manually"}

    except Exception as e:
        return {"status": "ERROR", "description": "", "notes": str(e)[:200]}
    finally:
        await page.close()


def write_job_file(path: Path, row: dict, result: dict, checked_at: str):
    header = (
        "---\n"
        f"company: {row['Company']}\n"
        f"title: {row['Title']}\n"
        f"location: {row.get('Location', '')}\n"
        f"your_tag: {row['Status']}\n"
        f"ats: {row.get('ATS', '')}\n"
        f"url: {row['URL']}\n"
        f"checked_at: {checked_at}\n"
        f"posting_status: {result['status']}\n"
        f"notes: {result['notes']}\n"
        "---\n\n"
    )
    path.write_text(header + result["description"], encoding="utf-8")


def append_report_row(report_path: Path, row: dict, result: dict, checked_at: str):
    is_new = not report_path.exists()
    with open(report_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        if is_new:
            writer.writerow(["checked_at", "company", "title", "your_tag", "posting_status", "notes", "url"])
        writer.writerow([
            checked_at, row["Company"], row["Title"], row["Status"],
            result["status"], result["notes"], row["URL"],
        ])


def load_checked_urls(report_path: Path) -> set:
    """The status report (not the presence of a .md file) is the source of
    truth for 'already checked' - inactive jobs are logged there but don't
    get a saved file, so file-existence alone can't be used to resume."""
    if not report_path.exists():
        return set()
    with open(report_path, encoding="utf-8") as f:
        return {row["url"] for row in csv.DictReader(f, delimiter="\t")}


async def run(tsv_path: Path, out_dir: Path, force: bool, limit: int, delay: float):
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "_status_report.tsv"

    with open(tsv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    checked_urls = set() if force else load_checked_urls(report_path)

    total = len(rows)
    processed_this_run = 0
    skipped = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for i, row in enumerate(rows, start=1):
                url = row["URL"].strip()
                if not url:
                    continue

                if url in checked_urls:
                    skipped += 1
                    print(f"[{i}/{total}] SKIP (already checked): {row['Company']} - {row['Title']}")
                    continue

                if limit is not None and processed_this_run >= limit:
                    print(f"\nReached --limit {limit} new job(s) this run. "
                          f"Re-run the same command to continue with the rest.")
                    break

                print(f"[{i}/{total}] Fetching: {row['Company']} - {row['Title']} ({url})")
                result = await fetch_job_page(browser, url)
                checked_at = datetime.now(timezone.utc).isoformat()

                # Only worth saving a file when there's real content to keep -
                # inactive/error postings just get logged in the report.
                if result["status"] in ("ACTIVE", "UNKNOWN"):
                    out_path = output_path(out_dir, row["Company"], row["Title"], url)
                    write_job_file(out_path, row, result, checked_at)

                append_report_row(report_path, row, result, checked_at)
                print(f"    -> {result['status']} {result['notes']}")

                processed_this_run += 1
                await asyncio.sleep(delay)
        finally:
            await browser.close()

    print(f"\nDone this run: {processed_this_run} fetched, {skipped} already done, "
          f"{total} total in TSV.")
    print(f"Output: {out_dir}/  |  Report: {report_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv_path", type=Path, help="Path to job-tracker-export-*.tsv")
    ap.add_argument("--out-dir", type=Path, default=Path("saved_job_postings"))
    ap.add_argument("--force", action="store_true", help="Re-fetch jobs even if already downloaded")
    ap.add_argument("--limit", type=int, default=None, help="Max number of NEW jobs to process this run")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds to wait between requests")
    args = ap.parse_args()

    if not args.tsv_path.exists():
        print(f"File not found: {args.tsv_path}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(args.tsv_path, args.out_dir, args.force, args.limit, args.delay))


if __name__ == "__main__":
    main()
