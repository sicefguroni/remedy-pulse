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
