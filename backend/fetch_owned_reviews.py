"""
fetch_owned_reviews.py — Pulls reviews for Remedy's own Google Business
Profile listings and writes two normalized JSON files shaped to match
remedy-pulse-mockup.html directly:

  reviews_aggregate.json  -> one row per branch, matches the Reviews tab table
  reviews_raw.json        -> one row per individual review, matches the shape
                             the Mentions tab feed items would need once the
                             mockup is refactored to render from JSON (see
                             the "Piece 2" data-driven refactor discussed
                             separately — this script doesn't do that
                             refactor itself, it just produces the input
                             for it).

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

import os
import json
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from config import OWNED_LISTINGS

load_dotenv()

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "./token.json")
SCOPES = ["https://www.googleapis.com/auth/business.manage"]

ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4/{account}/{location}/reviews"


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
    resp = requests.get(
        ACCOUNTS_URL, headers={"Authorization": f"Bearer {creds.token}"}
    )
    resp.raise_for_status()
    return resp.json().get("accounts", [])


def get_locations(creds, account_name):
    resp = requests.get(
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
        resp = requests.get(
            REVIEWS_URL.format(account=account_name, location=location_name),
            headers={"Authorization": f"Bearer {creds.token}"},
            params=params,
        )
        if resp.status_code == 403:
            print(
                "  -> 403 from the reviews endpoint. This almost always means "
                "the project doesn't have Business Profile API access granted "
                "yet — see the note at the top of this file."
            )
            return reviews
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
        normalized.append({
            "platform": "Google",
            "listing": listing_name,
            "author": mask_reviewer_name(r.get("reviewer", {}).get("displayName")),
            "rating": rating,
            "text": r.get("comment", ""),
            "date": r.get("createTime", "")[:10] or None,
            "hasReply": has_reply,
            "sentiment": "Positive" if rating and rating >= 4 else ("Negative" if rating and rating <= 2 else "Neutral"),
            # Places/Business Profile APIs don't expose a public deep link to
            # an individual review — the mockup's "View source" links fall
            # back to a search query for this same reason.
            "sourceUrl": None,
        })
    return normalized


def build_aggregate(listing_name, normalized_reviews):
    total = len(normalized_reviews)
    if total == 0:
        return {
            "listing": listing_name,
            "rating": None,
            "reviewCount": 0,
            "responseRate": None,
            "pendingReplies": 0,
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
            raw_reviews = get_reviews(creds, account_name, loc_name)
            normalized = normalize_reviews(listing_name, raw_reviews)
            all_raw.extend(normalized)
            all_aggregate.append(build_aggregate(listing_name, normalized))

    with open("reviews_aggregate.json", "w") as f:
        json.dump(all_aggregate, f, indent=2)
    with open("reviews_raw.json", "w") as f:
        json.dump(all_raw, f, indent=2)

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
