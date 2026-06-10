"""
scraper.py
Fetches raw Facebook event data from Apify and triggers the Apify actor run.
"""

import httpx
import json
import os
import time
from datetime import datetime

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
APIFY_ACTOR_ID = "UZBnerCFBo5FgGouO"

# DFW search queries
DFW_SEARCHES = [
    "events Dallas TX",
    "events Fort Worth TX",
    "events Arlington TX",
]


def trigger_apify_run() -> str:
    """Trigger the Apify Facebook Events Scraper actor and return dataset ID."""
    print(f"[{datetime.now().isoformat()}] Triggering Apify actor run...")

    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/runs"
    payload = {
        "search": DFW_SEARCHES,
        "maxEvents": 50,
    }

    resp = httpx.post(
        url,
        json=payload,
        params={"token": APIFY_TOKEN},
        timeout=30
    )
    resp.raise_for_status()
    run_data = resp.json()["data"]
    run_id = run_data["id"]
    print(f"  Actor run started: {run_id}")
    return run_id


def wait_for_run(run_id: str, poll_interval: int = 15, max_wait: int = 600) -> str:
    """Poll until the Apify run finishes, return the dataset ID."""
    url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    elapsed = 0

    while elapsed < max_wait:
        resp = httpx.get(url, params={"token": APIFY_TOKEN}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data["status"]

        print(f"  Run status: {status} ({elapsed}s elapsed)")

        if status == "SUCCEEDED":
            return data["defaultDatasetId"]
        elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Apify run {run_id} did not finish within {max_wait}s")


def fetch_events(dataset_id: str) -> list[dict]:
    """Download all items from an Apify dataset."""
    print(f"  Fetching events from dataset: {dataset_id}")
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    resp = httpx.get(
        url,
        params={"token": APIFY_TOKEN, "format": "json", "limit": 10000},
        timeout=60
    )
    resp.raise_for_status()
    events = resp.json()
    print(f"  Retrieved {len(events)} raw events")
    return events


def run_scraper() -> list[dict]:
    """Full pipeline: trigger run → wait → fetch results."""
    run_id = trigger_apify_run()
    dataset_id = wait_for_run(run_id)
    return fetch_events(dataset_id)


if __name__ == "__main__":
    events = run_scraper()
    with open("/tmp/raw_events.json", "w") as f:
        json.dump(events, f, indent=2)
    print(f"Saved {len(events)} raw events to /tmp/raw_events.json")
