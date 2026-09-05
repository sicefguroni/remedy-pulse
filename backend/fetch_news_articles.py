"""fetch_news_articles.py — Pulls news/press coverage of Remedy via the
GNews API and writes a normalized JSON file:

  news_articles.json -> {"fetchedAt": <ISO-8601 UTC>, "articles": [...]}

This is the engineering half of checklist item 1.5 / decision doc
docs/decisions/02-news-press-ingestion-path.md: the EMV tab in
remedy-pulse-mockup.html is built entirely on press coverage, and before
this script there was no code, key, or config for any news source at all
— the decision doc recommends a time-boxed GNews evaluation, and this
script is that evaluation harness. It does NOT decide the vendor question
for you; see the decision doc for the fallback options if GNews's
coverage/quota turns out to be insufficient.

IMPORTANT — what this script does and does NOT do:
- It normalizes raw article metadata (outlet, headline, url, publish date,
  description). It does NOT compute an EMV monetary value. The mockup's
  EMV formula (Base AVE x Prominence x PubScore x PR_Credibility x
  Sentiment, see the "Rate Card" card and any row's expandable detail on
  the EMV tab) needs inputs — prominence, PubScore, sentiment — that a
  news search API doesn't provide and that are editorial/PR judgment
  calls, not facts a connector can fetch. Computing EMV from these raw
  articles is separate downstream work, the same way Clarity Index is
  computed from inputs rather than fetched (see
  docs/decisions/01-clarity-index-formula.md for the same pattern).
- It maps each outlet to a Rate Card tier (National News / Lifestyle Mag
  / Broadcast TV) ONLY for the six outlets already hardcoded in the
  mockup's sample EMV rows (config.OUTLET_TIER_MAP) — matching business
  judgment Marketing already made for those six, not a guess extended to
  every outlet GNews might return. Any other outlet comes back with
  tier=None and status="unmapped_outlet" so it doesn't get silently
  mispriced by whatever comes after this script.
- GNews free tier: 100 requests/day, results generally capped to roughly
  the last month. This script makes one request per NEWS_SEARCH_TERMS
  entry (see config.py) — keep that list short.

Usage:
    python fetch_news_articles.py
"""

import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from config import NEWS_SEARCH_TERMS, OUTLET_TIER_MAP
from http_utils import RetryExhaustedError, get_with_retry

load_dotenv()

API_KEY = os.getenv("GNEWS_API_KEY")
SEARCH_URL = "https://gnews.io/api/v4/search"

# lang/country narrow results to English-language Philippine coverage,
# matching the outlets already in the mockup's sample data (Rappler,
# Philippine Star, Manila Bulletin, ANC, etc).
LANG = "en"
COUNTRY = "ph"
MAX_RESULTS_PER_QUERY = 25


def fetch_articles_for_term(term):
    params = {
        "q": term,
        "lang": LANG,
        "country": COUNTRY,
        "max": MAX_RESULTS_PER_QUERY,
        "apikey": API_KEY,
    }
    resp = get_with_retry(SEARCH_URL, params=params)
    if resp.status_code == 403:
        raise SystemExit(
            "GNews returned 403 — this usually means GNEWS_API_KEY is "
            "invalid or the free-tier daily quota (100 requests/day) is "
            "already spent for today."
        )
    resp.raise_for_status()
    return resp.json().get("articles", [])


def normalize(raw_article):
    source = raw_article.get("source", {}) or {}
    outlet = source.get("name")
    tier = OUTLET_TIER_MAP.get(outlet)
    return {
        "outlet": outlet,
        "outletUrl": source.get("url"),
        "headline": raw_article.get("title"),
        "description": raw_article.get("description"),
        "url": raw_article.get("url"),
        # GNews returns publishedAt as a full ISO-8601 UTC timestamp already
        # (e.g. "2026-06-29T09:03:00Z") — kept as-is rather than truncated
        # to a bare date, unlike Google reviews' createTime, since the EMV
        # tab's "View source" / date-filter UI wants full precision here.
        "publishedAt": raw_article.get("publishedAt"),
        "tier": tier,
        # sentiment is intentionally None — GNews doesn't classify
        # sentiment, and this connector doesn't do its own classification
        # (that's Phase 6's job, applied consistently across every source,
        # not invented ad hoc per-connector here).
        "sentiment": None,
        "status": "ok" if tier else "unmapped_outlet",
    }


def dedupe_by_url(articles):
    seen = set()
    deduped = []
    for a in articles:
        url = a.get("url")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        deduped.append(a)
    return deduped


def main():
    if not API_KEY:
        raise SystemExit(
            "GNEWS_API_KEY is not set. Copy .env.example to .env and fill "
            "it in — sign up for a free key at https://gnews.io."
        )

    all_raw = []
    for term in NEWS_SEARCH_TERMS:
        print(f"Searching GNews for {term}...")
        try:
            raw = fetch_articles_for_term(term)
        except RetryExhaustedError as exc:
            print(f"ERROR: request failed (retries exhausted) for {term}: {exc}")
            continue
        print(f"  -> {len(raw)} result(s)")
        all_raw.extend(raw)
        time.sleep(0.2)

    all_raw = dedupe_by_url(all_raw)
    normalized = [normalize(a) for a in all_raw]

    unmapped = sorted({a["outlet"] for a in normalized if a["status"] == "unmapped_outlet" and a["outlet"]})
    if unmapped:
        print(
            "\nNote: these outlets have no Rate Card tier in "
            f"config.OUTLET_TIER_MAP and were written with tier=None: "
            f"{', '.join(unmapped)}. Add them once Marketing confirms "
            "which Rate Card row each belongs in."
        )

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open("news_articles.json", "w") as f:
        json.dump({"fetchedAt": fetched_at, "articles": normalized}, f, indent=2)

    print(f"\nWrote {len(normalized)} articles to news_articles.json")


if __name__ == "__main__":
    main()
