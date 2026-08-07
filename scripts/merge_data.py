import json
import gzip
from pathlib import Path
from datetime import datetime, timezone


def merge_job_data():
    """Merge new scrape with existing data, removing stale jobs."""

    # Load new scraped data
    new_path = Path("scripts/output/all_jobs.json.gz")
    with gzip.open(new_path, "rt", encoding="utf-8") as f:
        new_jobs = json.load(f)
    print(f"New scrape: {len(new_jobs):,} jobs")

    # Load existing data (if exists)
    existing_jobs = []
    existing_path = Path("data/all_jobs.json.gz")
    if existing_path.exists():
        with gzip.open(existing_path, "rt", encoding="utf-8") as f:
            existing_jobs = json.load(f)
        print(f"Existing data: {len(existing_jobs):,} jobs")

    # Load tracked job URLs (any status: saved/applied/ignored) exported from the
    # web UI's "Export Tracked URLs (JSON)" button. Tracked jobs are kept regardless
    # of age, since a job that's disappeared from the live listing shouldn't vanish
    # from someone's tracking just because it's more than 30 days old.
    tracked_urls = set()
    tracked_path = Path("data/tracked_urls.json")
    if tracked_path.exists():
        with open(tracked_path, "r", encoding="utf-8") as f:
            tracked_urls = set(json.load(f).keys())
        print(f"Loaded {len(tracked_urls):,} tracked (saved/applied/ignored) URLs")

    # Merge by URL
    merged = {}
    stale_count = 0

    # Add existing jobs first (with age filter)
    for job in existing_jobs:
        url = job.get("absolute_url") or job.get("url")
        if not url:
            continue

        if url in tracked_urls:
            merged[url] = job
            continue

        # Keep jobs scraped within last 30 days
        scraped = job.get("scraped_at")
        if scraped:
            try:
                scraped_date = datetime.fromisoformat(scraped.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_days = (now - scraped_date).days

                if age_days <= 30:
                    merged[url] = job
                else:
                    stale_count += 1
            except Exception as e:
                # If date parsing fails, keep the job but surface it - a silently
                # swallowed parse error here previously masked the age filter entirely.
                print(f"Warning: couldn't parse scraped_at ({scraped!r}) for {url}: {e}")
                merged[url] = job
        else:
            # No scraped_at field, keep it
            merged[url] = job

    if stale_count > 0:
        print(f"Dropped {stale_count:,} stale jobs (>30 days old, untracked)")

    # Add/update with new scrape (always wins on duplicates)
    for job in new_jobs:
        url = job.get("absolute_url") or job.get("url")
        if url:
            merged[url] = job

    # Convert to list
    final_jobs = list(merged.values())
    print(f"Merged result: {len(final_jobs):,} jobs")

    # Ensure data directory exists
    Path("data").mkdir(exist_ok=True)

    # Save merged data
    with gzip.open("data/all_jobs.json.gz", "wt", encoding="utf-8") as f:
        json.dump(final_jobs, f)

    # Update metadata
    with open("scripts/output/metadata.json", "r") as f:
        metadata = json.load(f)

    metadata["total_jobs"] = len(final_jobs)

    with open("data/metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print("Merge complete")
    return len(final_jobs)


if __name__ == "__main__":
    merge_job_data()
