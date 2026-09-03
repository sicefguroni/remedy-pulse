"""fetch_owned_reviews.py — Pulls reviews for Remedy's own Google Business
Profile listings and writes two normalized JSON files:

  reviews_aggregate.json  -> {"fetchedAt": <ISO-8601 UTC>, "listings": [...]}
                              one row per branch. This is the INPUT for the
                              Reviews tab table, not a byte-for-byte match
                              of it — see "What this does and doesn't
                              cover" below before wiring a consumer to it.
  reviews_raw.json        -> {"fetchedAt": <ISO-8601 UTC>, "reviews": [...]}
                              one row per individual review, matching the
                              shape the Mentions tab feed items would need
                              once the mockup is refactored to render from
                              JSON (see the "Piece 2" data-driven refactor
                              discussed separately — this script doesn't do
                              that refactor itself, it just produces the
                              input for it).

What each `reviews_aggregate.json` row does and doesn't cover
---------------------------------------------------------------
- `status` is one of:
    "ok"            - fetched successfully, reviewCount may be > 0.
    "no_reviews"    - fetched successfully, this branch genuinely has zero
                       reviews right now.
    "access_denied" - the reviews endpoint returned 403 for this listing
                       (see ReviewsAccessDenied below). rating,
                       reviewCount, responseRate, and pendingReplies are
                       all `null` — this must never be mistaken for
                       {"reviewCount": 0, "status": "ok"}.
    "error"         - the request failed even after retries (see
                       http_utils.get_with_retry). Same null fields as
                       access_denied.
  A consumer should map "access_denied" and "error" to a distinct
  "sync failed" UI state, never to "All clear" — only "ok" (with
  pendingReplies == 0) means "All clear", and only "ok"/"no_reviews" rows
  are safe to render as real review data.
- `responseRate` is emitted as a FRACTION (e.g. 0.88), not a percentage —
  the consumer must multiply by 100 (and format/round) before display,
  the way the mockup's Reviews tab table renders "88%". It is `null`
  whenever status is "access_denied" or "error".
- There is intentionally NO "Trend (30d)" field, even though the mockup's
  Reviews tab table has a Trend (30d) column. A real 30-day trend needs a
  history of past snapshots to diff against; this script only ever sees
  the current live state of each listing and keeps no history. Faking a
  trend from a single snapshot would be worse than omitting it — a
  scheduled job that stores periodic snapshots is a separate, later piece
  of work, not something bolted onto this script.
So: this script's output is the input for the Reviews tab table, not a
drop-in match of it. A consumer still needs to format `responseRate`, map
`status` to the UI tag, and source Trend (30d) from elsewhere (or omit it)
before rendering.

IMPORTANT — read before running:
The review-reading endpoint used below (mybusiness.googleapis.com/v4,
accounts.locations.reviews.list) is NOT automatically available on a new
Google Cloud project. Google gates Business Profile API access behind a
request form:
  https://developers.google.com/my-business/content/prereqs
Budget lead time for that approval before this script will return real
data. If access is denied or delayed, the practical fallback is a licensed
reviews aggregator/vendor rather than waiting indefinitely — worth a
decision from Paul/Marketing if this drags on (same category of decision
as the other §16 items).

Usage:
    python oauth_setup.py         # once, to authorize
    python fetch_owned_reviews.py # run any time after that
"""

import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from config import OWNED_LISTINGS
from http_utils import RetryExhaustedError, get_with_retry

load_dotenv()

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "./token.json")
SCOPES = ["https://www.googleapis.com/auth/business.manage"]

ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4/{account}/{location}/reviews"


class ReviewsAccessDenied(Exception):
    """Raised when the reviews endpoint returns 403 for a given listing.

    A 403 almost always means the Business Profile API access request
    (see the module docstring) hasn't been granted yet for this project.
    This is deliberately NOT swallowed into an empty review list — an
    access-denied branch must stay distinguishable on disk from a branch
    that genuinely has zero reviews.
    """


def load_credentials():
    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(
            f"No token file at '{TOKEN_FILE}'. Run oauth_setup.py first."
        )
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def get_accounts(creds):
    resp = get_with_retry(
        ACCOUNTS_URL, headers={"Authorization": f"Bearer {creds.token}"}
    )
    resp.raise_for_status()
    return resp.json().get("accounts", [])


def get_locations(creds, account_name):
    resp = get_with_retry(
        LOCATIONS_URL.format(account=account_name),
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"readMask": "name,title,storefrontAddress"},
    )
    resp.raise_for_status()
    return resp.json().get("locations", [])


