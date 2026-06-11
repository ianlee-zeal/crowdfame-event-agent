"""
main.py
Orchestrates the full Crowdfame event pipeline:
  1. Scrape US events from Facebook via Apify
  2. AI background check (legitimacy validation)
  3. POST approved events to Crowdfame API
  4. Save run report
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from scraper import run_scraper
from validator import run_validation
from poster import run_poster

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


def save_report(report: dict):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"run_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n📄 Full report saved to {path}")
    return path


def main():
    run_start = datetime.now(timezone.utc)
    print("=" * 60)
    print(f"🚀 Crowdfame Event Agent — {run_start.isoformat()}")
    print("=" * 60)

    report = {
        "run_start": run_start.isoformat(),
        "stages": {}
    }

    # ── Stage 1: Scrape ──────────────────────────────────────────
    print("\n📡 STAGE 1: Scraping Facebook events via Apify...")
    try:
        raw_events = run_scraper()
        report["stages"]["scrape"] = {
            "status": "ok",
            "raw_count": len(raw_events)
        }
    except Exception as e:
        print(f"❌ Scrape failed: {e}")
        report["stages"]["scrape"] = {"status": "error", "error": str(e)}
        save_report(report)
        sys.exit(1)

    # ── Stage 2: Validate ─────────────────────────────────────────
    print("\n🔍 STAGE 2: Running AI background checks...")
    try:
        validation_results, approved_events = run_validation(raw_events)
        report["stages"]["validate"] = {
            "status": "ok",
            "total_checked": len(validation_results),
            "approved": len(approved_events),
            "rejected": len(validation_results) - len(approved_events),
        }
        # Save to logs for artifact upload
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        approved_path = LOGS_DIR / f"approved_events_{timestamp}.json"
        with open(approved_path, "w") as f:
            json.dump(approved_events, f, indent=2, default=str)
        
        results_path = LOGS_DIR / f"validation_results_{timestamp}.json"
        with open(results_path, "w") as f:
            json.dump(validation_results, f, indent=2, default=str)
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        report["stages"]["validate"] = {"status": "error", "error": str(e)}
        save_report(report)
        sys.exit(1)

    if not approved_events:
        print("⚠️  No events passed validation. Nothing to post.")
        report["stages"]["post"] = {"status": "skipped", "reason": "no_approved_events"}
        save_report(report)
        return

    # ── Stage 3: Post ─────────────────────────────────────────────
    print("\n📤 STAGE 3: Posting approved events to Crowdfame API...")
    try:
        post_report = run_poster(approved_events)
        report["stages"]["post"] = {
            "status": "ok",
            **{k: v for k, v in post_report.items() if k != "details"},
        }
    except Exception as e:
        print(f"❌ Posting failed: {e}")
        report["stages"]["post"] = {"status": "error", "error": str(e)}
        save_report(report)
        sys.exit(1)

    # ── Final summary ─────────────────────────────────────────────
    run_end = datetime.now(timezone.utc)
    duration = (run_end - run_start).total_seconds()
    report["run_end"] = run_end.isoformat()
    report["duration_seconds"] = duration

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE")
    print(f"   Scraped:   {report['stages']['scrape']['raw_count']} events")
    print(f"   Approved:  {report['stages']['validate']['approved']} events")
    print(f"   Rejected:  {report['stages']['validate']['rejected']} events")
    print(f"   Created:   {report['stages']['post'].get('created', 0)} in Crowdfame")
    print(f"   Duration:  {duration:.1f}s")
    print("=" * 60)

    save_report(report)


if __name__ == "__main__":
    main()
