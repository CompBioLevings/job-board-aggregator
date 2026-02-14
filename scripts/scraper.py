import requests
import json
import random
import time
import re
import os
import gzip
import argparse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urljoin, urlparse

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
GREENHOUSE_FILE = os.path.join(ROOT_DIR, "data", "greenhouse_companies.json")
ASHBY_FILE = os.path.join(ROOT_DIR, "data", "ashby_companies.json")
BAMBOOHR_FILE = os.path.join(ROOT_DIR, "data", "bamboohr_companies.json")
WORKDAY_FILE = os.path.join(ROOT_DIR, "data", "workday_companies.json")
WORKDAYSITE_FILE = os.path.join(ROOT_DIR, "data", "workdaysite_companies.json")
LEVER_FILE = os.path.join(ROOT_DIR, "data", "lever_companies.json")

OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

RECRUITER_TERMS = [
    "recruit",
    "recruiting",
    "recruiter",
    "staffing",
    "staff",
    "talent",
    "talenthub",
    "talentgroup",
    "solutions",
    "consulting",
    "placement",
    "search",
    "resources",
    "agency",
]

USER_AGENTS = [
    # Chrome 144 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Chrome 144 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Chrome 144 - Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Firefox 147 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Firefox 147 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Firefox 147 - Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Safari 26 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
    # Edge 144 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
]


# ============================================================
# LOAD COMPANIES
# ============================================================

def load_companies(filepath):
    """Load companies from JSON file."""
    try:
        with open(filepath, "r") as f:
            companies = set(json.load(f))
        print(f"Loaded {len(companies):,} companies from {filepath}")
        return companies
    except FileNotFoundError:
        print(f"File not found: {filepath}")
        return set()


# ============================================================
# VERIFY ACTIVE JOBS + FETCH ALL JOBS
# ============================================================

# API requests for testing in browser console
"""
fetch("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    operationName: "ApiJobBoardWithTeams",
    variables: {organizationHostedJobsPageName: "zip"},
    query: "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id title locationName } } }"
  })
}).then(r => r.json()).then(console.log)

fetch("https://{slug}.bamboohr.com/careers/list"){
    method: "GET",
    headers: {"Content-Type": "application/json"},
}.then(r => r.json()).then(console.log)

}
"""


SOURCE_TYPE = "automated"

def get_job_metadata():
    """Generate consistent metadata for each job."""
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": SOURCE_TYPE
    }

def fetch_company_jobs_greenhouse(slug):
    """Fetch all jobs for a company."""
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        response = requests.get(url, timeout=30)

        if response.status_code == 200:
            data = response.json()
            jobs = data.get("jobs", [])

            if jobs:
                # Normalize job structure for frontend
                normalized = []
                for job in jobs:
                    # Get location and filter
                    location = job.get("location", {}).get("name", "Not specified")
                    if not is_valid_location(location):
                        continue
                    normalized.append(
                        {
                            "company": slug,
                            "company_slug": slug,
                            "title": job.get("title"),
                            "location": job.get("location", {}).get(
                                "name", "Not specified"
                            ),
                            "url": job.get("absolute_url"),
                            "absolute_url": job.get("absolute_url"),
                            "departments": [
                                d.get("name") for d in job.get("departments", [])
                            ],
                            "id": job.get("id"),
                            "updated_at": job.get("updated_at"),
                            "is_recruiter": is_recruiter_company(slug),
                            "ats": "Greenhouse",
                            **get_job_metadata()
                        }
                    )

                return slug, normalized

    except Exception as e:
        pass

    return slug, []


def fetch_company_jobs_ashby(slug):
    try:
        url = f"https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
        payload = {
            "operationName": "ApiJobBoardWithTeams",
            "variables": {"organizationHostedJobsPageName": slug},
            "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id title locationName } } }",
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; JobFetcher/1.0)",
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            data = response.json()
            jobs = data.get("data", {}).get("jobBoard", {}).get("jobPostings", [])

            if jobs:
                normalized = []
                for job in jobs:
                    location = job.get("locationName", "Not specified")
                    if not is_valid_location(location):
                        continue
                    normalized.append(
                        {
                            "company": slug,
                            "company_slug": slug,
                            "title": job.get("title", ""),
                            "location": location,
                            "updated_at": None,
                            "url": f"https://jobs.ashbyhq.com/{slug}/jobs/{job.get('id')}",
                            "is_recruiter": is_recruiter_company(slug),
                            "ats": "Ashby",
                            **get_job_metadata()
                        }
                    )
                return slug, normalized
    except Exception as e:
        pass
    return slug, []


