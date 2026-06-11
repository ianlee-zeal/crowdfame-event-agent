import anthropic
import json
import re
from datetime import datetime, timezone
from typing import TypedDict

client = anthropic.Anthropic()

class ValidationResult(TypedDict):
    event_id: str
    legitimate: bool
    confidence: float
    flags: list[str]
    normalized: dict | None
    reasoning: str

SYSTEM_PROMPT = """You are a Crowdfame event validator. Evaluate Facebook events.

REJECT if: spam, MLM, past event, vague location, <20 word description, private
APPROVE if: real US public event, clear title/location/date

ALWAYS respond with ONLY this JSON format (no markdown, no text outside JSON):
[
  {
    "event_id": "id_string",
    "legitimate": true/false,
    "confidence": 0.0-1.0,
    "flags": ["flag1", "flag2"],
    "reasoning": "one sentence",
    "normalized": {
      "title": "...",
      "date": "YYYY-MM-DD",
      "startTime": "YYYY-MM-DDTHH:mm",
      "endTime": null,
      "timezone": "America/Chicago",
      "location": "...",
      "city": "...",
      "state": "TX",
      "description": "...",
      "sourceUrl": "https://facebook.com/events/...",
      "posterImageUrl": null,
      "creatorName": null,
      "instagramHandle": null,
      "country": "US"
    } or null
  }
]
"""

def chunk_events(events: list[dict], chunk_size: int = 15) -> list[list[dict]]:
    return [events[i:i + chunk_size] for i in range(0, len(events), chunk_size)]

def assign_ids(events: list[dict]) -> list[dict]:
    for i, e in enumerate(events):
        if "id" not in e or not e["id"]:
            e["id"] = e.get("eventId") or f"event_{i}"
    return events

def validate_chunk(chunk: list[dict]) -> list[ValidationResult]:
    slim = []
    for e in chunk:
        slim.append({
            "id": e.get("id"),
            "title": e.get("name") or e.get("title") or "",
            "description": (e.get("description") or "")[:400],
            "startTimestamp": e.get("startTimestamp"),
            "location": e.get("location") or e.get("place") or "",
            "url": e.get("url"),
            "organizer": (e.get("hosts") or e.get("organizer") or "")[:100],
        })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Validate these events. Extract dates from description if timestamp missing. Return ONLY JSON array:\n{json.dumps(slim, default=str)}"
        }]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        results = json.loads(raw)
        for i, r in enumerate(results):
            if r.get("legitimate") and r.get("normalized"):
                r["normalized"]["posterImageUrl"] = chunk[i].get("imageUrl")
        return results
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"    Parse error: {e}")
        return [{
            "event_id": ev.get("id", "unknown"),
            "legitimate": False,
            "confidence": 0.0,
            "flags": ["parse_error"],
            "reasoning": "Validation error",
            "normalized": None,
        } for ev in chunk]

def deduplicate(results: list[ValidationResult]) -> list[ValidationResult]:
    seen_urls = set()
    deduped = []
    for r in results:
        if not r.get("legitimate") or not r.get("normalized"):
            deduped.append(r)
            continue
        url = r["normalized"].get("sourceUrl", "")
        if url in seen_urls:
            r["legitimate"] = False
            r["normalized"] = None
        else:
            seen_urls.add(url)
        deduped.append(r)
    return deduped

def run_validation(events: list[dict]) -> tuple[list[ValidationResult], list[dict]]:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Validating {len(events)} events...")
    events = assign_ids(events)
    chunks = chunk_events(events, chunk_size=15)
    all_results: list[ValidationResult] = []
    
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}/{len(chunks)}...")
        results = validate_chunk(chunk)
        all_results.extend(results)
    
    all_results = deduplicate(all_results)
    approved = [r["normalized"] for r in all_results if r.get("legitimate") and r.get("normalized")]
    rejected = len(all_results) - len(approved)
    
    print(f"  ✅ Approved: {len(approved)}")
    print(f"  ❌ Rejected: {rejected}")
    
    return all_results, approved
