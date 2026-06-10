"""
validator.py
AI-powered background check agent. Uses Claude to evaluate each event
for legitimacy, spam, scams, duplicate content, and policy compliance
before it gets pushed to the Crowdfame API.
"""

import anthropic
import json
import re
import httpx
from datetime import datetime, timezone
from typing import TypedDict

client = anthropic.Anthropic()


class ValidationResult(TypedDict):
    event_id: str
    legitimate: bool
    confidence: float          # 0.0 – 1.0
    flags: list[str]           # e.g. ["spam", "vague_location", "past_event"]
    normalized: dict | None    # cleaned event if legitimate, else None
    reasoning: str


SYSTEM_PROMPT = """You are a strict event legitimacy agent for Crowdfame, a US event discovery app.
Your job is to evaluate Facebook events and decide if they should be published.

REJECT the event if ANY of these are true:
- It is spam, MLM, pyramid scheme, or "get rich quick"
- It promotes illegal activity (drug sales, weapons, fraud, etc.)
- It is adult/explicit content
- The event date is in the past
- The location is vague, fake, or non-US
- The title or description is gibberish, auto-generated, or placeholder text
- It is a duplicate or near-duplicate of another event in the batch
- It has no meaningful description (under 20 real words)
- The organizer appears to be a bot or fake account
- It is a private or invite-only event with no public value

APPROVE the event if:
- It is a real, verifiable public event in the US
- It has a clear title, date, time, and location
- The organizer appears to be a legitimate person, brand, or venue
- The description provides genuine value to potential attendees

Respond ONLY with a JSON array, one object per event, with this exact schema:
[
  {
    "event_id": "<id from input>",
    "legitimate": true | false,
    "confidence": 0.0-1.0,
    "flags": ["flag1", "flag2"],
    "reasoning": "One sentence explanation",
    "normalized": {
      "title": "...",
      "description": "...",
      "start_datetime": "ISO8601",
      "end_datetime": "ISO8601 or null",
      "location_name": "...",
      "location_address": "...",
      "city": "...",
      "state": "...",
      "url": "https://facebook.com/events/...",
      "organizer_name": "...",
      "category": "music|sports|food|arts|community|business|tech|health|other",
      "image_url": "... or null"
    }
  }
]

If legitimate is false, set normalized to null.
Never include markdown, code fences, or any text outside the JSON array.
"""


def chunk_events(events: list[dict], chunk_size: int = 20) -> list[list[dict]]:
    """Split events into chunks to stay within context limits."""
    return [events[i:i + chunk_size] for i in range(0, len(events), chunk_size)]


def assign_ids(events: list[dict]) -> list[dict]:
    """Ensure every event has a stable ID for tracking."""
    for i, e in enumerate(events):
        if "id" not in e or not e["id"]:
            e["id"] = e.get("eventId") or e.get("url", f"event_{i}").split("/")[-1] or f"event_{i}"
    return events


def validate_chunk(chunk: list[dict]) -> list[ValidationResult]:
    """Send one chunk to Claude for background-check validation."""
    # Strip heavy/irrelevant fields before sending to save tokens
    slim = []
    for e in chunk:
        slim.append({
            "id": e.get("id"),
            "title": e.get("name") or e.get("title"),
            "description": (e.get("description") or "")[:800],
            "start": e.get("startTimestamp") or e.get("start_time"),
            "end": e.get("endTimestamp") or e.get("end_time"),
            "location": e.get("location") or e.get("place"),
            "url": e.get("url"),
            "organizer": e.get("hosts") or e.get("organizer"),
            "attendingCount": e.get("attendingCount"),
            "image": e.get("photo") or e.get("coverPhoto"),
        })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Validate these {len(slim)} events:\n{json.dumps(slim, default=str)}"
        }]
    )

    raw = response.content[0].text.strip()
    # Strip any accidental markdown fences
    raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error in chunk: {e}")
        # Return all as rejected rather than crash
        return [
            {
                "event_id": ev.get("id", "unknown"),
                "legitimate": False,
                "confidence": 0.0,
                "flags": ["parse_error"],
                "reasoning": "Validation failed due to a parsing error.",
                "normalized": None,
            }
            for ev in chunk
        ]


def deduplicate(results: list[ValidationResult]) -> list[ValidationResult]:
    """Remove near-duplicate approved events by URL and title similarity."""
    seen_urls = set()
    seen_titles = set()
    deduped = []

    for r in results:
        if not r["legitimate"] or not r["normalized"]:
            deduped.append(r)
            continue

        url = r["normalized"].get("url", "")
        title = r["normalized"].get("title", "").lower().strip()

        if url in seen_urls:
            r["legitimate"] = False
            r["flags"].append("duplicate_url")
            r["normalized"] = None
        elif title in seen_titles:
            r["legitimate"] = False
            r["flags"].append("duplicate_title")
            r["normalized"] = None
        else:
            seen_urls.add(url)
            seen_titles.add(title)

        deduped.append(r)

    return deduped


def run_validation(events: list[dict]) -> tuple[list[ValidationResult], list[dict]]:
    """
    Full validation pipeline.
    Returns (all_results, approved_events_normalized).
    """
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Starting background checks on {len(events)} events...")

    events = assign_ids(events)
    chunks = chunk_events(events, chunk_size=20)
    all_results: list[ValidationResult] = []

    for i, chunk in enumerate(chunks):
        print(f"  Validating chunk {i+1}/{len(chunks)} ({len(chunk)} events)...")
        results = validate_chunk(chunk)
        all_results.extend(results)

    all_results = deduplicate(all_results)

    approved = [r["normalized"] for r in all_results if r["legitimate"] and r["normalized"]]
    rejected = [r for r in all_results if not r["legitimate"]]

    print(f"\n  ✅ Approved: {len(approved)}")
    print(f"  ❌ Rejected: {len(rejected)}")

    # Print rejection summary
    flag_counts: dict[str, int] = {}
    for r in rejected:
        for flag in r.get("flags", []):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    if flag_counts:
        print("  Rejection reasons:", json.dumps(flag_counts, indent=4))

    return all_results, approved


if __name__ == "__main__":
    with open("/tmp/raw_events.json") as f:
        events = json.load(f)

    results, approved = run_validation(events)

    with open("/tmp/validation_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    with open("/tmp/approved_events.json", "w") as f:
        json.dump(approved, f, indent=2, default=str)

    print(f"\nSaved results to /tmp/validation_results.json and /tmp/approved_events.json")