def fetch_company_jobs_bamboohr(slug):
    """https://{slug}.bamboohr.com/careers
    https://{slug}.bamboohr.com/careers/list

    """

    try:
        url = f"https://{slug}.bamboohr.com/careers/list"
        response = requests.get(url, timeout=30)

        if response.status_code == 200:
            data = response.json()
            jobs = data.get("result", [])

            if jobs:
                normalized = []
                for job in jobs:
                    location = job.get("location", "Not specified")
                    if location != "Not specified":
                        location = location['city'] + ', ' + location['state']
                    if not is_valid_location(location):
                        continue
                    normalized.append(
                        {
                            "company": slug,
                            "company_slug": slug,
                            "title": job.get("jobOpeningName"),
                            "location": location,
                            "updated_at": None,
                            "url": f"https://{slug}.bamboohr.com/careers/view/{job.get('id')}",
                            "is_recruiter": is_recruiter_company(slug),
                            "ats": "BambooHR",
                            **get_job_metadata()
                        }
                    )
                return slug, normalized
    except Exception as e:
        pass
    return slug, []


def fetch_company_jobs_lever(slug):
    """https://api.lever.co/v0/postings/{slug}"""

    try:
        url = f"https://api.lever.co/v0/postings/{slug}"
        response = requests.get(url, timeout=30)

        if response.status_code == 200:
            jobs = response.json()

            if jobs:
                normalized = []
                for job in jobs:
                    categories = job.get("categories", {})
                    location = categories.get("location", "Not specified")
                    if not is_valid_location(location):
                        continue
                    # Get corrected date
                    posted_on = job.get("createdAt")
                    posted_on_parsed = parse_unix_timestamp(posted_on)
                    normalized.append(
                        {
                            "company": slug,
                            "company_slug": slug,
                            "title": job.get("text"),
                            "location": categories.get("location", "Not specified") [:50],
                            "url": job.get("hostedUrl"),
                            "updated_at": posted_on_parsed,
                            "is_recruiter": is_recruiter_company(slug),
                            "ats": "Lever",
                            **get_job_metadata()
                        }
                    )
                return slug, normalized
    except Exception as e:
        pass
    return slug, []


def fetch_company_jobs_workday(slug):
    """
    slug format: "company|wd#|site_id" e.g. "kohls|wd1|kohlscareers"
    url: https://{company}.wd{num}.myworkdayjobs.com/wday/cxs/{company}/{site_id}/jobs
    """

    try:
        parts = slug.split("|")
        if len(parts) != 3:
            return slug, []

        company, wd, site_id = parts
        wd_num = wd.replace("wd", "")

        base_url = f"https://{company}.wd{wd_num}.myworkdayjobs.com"
        career_url = f"https://{company}.wd{wd_num}.myworkdayjobs.com/{site_id}"
        api_url = f"{base_url}/wday/cxs/{company}/{site_id}/jobs"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": random.choice(USER_AGENTS),
            "Origin": base_url,
            "Referer": f"{base_url}/{site_id}",
        }

        normalized = []
        offset = 0
        limit = 20
        retries = 0
        max_retries = 2
        observed_total = None

        while True:
            payload = {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            }

            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30,
            )

            if response.status_code != 200:
                if retries < max_retries:
                    retries += 1
                    time.sleep(random.uniform(2.0, 4.0))
                    continue
                break

            data = response.json()
            jobs = data.get("jobPostings", [])
            total = data.get("total", 0)

            # Detect silent blocking / truncation
            if observed_total is None:
                observed_total = total
            elif total != observed_total:
                # Workday sometimes lies mid-pagination when blocking
                break

            if not jobs:
                break

            for job in jobs:
                job_path = job.get("externalPath", "")
                # Get corrected date
                posted_on = job.get("postedOn")
                posted_on_parsed = parse_relative_date(posted_on)
                # Get location and filter
                location = job.get("locationsText", "Not specified")
                if not is_valid_location(location):
                    continue
                normalized.append(
                    {
                        "company": company,
                        "company_slug": slug,
                        "title": job.get("title"),
                        "location": job.get("locationsText", "Not specified") [:50],
                        "url": f"{career_url}{job_path}",
                        "updated_at": posted_on_parsed,
                        "is_recruiter": is_recruiter_company(company),
                        "ats": "Workday",
                        **get_job_metadata()
                    }
                )
            # List possible queries from job
            
            offset += limit

            if offset >= total:
                break

            # Jitter between pages (critical)
            time.sleep(random.uniform(0.8, 1.8))

        return slug, normalized

    except Exception:
        return slug, []