def get_reviews(creds, account_name, location_name):
    reviews = []
    page_token = None
    while True:
        params = {"pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = get_with_retry(
            REVIEWS_URL.format(account=account_name, location=location_name),
            headers={"Authorization": f"Bearer {creds.token}"},
            params=params,
        )
        if resp.status_code == 403:
            raise ReviewsAccessDenied(
                f"403 from the reviews endpoint for {location_name}. This "
                "almost always means the project doesn't have Business "
                "Profile API access granted yet — see the note at the top "
                "of this file."
            )
        resp.raise_for_status()
        data = resp.json()
        reviews.extend(data.get("reviews", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return reviews


def star_rating_to_int(star_rating_enum):
    # Google's API returns ratings as an enum string, not a number.
    mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
    return mapping.get(star_rating_enum, None)


def mask_reviewer_name(display_name):
    # Per spec §11 (PH Data Privacy Act) — minimize storage of personal
    # identifiers. Keep first name / initial only rather than full name.
    if not display_name:
        return "Google patron"
    parts = display_name.strip().split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def normalize_reviews(listing_name, raw_reviews):
    normalized = []
    for r in raw_reviews:
        rating = star_rating_to_int(r.get("starRating"))
        has_reply = "reviewReply" in r
        if rating and rating >= 4:
            sentiment = "Positive"
        elif rating and rating <= 2:
            sentiment = "Negative"
        else:
            sentiment = "Neutral"
        normalized.append({
            "platform": "Google",
            "listing": listing_name,
            "author": mask_reviewer_name(r.get("reviewer", {}).get("displayName")),
            "rating": rating,
            "text": r.get("comment", ""),
            "date": r.get("createTime", "")[:10] or None,
            "hasReply": has_reply,
            "sentiment": sentiment,
            # Places/Business Profile APIs don't expose a public deep link to
            # an individual review — the mockup's "View source" links fall
            # back to a search query for this same reason.
            "sourceUrl": None,
        })
    return normalized


def build_aggregate(listing_name, normalized_reviews, status="ok"):
    # status may be pre-set to "access_denied" or "error" by the caller
    # when the fetch itself failed — in that case normalized_reviews is
    # always [] and every numeric field below is intentionally null so a
    # denied/failed branch can never look like a genuine zero-review one.
    if status in ("access_denied", "error"):
        return {
            "listing": listing_name,
            "rating": None,
            "reviewCount": None,
            "responseRate": None,
            "pendingReplies": None,
            "status": status,
        }

    total = len(normalized_reviews)
    if total == 0:
        return {
            "listing": listing_name,
            "rating": None,
            "reviewCount": 0,
            "responseRate": None,
            "pendingReplies": 0,
            "status": "no_reviews",
        }
    rated = [r["rating"] for r in normalized_reviews if r["rating"]]
    avg_rating = round(sum(rated) / len(rated), 1) if rated else None
    replied = sum(1 for r in normalized_reviews if r["hasReply"])
    pending = total - replied
    return {
        "listing": listing_name,
        "rating": avg_rating,
        "reviewCount": total,
        "responseRate": round(replied / total, 2),
        "pendingReplies": pending,
        "status": "ok",
    }


def main():
    creds = load_credentials()
    accounts = get_accounts(creds)
    if not accounts:
        raise SystemExit("No Business Profile accounts found for this login.")

    # Build a lookup of location_id -> listing name from config.py
    configured_ids = {
        v["location_id"]: k for k, v in OWNED_LISTINGS.items()
        if not v["location_id"].startswith("REPLACE_ME")
    }

    all_aggregate = []
    all_raw = []
    unmatched_locations = []

    for account in accounts:
        account_name = account["name"]  # e.g. "accounts/123456789"
        locations = get_locations(creds, account_name)

        for loc in locations:
            loc_name = loc["name"]  # e.g. "locations/987654321"
            loc_id = loc_name.split("/")[-1]
            title = loc.get("title", "Unknown listing")

            if loc_id not in configured_ids:
                unmatched_locations.append({"id": loc_id, "title": title})
                continue

            listing_name = configured_ids[loc_id]
            print(f"Fetching reviews for {listing_name} ({loc_id})...")

            try:
                raw_reviews = get_reviews(creds, account_name, loc_name)
            except ReviewsAccessDenied as exc:
                print(f"ERROR: access denied fetching reviews for {listing_name}: {exc}")
                all_aggregate.append(build_aggregate(listing_name, [], status="access_denied"))
                continue
            except RetryExhaustedError as exc:
                print(f"ERROR: request failed (retries exhausted) fetching reviews for {listing_name}: {exc}")
                all_aggregate.append(build_aggregate(listing_name, [], status="error"))
                continue

            normalized = normalize_reviews(listing_name, raw_reviews)
            all_raw.extend(normalized)
            all_aggregate.append(build_aggregate(listing_name, normalized))

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open("reviews_aggregate.json", "w") as f:
        json.dump({"fetchedAt": fetched_at, "listings": all_aggregate}, f, indent=2)
    with open("reviews_raw.json", "w") as f:
        json.dump({"fetchedAt": fetched_at, "reviews": all_raw}, f, indent=2)

    print(f"\nWrote {len(all_aggregate)} listings to reviews_aggregate.json")
    print(f"Wrote {len(all_raw)} individual reviews to reviews_raw.json")

    if unmatched_locations:
        print(
            "\nFound locations not yet mapped in config.py — paste their IDs "
            "into OWNED_LISTINGS if any of these are Remedy branches:"
        )
        for loc in unmatched_locations:
            print(f"  - {loc['title']}: location_id = {loc['id']}")


if __name__ == "__main__":
    main()
