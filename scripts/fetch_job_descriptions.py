#!/usr/bin/env python3

"""
Fetch and archive full job descriptions for saved/applied jobs, and flag
postings that are no longer live.

Input: a job-tracker-export-*.tsv file (from the web app's "Export Saved &
Applied (TSV)" button). Columns expected: Status, Company, Title, Location,
ATS, Job Updated, Status Set On, URL.

Output: one Markdown file per job in --out-dir (default saved_job_postings/),
plus a cumulative _status_report.tsv summarizing every job checked so far.

Resumable by design: every job checked is logged in _status_report.tsv by URL,
and a URL already in that report is skipped on the next run unless --force is
passed. The one exception is a posting whose tracker "Job Updated" date is
newer than our last check - that means it was re-listed after we wrote it off,
so it gets fetched again. Safe to Ctrl-C or run out of budget mid-way through,
just re-run the same command to continue where it left off. Use --limit to
deliberately cap how many *new* jobs a single run processes.

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

# Blank lines placed between stacked captures in an archive file.
CAPTURE_SEPARATOR = "\n\n\n\n"
CAPTURE_START_RE = re.compile(r"^---\ncompany: ", re.MULTILINE)

NAV_TIMEOUT_MS = 25_000
SETTLE_WAIT_MS = 1_500
DESCRIPTION_SELECTOR_TIMEOUT_MS = 6_000

# Per-ATS containers holding just the job description. Matching one of these
# (rather than reading the whole <body>) is what separates "posting closed but
# the description is still served" from "redirected to the board index" - the
# latter is also a long, healthy-looking page, but none of it is the JD.
DESCRIPTION_SELECTORS = [
    ("myworkdayjobs.com", '[data-automation-id="jobPostingDescription"]'),
    ("greenhouse.io", ".job__description, .job-post-content, #content"),
    ("ashbyhq.com", '[class*="_descriptionText"], [class*="descriptionText"]'),
    ("oraclecloud.com", '.job-details__description-content, .job-details__description'),
    ("ats.rippling.com", '[class*="jobDescription"], [class*="description"]'),
    ("ultipro.com", '[data-automation="job-description"], .opportunity-description'),
    ("bamboohr.com", '#job-description, .jss-description'),
    ("lever.co", ".posting-content, .section-wrapper"),
    ("smartrecruiters.com", ".job-sections, .jobad-main"),
]


def slugify(text: str, max_len: int = 50) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def output_path(out_dir: Path, company: str, title: str, url: str) -> Path:
    return out_dir / f"{slugify(company)}__{slugify(title)}__{url_hash(url)}.md"


async def extract_description(page, url: str):
    """The JD text from a dedicated description container, or None if the page
    doesn't have one. None means "this page is not serving a job description",
    which is different from "the page is short"."""
    for pattern, selector in DESCRIPTION_SELECTORS:
        if pattern not in url:
            continue
        try:
            await page.wait_for_selector(selector, timeout=DESCRIPTION_SELECTOR_TIMEOUT_MS)
            text = (await page.inner_text(selector)).strip()
            if len(text) >= MIN_ACTIVE_LENGTH:
                return text
        except Exception:
            pass  # container absent or empty - not a JD page
    return None


async def fetch_job_page(browser, url: str) -> dict:
    """Returns {status, description, notes}. status in ACTIVE/INACTIVE/UNKNOWN/ERROR.

    A closed posting still gets its description saved when the page serves one.
    Whether the role is open and whether the text is worth keeping are separate
    questions, and plenty of ATS platforms show a "no longer accepting
    applications" banner above the full, intact description.
    """
    page = await browser.new_page()
    try:
        response = None
        try:
            response = await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        except Exception:
            # Some sites never go fully idle (analytics beacons etc.) - a
            # slower "load" wait is a reasonable fallback before giving up.
            response = await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)

        await page.wait_for_timeout(SETTLE_WAIT_MS)

        body = await page.inner_text("body")
        description = await extract_description(page, url)

        # Evidence worth keeping in the report when confirming a posting is
        # really gone: the HTTP status, and whether the site bounced us off the
        # job URL entirely (several ATS redirect dead postings to the board).
        evidence = []
        if response is not None and response.status != 200:
            evidence.append(f"http {response.status}")
        if page.url.rstrip("/") != url.rstrip("/"):
            evidence.append(f"redirected to {page.url}")

        lowered = body.lower()
        matched = next((m for m in INACTIVE_MARKERS if m in lowered), None)

        if matched:
            notes = [f'matched: "{matched}"']
            if description:
                notes.append("description still served - archived")
            else:
                notes.append("no description on page")
            return {
                "status": "INACTIVE",
                "description": description or "",
                "notes": "; ".join(notes + evidence),
            }

        text = description or body
        if len(text.strip()) >= MIN_ACTIVE_LENGTH:
            return {"status": "ACTIVE", "description": text, "notes": "; ".join(evidence)}
        return {
            "status": "UNKNOWN",
            "description": text,
            "notes": "; ".join(["short/unrecognized page - check manually"] + evidence),
        }

    except Exception as e:
        return {"status": "ERROR", "description": "", "notes": str(e)[:200]}
    finally:
        await page.close()


def split_captures(text: str) -> list:
    """Splits an archive file into its capture blocks, newest first.

    Every capture begins with a frontmatter fence whose first key is
    `company:`, which is what marks the boundary between stacked captures."""
    starts = [m.start() for m in CAPTURE_START_RE.finditer(text)]
    if not starts:
        return []
    bounds = starts + [len(text)]
    return [text[bounds[i]:bounds[i + 1]].strip() for i in range(len(starts))]


def capture_description(block: str) -> str:
    """The description text of one capture block, minus its frontmatter."""
    parts = block.split("\n---\n", 1)
    return parts[1].strip() if len(parts) == 2 else block.strip()


def write_job_file(path: Path, row: dict, result: dict, checked_at: str) -> str:
    """Adds this capture to the job's archive file, newest at the top.

    A posting can be pulled and later re-listed under the same URL, so each
    file is a running archive rather than a single snapshot: every capture
    keeps its own frontmatter, blocks are separated by blank lines, and the
    most recent one is first. A capture identical to the one already at the
    top is not added again - re-checks are triggered by the ATS's own
    "updated" date, which often moves without the description changing.

    Returns "new", "reposted", or "unchanged".
    """
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
    block = header + result["description"]

    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return "new"

    existing = path.read_text(encoding="utf-8")
    captures = split_captures(existing)
    if captures and capture_description(captures[0]) == result["description"].strip():
        return "unchanged"

    path.write_text(block.rstrip() + CAPTURE_SEPARATOR + existing.lstrip(), encoding="utf-8")
    return "reposted"


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


def parse_timestamp(value: str):
    """Lenient ISO-8601 parse for report and tracker timestamps. Returns a
    timezone-aware datetime, or None when the field is empty or unparseable
    (the tracker leaves 'Job Updated' blank for some ATS platforms)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_last_checked(report_path: Path) -> dict:
    """Maps URL -> the most recent checked_at recorded for it.

    The status report (not the presence of a .md file) is the source of truth
    for 'already checked' - inactive jobs are logged there but don't get a
    saved file, so file-existence alone can't be used to resume. The report is
    append-only and --force can add a second row for the same URL, so the
    latest timestamp wins.
    """
    if not report_path.exists():
        return {}
    last = {}
    with open(report_path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            url = row["url"]
            checked_at = parse_timestamp(row.get("checked_at", ""))
            previous = last.get(url)
            if previous is None or (checked_at is not None and checked_at > previous):
                last[url] = checked_at
    return last


async def run(tsv_path: Path, out_dir: Path, force: bool, limit: int, delay: float):
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "_status_report.tsv"

    with open(tsv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    last_checked = {} if force else load_last_checked(report_path)

    total = len(rows)
    processed_this_run = 0
    skipped = 0
    repostings = []
    unchanged = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for i, row in enumerate(rows, start=1):
                url = row["URL"].strip()
                if not url:
                    continue

                is_recheck = False
                if url in last_checked:
                    previous_check = last_checked[url]
                    job_updated = parse_timestamp(row.get("Job Updated", ""))
                    # A posting can be pulled and later re-listed under the same
                    # URL. When the tracker has seen it updated since our last
                    # look, the recorded status is out of date - check it again.
                    if not (previous_check and job_updated and job_updated > previous_check):
                        skipped += 1
                        print(f"[{i}/{total}] SKIP (already checked): {row['Company']} - {row['Title']}")
                        continue
                    is_recheck = True

                if limit is not None and processed_this_run >= limit:
                    print(f"\nReached --limit {limit} new job(s) this run. "
                          f"Re-run the same command to continue with the rest.")
                    break

                label = "Re-fetching (posting updated since last check)" if is_recheck else "Fetching"
                print(f"[{i}/{total}] {label}: {row['Company']} - {row['Title']} ({url})")
                result = await fetch_job_page(browser, url)
                checked_at = datetime.now(timezone.utc).isoformat()

                # Save whenever there's real text to keep. A closed posting
                # that still serves its description is worth archiving; one
                # that serves only a "job not found" shell is not.
                outcome = ""
                if result["description"].strip():
                    out_path = output_path(out_dir, row["Company"], row["Title"], url)
                    outcome = write_job_file(out_path, row, result, checked_at)
                    if outcome == "reposted":
                        repostings.append((row["Company"], row["Title"], out_path))
                    elif outcome == "unchanged":
                        unchanged += 1

                append_report_row(report_path, row, result, checked_at)
                suffix = {
                    "reposted": " [REPOSTED - description changed, added at top of file]",
                    "unchanged": " [same text as last capture, file left alone]",
                }.get(outcome, "")
                print(f"    -> {result['status']} {result['notes']}{suffix}")

                processed_this_run += 1
                await asyncio.sleep(delay)
        finally:
            await browser.close()

    print(f"\nDone this run: {processed_this_run} fetched, {skipped} already done, "
          f"{total} total in TSV.")
    if unchanged:
        print(f"Re-checked but unchanged: {unchanged} (ATS moved the date, description did not)")

    # Printed as a distinct block so it's easy to spot at the end of a long
    # run - these are the jobs worth actually looking at.
    if repostings:
        print(f"\nREPOSTINGS ({len(repostings)}): description changed since the previous capture.")
        for company, title, out_path in repostings:
            print(f"  - {company} - {title}")
            print(f"    {out_path}")
        print("  Newest text is at the top of each file; the previous capture follows below it.")

    print(f"\nOutput: {out_dir}/  |  Report: {report_path}")


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