def fetch_company_jobs_workdaysite(slug):
    """
    slug format: "company|wd#|site_id" e.g. "upenn|wd1|upenn/careers-at-upenn"
    
    url: https://wd{num}.myworkdaysite.com/wday/cxs/{site_id}/jobs
    """

    try:
        parts = slug.split("|")
        if len(parts) != 3:
            return slug, []

        company, wd, site_id = parts
        wd_num = wd.replace("wd", "")

        base_url = f"https://wd{wd_num}.myworkdaysite.com"
        career_url = f"{base_url}/en-US/recruiting/{site_id}"
        api_url = f"{base_url}/wday/cxs/{site_id}/jobs"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": random.choice(USER_AGENTS),
            "Origin": base_url,
            "Referer": f"{base_url}/en-US/recruiting/{site_id}",
        }

        normalized = []
        offset = 0
        limit = 20
        retries = 0
        max_retries = 2
        observed_total = None

        while True:
            payload = {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            }

            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30,
            )

            if response.status_code != 200:
                if retries < max_retries:
                    retries += 1
                    time.sleep(random.uniform(2.0, 4.0))
                    continue
                break

            data = response.json()
            jobs = data.get("jobPostings", [])
            total = data.get("total", 0)

            # Detect silent blocking / truncation
            if observed_total is None:
                observed_total = total
            elif total != observed_total:
                # Workday sometimes lies mid-pagination when blocking
                break

            if not jobs:
                break

            for job in jobs:
                job_path = job.get("externalPath", "")
                # Get corrected date
                posted_on = job.get("postedOn")
                posted_on_parsed = parse_relative_date(posted_on)
                # Get location and filter
                location = job.get("locationsText", "Not specified")
                if not is_valid_location(location):
                    continue
                normalized.append(
                    {
                        "company": company,
                        "company_slug": slug,
                        "title": job.get("title"),
                        "location": job.get("locationsText", "Not specified") [:50],
                        "url": f"{career_url}{job_path}",
                        "updated_at": posted_on_parsed,
                        "is_recruiter": is_recruiter_company(company),
                        "ats": "WorkdaySite",
                        **get_job_metadata()
                    }
                )
            # List possible queries from job
            
            offset += limit

            if offset >= total:
                break

            # Jitter between pages (critical)
            time.sleep(random.uniform(0.8, 1.8))

        return slug, normalized

    except Exception:
        return slug, []


