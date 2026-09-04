"""fetch_competitor_ratings.py — Pulls public rating benchmarks for tracked
competitors via the Places API "Place Details" endpoint. No OAuth needed,
just an API key — but note the real limitation below before treating this
as equivalent to the owned-listing data.

LIMITATION: Place Details returns the overall rating + total review count
for a listing, plus a small CAPPED SAMPLE of individual reviews (not the
full history, and Google doesn't document exactly how that sample is
chosen). This is fine for "how does Remedy's rating compare to Belo's
rating" but not for building a full competitor review feed the way we can
for Remedy's own listings.

Output: competitor_ratings.json ->
  {"fetchedAt": <ISO-8601 UTC>, "competitors": [...]}
one row per competitor, each carrying a `status`:
  "ok"        - Place Details returned data for this competitor.
  "not_found" - the API responded but with a non-"OK" status for this
                place_id (e.g. a bad/stale place_id).
  "error"     - the request failed even after retries (see
                http_utils.get_with_retry) — rating/counts are null,
                distinct from a competitor that genuinely has none.
Competitors still marked with a REPLACE_ME placeholder place_id in
config.py are skipped entirely (not written as a row) since there is no
ID to query yet.

Usage:
    python fetch_competitor_ratings.py
"""

import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from config import COMPETITOR_PLACE_IDS
from http_utils import RetryExhaustedError, get_with_retry

load_dotenv()

API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

FIELDS = "name,rating,user_ratings_total,reviews"


def fetch_place_details(place_id):
    params = {
        "place_id": place_id,
        "fields": FIELDS,
        "key": API_KEY,
    }
    resp = get_with_retry(PLACE_DETAILS_URL, params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        print(f"  -> API returned status={data.get('status')} for {place_id}")
        return None
    return data.get("result")


def normalize(competitor_name, result, status="ok"):
    if not result:
        return {
            "competitor": competitor_name,
            "rating": None,
            "userRatingsTotal": 0,
            "sampleReviewCount": 0,
            "sampleReviews": [],
            "status": status,
        }
    reviews = result.get("reviews", [])
    return {
        "competitor": competitor_name,
        "rating": result.get("rating"),
        "userRatingsTotal": result.get("user_ratings_total", 0),
        "sampleReviewCount": len(reviews),
        # Sample reviews only, per the limitation noted above — not a full feed.
        "sampleReviews": [
            {
                "author": r.get("author_name"),
                "rating": r.get("rating"),
                "text": r.get("text", "")[:280],
                "date": r.get("relative_time_description"),
            }
            for r in reviews
        ],
        "status": status,
    }


def main():
    if not API_KEY:
        raise SystemExit(
            "GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env "
            "and fill it in."
        )

    unresolved = [
        name for name, pid in COMPETITOR_PLACE_IDS.items()
        if pid.startswith("REPLACE_ME")
    ]
    if unresolved:
        print(
            "Note: these competitors still have placeholder place_ids in "
            f"config.py and will be skipped: {', '.join(unresolved)}"
        )

    results = []
    for name, place_id in COMPETITOR_PLACE_IDS.items():
        if place_id.startswith("REPLACE_ME"):
            continue
        print(f"Fetching {name}...")
        try:
            details = fetch_place_details(place_id)
        except RetryExhaustedError as exc:
            print(f"ERROR: request failed (retries exhausted) fetching {name}: {exc}")
            results.append(normalize(name, None, status="error"))
            continue
        status = "ok" if details else "not_found"
        results.append(normalize(name, details, status=status))
        time.sleep(0.2)

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open("competitor_ratings.json", "w") as f:
        json.dump({"fetchedAt": fetched_at, "competitors": results}, f, indent=2)

    print(f"\nWrote {len(results)} competitors to competitor_ratings.json")


if __name__ == "__main__":
    main()
