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

# The v1 backfill policy (checklist 9.2, PRD Non-Goals: "Deep historical
# backfill (>90 days). v1 starts tracking from launch forward; going back
# further is a data-availability and cost question best revisited once
# the core loop is proven."). Enforced centrally in app.jobs's shared
# is_within_backfill_window() helper, applied by every ingestion job
# before it calls record_ingestion() - not a per-adapter opt-in, so a
# future adapter can't forget it. Applies uniformly to both an initial
# backfill run and steady-state polling (in steady state it should almost
# never trigger, since polling only fetches recent items anyway - it's a
# safety bound, e.g. against a keyword search surfacing a 2-year-old post
# that still matches, not primarily a backfill-day mechanism).
BACKFILL_WINDOW_DAYS = 90

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

# ---------------------------------------------------------------------------
# Brand alias matching (checklist 8.8 — P0-10's "keyword-variant matching"
# requirement itself, per the checklist's own framing: "the mockup encodes
# brand aliases as HTML `title` tooltips... those tooltips are the
# requirement, stored in the only place they could be at mockup stage. Move
# them into config.")
#
# This data was RECOVERED from git history, not invented here: the Phase 7
# data-driven refactor (commit a18cd10) replaced the mockup's hand-written
# markup wholesale, which silently deleted the `title="Also matches: ..."`
# tooltips that carried this real domain knowledge (see the pre-refactor
# version at commit a18cd10~1 for the original source). Recovered here so
# it isn't lost, and because config is the right home for it, not HTML.
# ---------------------------------------------------------------------------

# Umbrella-brand aliases — any of these strings appearing in mention/review
# text should count toward that brand's identity, not just an exact name
# match. Keys match COMPETITOR_PLACE_IDS's formal names above (and
# Mention.venue's actual stored value for competitor rows, per
# app/jobs/google_places_job.py) so this dict can key off the same names
# without a second name-mapping layer; "Remedy" is the umbrella brand
# itself, with no analogous formal-name dict of its own.
BRAND_ALIASES = {
    "Remedy": [
        "Remedy Skin Solutions", "Remedy Skin", "Remedy BGC", "Remedy clinic",
        "Remedy GH", "Remedy Greenhills", "Remedy Vertis", "Remedy Vertis North",
        "Skin Bar by Remedy",
    ],
    "Aivee Clinic": ["Aivee Skin Spa", "Foleon by Aivee"],
    "Kamiseta Skin Clinic": ["Kamiseta Skin", "Kamiseta Skin Clinic"],
    # Belo Medical Group, SkinStation, DermHQ, Luminisce had no aliases
    # documented in the mockup's tooltips — not an oversight to silently
    # "fix" here by guessing; add real ones if/when Marketing identifies
    # variant names worth tracking for a given competitor.
}

# Branch-level aliases, IN ADDITION TO BRAND_ALIASES["Remedy"] above — a
# mention naming one of these maps to this SPECIFIC owned listing, not just
# "Remedy" generically (relevant for per-branch review/mention attribution,
# e.g. fetch_owned_reviews.py's OWNED_LISTINGS matching). Only two of the
# four owned listings had aliases documented in the mockup; preserved
# exactly as found rather than inventing the other two.
OWNED_LISTING_ALIASES = {
    "Remedy — Vertis North": ["Remedy Vertis", "Remedy Vertis North"],
    "Skin Bar by Remedy — Greenhills Mall": ["Remedy GH", "Remedy Greenhills", "Skin Bar by Remedy"],
}

# "Category Watch — Hair" (per the mockup's own note: "New tracked entities
# per Marketing's §18 update — not yet reflected in Share of Voice above").
# Each entity's keyword/boolean search query was explicitly marked "Boolean
# query pending" in the mockup — i.e. these were proposed for tracking but
# never actually wired into any adapter or matching logic. Preserved here
# as the pending-decision list it already was; NOT expanded into real
# tracking (no place_id, no search terms) without that decision being made
# and the Boolean query actually being written — doing so unilaterally
# would silently promote "someone typed this into the mockup once" into
# "this is now tracked," which is exactly the kind of claim-vs-enforcement
# gap this whole checklist exists to close, not reproduce.
CATEGORY_WATCH_HAIR_PENDING = [
    {"name": "Clinique de Paris", "note": None},
    {"name": "Foleon by Aivee", "note": "also under Aivee"},
    {"name": "Svenson", "note": None},
]

# Search terms fetch_news_articles.py queries GNews with, one request per
# term, deduplicated by URL on the way out. Keep this narrow — broad terms
# like "Remedy" alone pull in unrelated results (the word is generic).
# Owner: Marketing should review/tune this list; it is a first pass, not
# a validated set.
#
# Extended (8.8) with the aliases recovered into BRAND_ALIASES["Remedy"]
# above, MERGED with (not replacing) the original hand-picked terms below —
# "Remedy Skin Clinic" isn't in the recovered alias list but was already a
# deliberate term here, and several existing tests
# (test_jobs_news.py) mock specific search terms by exact string, so
# silently dropping any of the original terms would break real,
# already-passing test coverage for no reason. dict.fromkeys(...) dedupes
# while preserving first-seen order (a plain set() would not).
NEWS_SEARCH_TERMS = list(dict.fromkeys(
    [
        '"Remedy Skin Clinic"',
        '"Remedy BGC"',
        '"Remedy Vertis North"',
        '"Skin Bar by Remedy"',
    ]
    + [f'"{alias}"' for alias in BRAND_ALIASES["Remedy"]]
))

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
#
# Extended (8.8) the same way NEWS_SEARCH_TERMS was — merged with, not
# replacing, the original terms (test_fetch_reddit_mentions.py mocks
# specific terms by exact string; see NEWS_SEARCH_TERMS's comment above for
# the full reasoning).
REDDIT_SEARCH_TERMS = list(dict.fromkeys(
    [
        "Remedy Skin Clinic",
        "Remedy BGC",
        "Remedy Vertis North",
        "Skin Bar by Remedy",
    ]
    + BRAND_ALIASES["Remedy"]
))