def fetch_company_jobs_generic(slug):
    """
    Generic scraper for simple career pages that serve HTML job listings.

    - `slug` may be a full URL (preferred) or a hostname/path fragment.
    - Uses heuristic HTML parsing to find anchors that look like job links and
      attempts to extract title + location.
    - Returns (slug, normalized_jobs) to match other fetchers.
    """
    try:
        # Accept full URLs or build a URL from slug
        if isinstance(slug, str) and slug.startswith("http"):
            url = slug
        else:
            url = f"https://{slug}"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return slug, []

        html = resp.text

        # Find all anchors; keep text and href
        anchors = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S)

        seen = set()
        normalized = []

        for href, inner in anchors:
            # Clean title text
            title = re.sub(r'<[^>]+>', '', inner or '').strip()
            if not title:
                continue

            # Heuristic: link or title should contain job-related keywords
            combined = f"{href} {title}".lower()
            if not re.search(r'job|career|opening|position|role|opportunity|vacancy|apply', combined):
                continue

            full_url = urljoin(url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            # Try to find a nearby location string in the HTML around this href.
            # For each candidate we extract, validate it with `is_valid_location`.
            loc = None
            idx = html.find(href)
            if idx != -1:
                start = max(0, idx - 100)
                snippet = html[start: idx + 2000]

                # 1) class containing "location"
                m = re.search(r'class=[\"\'][^\"\']*location[^\"\']*[\"\'][^>]*>([^<]+)<', snippet, re.I)
                if m:
                    cand = m.group(1).strip()
                    if is_valid_location(cand):
                        loc = cand

                # 2) data-location attribute
                if loc is None:
                    m2 = re.search(r'data-location=[\"\']([^\"\']+)[\"\']', snippet, re.I)
                    if m2:
                        cand = m2.group(1).strip()
                        if is_valid_location(cand):
                            loc = cand

                # 3) key/value pairs where the key has class 'key' or 'label' and contains 'location',
                #    and the value element has class 'value'
                if loc is None:
                    m_kv = re.search(
                        r'class=[\"\'][^\"\']*(?:key|label)[^\"\']*location[^\"\']*[\"\'][^>]*>[^<]*<[^>]*>.*?class=[\"\'][^\"\']*value[^\"\']*[\'\"][^>]*>([^<]+)<',
                        snippet,
                        re.I | re.S,
                    )
                    if m_kv:
                        cand = m_kv.group(1).strip()
                        if is_valid_location(cand):
                            loc = cand

                # 4) City, ST pattern fallback
                if loc is None:
                    m3 = re.search(r'([A-Za-z .\-]+,\s*[A-Z]{2})', snippet)
                    if m3:
                        cand = m3.group(1).strip()
                        if is_valid_location(cand):
                            loc = cand

            # Finalize location: if none of the parsing methods produced a valid location,
            # set to 'Not specified'. Do NOT skip the job just because an invalid candidate existed.
            if loc is None:
                loc = "Not specified"

            normalized.append(
                {
                    "company": urlparse(url).netloc,
                    "company_slug": slug,
                    "title": title,
                    "location": loc,
                    "url": full_url,
                    "updated_at": None,
                    "is_recruiter": is_recruiter_company(urlparse(url).netloc),
                    "ats": "Generic",
                    **get_job_metadata(),
                }
            )

        return slug, normalized

    except Exception:
        return slug, []

def fetch_all_jobs(companies, fetcher, platform="ATS"):
    """Fetch jobs from all companies in parallel."""
    print("=" * 80)
    print(f"FETCHING JOBS FROM {len(companies):,} COMPANIES FROM PLATFORM: {platform}")
    print("=" * 80 + "\n")

    all_jobs = []
    active_companies = {}
    failed = 0

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(fetcher, slug): slug for slug in companies}

        for i, future in enumerate(as_completed(futures), 1):
            slug, jobs = future.result()

            if jobs:
                all_jobs.extend(jobs)
                active_companies[slug] = len(jobs)
                print(f"  [{i}/{len(companies)}] {slug}: {len(jobs)} jobs")
            else:
                failed += 1
                if i % 50 == 0:
                    print(f"  [{i}/{len(companies)}] Checked... ({failed} inactive)")

    print(f"\nDETAILED STATS FOR {platform}:")
    print(f"  Companies checked: {len(companies)}")
    print(f"  Companies with jobs: {len(active_companies)}")
    print(f"  Failed/empty: {failed}")
    print(f"  Total jobs: {len(all_jobs)}")

    return active_companies, all_jobs


# ============================================================
# Helper Functions
# ============================================================

def is_recruiter_company(slug):
    slug = slug.lower()

    # Keyword-based detection
    if any(term in slug for term in RECRUITER_TERMS):
        return True

    return False

