"""
config.py — Maps Remedy Pulse's branch listings and tracked competitors to the
Google identifiers each API call needs. Fill in the REPLACE_ME placeholders
before running fetch_owned_reviews.py or fetch_competitor_ratings.py.

Where to find each ID:

- Business Profile location_id (owned listings only):
  Log into business.google.com with the account that manages these listings.
  The location ID appears in the dashboard URL, or can be listed
  programmatically via the Account Management API
  (GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts,
  then GET .../accounts/{accountId}/locations) — fetch_owned_reviews.py
  does this lookup for you and will print the IDs it finds, so you can
  paste them back in here once you've confirmed which is which.

- Competitor place_id (public listings, no ownership needed):
  Use Google's Place ID Finder tool:
  https://developers.google.com/maps/documentation/places/web-service/place-id
  Search for the exact clinic branch name + city to avoid picking up the
  wrong location.

- GNews API key (news/press ingestion — see
  docs/decisions/news-press-ingestion-path.md for why GNews specifically):
  Sign up at https://gnews.io — the free tier is self-serve (no approval
  wait, unlike Business Profile/Places), capped at 100 requests/day and
  articles from roughly the last month. Paste the key into .env as
  GNEWS_API_KEY, per .env.example.
"""

# Remedy's own branch listings — matches the four rows in the
# Reviews tab of remedy-pulse-mockup.html exactly. Keep these keys in sync
# if a branch is renamed there.
OWNED_LISTINGS = {
    "Remedy — BGC (One Uptown Residence)": {
        "location_id": "REPLACE_ME_BGC_LOCATION_ID",
    },
    "Club Remedy — BGC": {
        "location_id": "REPLACE_ME_CLUB_REMEDY_LOCATION_ID",
    },
    "Remedy — Vertis North": {
        "location_id": "REPLACE_ME_VERTIS_NORTH_LOCATION_ID",
    },
    "Skin Bar by Remedy — Greenhills Mall": {
        "location_id": "REPLACE_ME_GREENHILLS_LOCATION_ID",
    },
}

# Competitors tracked for rating benchmarking (public Places data only —
# Places API returns a rating + a small capped sample of reviews, not the
# full review history you'd get for an owned listing).
COMPETITOR_PLACE_IDS = {
    "Belo Medical Group": "REPLACE_ME_BELO_PLACE_ID",
    "Aivee Clinic": "REPLACE_ME_AIVEE_PLACE_ID",
    "Kamiseta Skin Clinic": "REPLACE_ME_KAMISETA_PLACE_ID",
    "SkinStation": "REPLACE_ME_SKINSTATION_PLACE_ID",
    "DermHQ": "REPLACE_ME_DERMHQ_PLACE_ID",
    "Luminisce": "REPLACE_ME_LUMINISCE_PLACE_ID",
}

# Search terms fetch_news_articles.py queries GNews with, one request per
# term, deduplicated by URL on the way out. Keep this narrow — broad terms
# like "Remedy" alone pull in unrelated results (the word is generic).
# Owner: Marketing should review/tune this list; it is a first pass, not
# a validated set.
NEWS_SEARCH_TERMS = [
    '"Remedy Skin Clinic"',
    '"Remedy BGC"',
    '"Remedy Vertis North"',
    '"Skin Bar by Remedy"',
]

# Maps an outlet name (as returned in a GNews article's source.name) to the
# Rate Card tier remedy-pulse-mockup.html's EMV tab uses to price a
# placement (see the "Rate Card" card on that tab). This is a BUSINESS
# JUDGMENT CALL — which publication counts as "National Newspaper" vs.
# "Lifestyle Magazine" vs. "Broadcast TV" is Gian/Marketing's call per the
# PRD's §6.3 note, not an engineering one. Seeded here from the six outlets
# already hardcoded in the EMV tab's sample data so the shape matches;
# every other outlet GNews returns comes back with tier=None and status
# "unmapped_outlet" (see fetch_news_articles.py) rather than a guessed
# tier, so nothing gets silently mispriced.
OUTLET_TIER_MAP = {
    "Rappler": "National News",
    "Philippine Star": "National News",
    "Manila Bulletin": "National News",
    "PeopleAsia": "Lifestyle Mag",
    "When In Manila": "Lifestyle Mag",
    "ANC": "Broadcast TV",
}

# ---------------------------------------------------------------------------
# Reddit mentions ingestion (checklist 4.3, 5.1, 5.2, 5.3 — see
# backend/fetch_reddit_mentions.py's module docstring for the connector
# itself, docs/decisions/reddit-integration-status.md and
# docs/decisions/reddit-deletion-propagation.md for why this exists and
# what it does/doesn't cover yet). This block is additive only — nothing
# above this line was changed to add it.
#
# Where to find Reddit credentials:
# Register a "script" app at https://www.reddit.com/prefs/apps (self-serve,
# no approval wait — distinct from the elevated commercial Data Access
# tier the use-case PDF describes, which is a separate, still-pending
# approval). That gives you REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET; the
# script authenticates as whichever Reddit account's
# REDDIT_USERNAME/REDDIT_PASSWORD you also provide. See
# fetch_reddit_mentions.py's module docstring for why this project uses
# that flow instead of a refresh-token flow.
# ---------------------------------------------------------------------------

# Subreddits searched for brand mentions, without the leading "r/". Seeded
# with r/PhilippinesSkincare specifically because it's the mockup's own
# existing sample Reddit mention (u/skinseeker_mnl in r/PhilippinesSkincare
# — remedy-pulse-mockup.html); the other two are a plausible first pass for
# where a PH skincare-clinic brand would actually get discussed. Owner:
# Marketing should review/tune this list — same "first pass, not a
# validated set" caveat NEWS_SEARCH_TERMS above already carries.
REDDIT_SUBREDDITS = [
    "PhilippinesSkincare",
    "AskPhilippines",
    "Philippines",
]

# Keyword terms searched within each subreddit above (one
# subreddit.search() call per subreddit/term pair — see
# fetch_reddit_mentions.py for why this is deliberately a search, not a
# stream). Mirrors NEWS_SEARCH_TERMS's own narrow-terms reasoning directly
# above: a bare brand word like "Remedy" is generic and pulls in unrelated
# results, so every term names a specific branch or sub-brand instead.
REDDIT_SEARCH_TERMS = [
    "Remedy Skin Clinic",
    "Remedy BGC",
    "Remedy Vertis North",
    "Skin Bar by Remedy",
]
