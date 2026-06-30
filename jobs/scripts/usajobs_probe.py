"""Throwaway USAJobs API probe. Do NOT commit. Phase-1 recon verification only."""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

api_key = os.environ.get("USAJOBS_API_KEY")
user_agent = os.environ.get("USAJOBS_USER_AGENT")
if not api_key or not user_agent:
    print("Missing USAJOBS_API_KEY or USAJOBS_USER_AGENT in .env", file=sys.stderr)
    sys.exit(1)

url = "https://data.usajobs.gov/api/search"
headers = {
    "Host": "data.usajobs.gov",
    "User-Agent": user_agent,
    "Authorization-Key": api_key,
}
params = {
    "JobCategoryCode": "2210",
    "LocationName": "Sacramento, California",
    "Radius": "50",
    "WhoMayApply": "Public",
    "ResultsPerPage": "5",
    "SortField": "OpenDate",
    "SortDirection": "Desc",
}

resp = requests.get(url, headers=headers, params=params, timeout=30)
print(f"HTTP {resp.status_code}")

if resp.status_code != 200:
    print("Body:", resp.text[:1000])
    sys.exit(2)

data = resp.json()
fixture_path = REPO_ROOT / "tests" / "fixtures" / "usajobs_sample.json"
fixture_path.parent.mkdir(parents=True, exist_ok=True)
fixture_path.write_text(json.dumps(data, indent=2, sort_keys=False))
print(f"Saved: {fixture_path.relative_to(REPO_ROOT)}")

count_all = data.get("SearchResult", {}).get("SearchResultCountAll")
print(f"SearchResultCountAll: {count_all}")