# Function to convert job date to absolute datatime
def parse_relative_date(date_string: str) -> Optional[str]:
    """
    Convert relative date strings to ISO 8601 format.
    
    Handles formats like "Posted Today", "Posted Yesterday", "Posted 2 days ago", etc.
    
    Args:
        date_string (str): The relative date string to parse
        
    Returns:
        Optional[str]: ISO 8601 formatted date string (e.g., "2026-01-23T16:58:55-05:00") 
                       or None if invalid
    
    Examples:
        >>> parse_relative_date("Posted Today")
        '2026-01-28T16:58:55-05:00'
        
        >>> parse_relative_date("Posted 2 days ago")
        '2026-01-26T16:58:55-05:00'
        
        >>> parse_relative_date("3 weeks ago")
        '2026-01-07T16:58:55-05:00'
    """
    if not date_string or not isinstance(date_string, str):
        return None
    
    # Normalize the string: trim and convert to lowercase
    normalized = date_string.strip().lower()
    
    # Current date/time
    now = datetime.now()
    target_date = now
    
    # Case-insensitive matching for different formats
    if 'today' in normalized or normalized == 'just now' or 'just posted' in normalized:
        # Today - keep current date
        target_date = now
    
    elif 'yesterday' in normalized:
        # Yesterday
        target_date = now - timedelta(days=1)
    
    elif re.search(r'(\d+)\s*(day|days)', normalized, re.IGNORECASE):
        # "X days ago" or "Posted X days ago"
        match = re.search(r'(\d+)\s*(day|days)', normalized, re.IGNORECASE)
        days = int(match.group(1))
        target_date = now - timedelta(days=days)
    
    elif re.search(r'(30)\+\s*(day|days)', normalized, re.IGNORECASE):
        # "30+ days ago" or "Posted 30+ days ago"
        match = re.search(r'(30)\+\s*(day|days)', normalized, re.IGNORECASE)
        days = int(match.group(1))
        target_date = now - timedelta(days=days+1)
    
    elif re.search(r'(\d+)\s*(week|weeks)', normalized, re.IGNORECASE):
        # "X weeks ago" or "Posted X weeks ago"
        match = re.search(r'(\d+)\s*(week|weeks)', normalized, re.IGNORECASE)
        weeks = int(match.group(1))
        target_date = now - timedelta(weeks=weeks)
    
    elif re.search(r'(\d+)\s*(month|months)', normalized, re.IGNORECASE):
        # "X months ago" or "Posted X months ago"
        match = re.search(r'(\d+)\s*(month|months)', normalized, re.IGNORECASE)
        months = int(match.group(1))
        # Approximate month as 30 days for simplicity
        # For more accurate calculation, you might want to use dateutil.relativedelta
        target_date = now - timedelta(days=months * 30)
    
    elif re.search(r'(\d+)\s*(year|years)', normalized, re.IGNORECASE):
        # "X years ago" or "Posted X years ago"
        match = re.search(r'(\d+)\s*(year|years)', normalized, re.IGNORECASE)
        years = int(match.group(1))
        target_date = now - timedelta(days=years * 365)
    
    elif re.search(r'(\d+)\s*(hour|hours)', normalized, re.IGNORECASE):
        # "X hours ago"
        match = re.search(r'(\d+)\s*(hour|hours)', normalized, re.IGNORECASE)
        hours = int(match.group(1))
        target_date = now - timedelta(hours=hours)
    
    elif re.search(r'(\d+)\s*(minute|minutes|min|mins)', normalized, re.IGNORECASE):
        # "X minutes ago"
        match = re.search(r'(\d+)\s*(minute|minutes|min|mins)', normalized, re.IGNORECASE)
        minutes = int(match.group(1))
        target_date = now - timedelta(minutes=minutes)
    
    else:
        # If we can't parse it as a relative date, try parsing it as a standard date
        try:
            target_date = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            # Unable to parse
            return None
    
    # Return in ISO 8601 format with timezone offset
    return target_date.astimezone().isoformat()

# Now for parsing Unix timestamps
def parse_unix_timestamp(timestamp: int) -> Optional[str]:
    """
    Convert Unix timestamp in milliseconds to ISO 8601 format.
    
    Args:
        timestamp (int): Unix timestamp in milliseconds since epoch (January 1, 1970, 00:00:00 UTC)
        
    Returns:
        Optional[str]: ISO 8601 formatted date string (e.g., "2025-01-09T06:10:18-05:00") 
                       or None if invalid
    
    Examples:
        >>> parse_unix_timestamp(1767999818020)
        '2025-01-09T06:10:18-05:00'
        
        >>> parse_unix_timestamp(1609459200000)
        '2021-01-01T00:00:00-05:00'
    """
    if not timestamp or not isinstance(timestamp, int):
        return None
    
    try:
        # Convert milliseconds to seconds
        dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        
        # Convert to local timezone
        dt_local = dt.astimezone()
        
        # Return in ISO 8601 format with timezone offset
        return dt_local.isoformat()
    
    except (ValueError, OSError, OverflowError):
        # Invalid timestamp
        return None

# Some functions for filtering to only include jobs in US, Canada, Denmark, Norway, Sweden
def get_us_location_patterns() -> Set[str]:
    """
    Get a comprehensive set of US location patterns including:
    - Country names (US, USA, United States)
    - All 50 state names (full and abbreviated)
    - Common variations
    - some additional patterns for Canada and other towns I've identified
    
    Returns:
        Set[str]: Set of lowercase location patterns for the US
    """
    us_patterns = {
        # Country identifiers
        'us', 'usa', 'u.s.', 'u.s.a.', 'united states', 'united states of america',
        
        # State abbreviations
        'al', 'ak', 'az', 'ar', 'ca', 'co', 'ct', 'de', 'fl', 'ga',
        'hi', 'id', 'il', 'in', 'ia', 'ks', 'ky', 'la', 'me', 'md',
        'ma', 'mi', 'mn', 'ms', 'mo', 'mt', 'ne', 'nv', 'nh', 'nj',
        'nm', 'ny', 'nc', 'nd', 'oh', 'ok', 'or', 'pa', 'ri', 'sc',
        'sd', 'tn', 'tx', 'ut', 'vt', 'va', 'wa', 'wv', 'wi', 'wy',
        
        # Full state names
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
        'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
        'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
        'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
        'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
        'new hampshire', 'new jersey', 'new mexico', 'new york',
        'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
        'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
        'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
        'west virginia', 'wisconsin', 'wyoming',
        
        # Territories
        'puerto rico', 'pr', 'guam', 'gu', 'virgin islands', 'vi',
        'american samoa', 'as', 'northern mariana islands', 'mp',
        
        # Common US city patterns (helps catch "Boston, MA" style entries)
        # We'll rely on state matching primarily, but include "remote" variants
        'remote, us', 'remote - us', 'remote (us)', 'remote usa', 'remote us',
        'us_remote', 'remote_us', 'united states remote', 'us remote', 'usa remote',
        
        # Now for variations with multiple locations or custom cities
        'locations', 'tarrytown', 'new york city', 'nyc', 'san francisco',
        'los angeles', 'chicago', 'boston', 'seattle', 'atlanta',
        'miami', 'dallas', 'houston', 'denver', 'washington dc', 'dc',
        'philadelphia', 'austin', 'portland', 'san diego', 'detroit',
        'minneapolis', 'st. paul',  'st paul', 'orlando', 'salt lake city',
        'raleigh', 'charlotte', 'pittsburgh', 'cincinnati', 'columbus',
        'indianapolis', 'nashville', 'richmond', 'sacramento', 'san jose',
        'baltimore', 'milwaukee', 'jacksonville', 'memphis', 'oklahoma city',
        'las vegas', 'albuquerque',
        
        # Also patterns for Canada
        'canada', 'ca', 'canadian', 'toronto', 'vancouver', 'montreal',
        'calgary', 'ottawa', 'edmonton', 'quebec', 'winnipeg', 'hamilton',
        'kitchener', 'london', 'halifax', 'waterloo', 'saskatchewan', 'nova scotia',
        'newfoundland', 'new brunswick', 'prince edward island', 'pei'
        
        # one last one for ones that don't specify location (just in case)
        'not specified'
    }
    
    return us_patterns


def get_nordic_location_patterns() -> Set[str]:
    """
    Get location patterns for Denmark, Norway, and Sweden.
    
    Returns:
        Set[str]: Set of lowercase location patterns for Nordic countries
    """
    nordic_patterns = {
        # Denmark
        'denmark', 'dk', 'danish', 'copenhagen', 'aarhus', 'odense', 'aalborg',
        
        # Norway
        'norway', 'no', 'norwegian', 'oslo', 'bergen', 'trondheim', 'stavanger',
        
        # Sweden
        'sweden', 'se', 'swedish', 'stockholm', 'gothenburg', 'göteborg',
        'malmö', 'malmo', 'uppsala', 'västerås', 'vasteras',
    }
    
    return nordic_patterns


def is_valid_location(location: str) -> bool:
    """
    Check if a location string matches US, Canada, Denmark, Norway, or Sweden.
    
    Args:
        location (str): Location string from job posting
        
    Returns:
        bool: True if location is in target countries, False otherwise
    """
    if not location or not isinstance(location, str):
        return False
    
    # Normalize the location string
    normalized = location.lower().strip()
    
    # Get all valid patterns
    us_patterns = get_us_location_patterns()
    nordic_patterns = get_nordic_location_patterns()
    all_patterns = us_patterns | nordic_patterns
    
    # Check for exact matches first (handles "DE", "US", etc.)
    # Split by common separators and check each part
    parts = re.split(r'[-,/\s]+', normalized)
    for part in parts:
        part = part.strip()
        if part in all_patterns:
            return True
    
    # Check if any pattern is contained in the location string
    # This handles formats like "Boston, MA" or "Remote - United States"
    for pattern in all_patterns:
        # Use word boundaries for short patterns to avoid false matches
        if len(pattern) <= 3:
            # For abbreviations, use word boundary matching
            pattern_regex = r'\b' + re.escape(pattern) + r'\b'
            if re.search(pattern_regex, normalized, re.IGNORECASE):
                return True
        else:
            # For longer patterns, simple substring matching is fine
            if pattern in normalized:
                return True
    
    return False

def clean_job_data(jobs):
    """Remove invalid/useless job entries."""
    cleaned = []
    skipped_reasons = {"no_title": 0, "no_url": 0, "no_company": 0}

    for job in jobs:
        title = (job.get("title") or "").strip().lower()
        url = job.get("url") or job.get("absolute_url")
        company = job.get("company") or job.get("company_slug")

        # Skip jobs with invalid titles
        if not title or title in ["not specified", "n/a", "unknown", ""]:
            skipped_reasons["no_title"] += 1
            continue

        # Skip jobs without URLs
        if not url:
            skipped_reasons["no_url"] += 1
            continue

        # Skip jobs without company info
        if not company:
            skipped_reasons["no_company"] += 1
            continue

        cleaned.append(job)

    # Print summary
    total_skipped = sum(skipped_reasons.values())
    if total_skipped > 0:
        print(f"\n  Skipped {total_skipped:,} invalid jobs:")
        for reason, count in skipped_reasons.items():
            if count > 0:
                print(f"    - {reason.replace('_', ' ').title()}: {count:,}")

    return cleaned


# ============================================================
# SAVE RESULTS
# ============================================================


def save_results(all_companies, active_companies, all_jobs):
    """Save all data to JSON files."""
    print("=" * 80)
    print("SAVING RESULTS")
    print("=" * 80 + "\n")

    original_count = len(all_jobs)
    all_jobs = clean_job_data(all_jobs)
    cleaned_count = original_count - len(all_jobs)
    print(f"Removed {cleaned_count:,} invalid jobs (blank/not specified titles)")

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Save all companies list
    companies_file = os.path.join(OUTPUT_DIR, "all_companies.json")
    with open(companies_file, "w") as f:
        json.dump(sorted(list(all_companies)), f, indent=2)
    print(f"All companies: {companies_file}")

    # Save active companies with job counts
    active_file = os.path.join(OUTPUT_DIR, "active_companies.json")
    with open(active_file, "w") as f:
        json.dump(active_companies, f, indent=2, sort_keys=True)
    print(f"Active companies: {active_file}")

    # Save all jobs
    all_jobs_file = os.path.join(OUTPUT_DIR, "all_jobs.json")
    with open(all_jobs_file, "w") as f:
        json.dump(all_jobs, f, indent=2)
    print(f"All jobs: {all_jobs_file} ({len(all_jobs):,} jobs)")

    # Save compressed version for GitHub Pages
    compressed_file = os.path.join(OUTPUT_DIR, "all_jobs.json.gz")
    with gzip.open(compressed_file, "wt", encoding="utf-8") as f:
        json.dump(all_jobs, f)

    # Check compression ratio
    original_size = os.path.getsize(all_jobs_file) / (1024 * 1024)
    compressed_size = os.path.getsize(compressed_file) / (1024 * 1024)
    print(
        f"Compressed: {compressed_file} ({compressed_size:.1f}MB, {compressed_size/original_size*100:.1f}% of original)"
    )

    recruiter_jobs = sum(1 for job in all_jobs if job.get("is_recruiter"))

    # Save metadata summary
    metadata = {
        "last_updated": timestamp,
        "total_companies": len(all_companies),
        "active_companies": len(active_companies),
        "total_jobs": len(all_jobs),
        "recruiter_jobs": recruiter_jobs,
        "source_type": SOURCE_TYPE,
        "platforms": "greenhouse_api, ashby_api, bamboohr_api, lever_api, workday_api, workdaysite_api",
    }

    metadata_file = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Metadata: {metadata_file}")

    print()


# ============================================================
# MAIN
# ============================================================


def main():
    print("\n" + "=" * 80)
    print("JOB BOARD AGGREGATOR")
    print("Scraping all jobs from ATS companies")
    print("=" * 80)

    # Load existing companies
    greenhouse_companies = load_companies(GREENHOUSE_FILE)
    ashby_companies = load_companies(ASHBY_FILE)
    bamboohr_companies = load_companies(BAMBOOHR_FILE)
    lever_companies = load_companies(LEVER_FILE)
    workday_companies = load_companies(WORKDAY_FILE)
    workdaysite_companies = load_companies(WORKDAYSITE_FILE)
    
    if (
        not greenhouse_companies
        and not ashby_companies
        and not bamboohr_companies
        and not lever_companies
        and not workday_companies
        and not workdaysite_companies
    ):
        print("Exiting - no companies loaded!")
        return

    # Fetch from all sources
    active_greenhouse, jobs_greenhouse = fetch_all_jobs(greenhouse_companies, fetch_company_jobs_greenhouse, "GREENHOUSE")

    active_workday, jobs_workday = fetch_all_jobs(workday_companies, fetch_company_jobs_workday, "WORKDAY")

    active_workdaysite, jobs_workdaysite = fetch_all_jobs(workdaysite_companies, fetch_company_jobs_workdaysite, "WORKDAYSITE")

    active_bamboohy, jobs_bamboohr = fetch_all_jobs(bamboohr_companies, fetch_company_jobs_bamboohr, "BAMBOOHR")

    active_lever, jobs_lever = fetch_all_jobs(lever_companies, fetch_company_jobs_lever, "LEVER")

    active_ashby, jobs_ashby = fetch_all_jobs(ashby_companies, fetch_company_jobs_ashby, "ASHBY")

    # Combine results
    all_companies = (
        greenhouse_companies
        | workday_companies
        | workdaysite_companies
        | bamboohr_companies
        | lever_companies
        | ashby_companies
    )
    all_active_companies = {
        **active_greenhouse,
        **active_workday,
        **active_workdaysite,
        **active_bamboohy,
        **active_lever,
        **active_ashby
    }
    all_jobs_OG = jobs_greenhouse + jobs_ashby + jobs_bamboohr + jobs_lever + jobs_workday + jobs_workdaysite
    all_jobs = []
    
    # Filter all_jobs to include only jobs from the last 30 days
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=filter_days)
    for job in all_jobs_OG:
        if job['updated_at'] is None:
            all_jobs.append(job)
        elif datetime.fromisoformat(job['updated_at'].replace("Z", "+00:00")) >= cutoff_date:
            all_jobs.append(job)
        else:
            pass
    
    # Save results
    save_results(all_companies, all_active_companies, all_jobs)

    # Final summary
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"Total companies:   {len(all_companies):,}")
    print(f"Active companies:  {len(all_active_companies):,}")
    print(f"Total jobs:        {len(all_jobs):,}")
    print(f"\nAll data saved to '{OUTPUT_DIR}/' directory")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Job Board Aggregator Scraper')
    parser.add_argument('--source', 
                       choices=['automated', 'manual'], 
                       default='automated',
                       help='Source type: automated (GitHub Actions) or manual (local run)')
    parser.add_argument('--within', 
                       default=30,
                       help='What should the date filter be; default=30. (within "30" days)')

    args = parser.parse_args()
    SOURCE_TYPE = args.source
    filter_days = int(args.within)
    
    print(f"\nRunning in {SOURCE_TYPE.upper()} mode\n")
    
    main()