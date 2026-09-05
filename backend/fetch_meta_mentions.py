"""fetch_meta_mentions.py — Pulls Instagram/Facebook comments and mentions
via Meta's Graph API (graph.facebook.com) and writes a normalized JSON
file:

  meta_mentions.json -> {"fetchedAt": <ISO-8601 UTC>, "mentions": [...],
                          "capabilities": {...}}

IMPORTANT — read before running:
As of this writing there is no Meta App Review access granted for this
project (checklist item 1.3, still open — a procurement/approval status,
not something this script can resolve). Meta's Graph API requires (a) the
Instagram/Facebook account connected as a Business asset, and (b) App
Review approval for each permission scope this script uses. Per 1.3's own
HEADS-UP: reading comments on your own posts, reading comments/mentions
where you're tagged, and reading mentions in someone else's caption are
THREE DISTINCT permission scopes with three distinct review outcomes — a
team can plausibly have one approved and the other two still pending.
This script is written so each of the three capabilities below fails
independently and clearly, and so the whole thing is ready to produce
real data the moment credentials/permissions exist — same "build it now,
unblock it later" precedent as fetch_owned_reviews.py before Business
Profile access was granted. It never fabricates Instagram/Facebook data
or pretends a permission is granted when it isn't; there are no
credentials in this environment, so this has only ever been exercised
against mocked HTTP responses (see tests/test_fetch_meta_mentions.py).

Three capabilities, three functions, three independent outcomes
-----------------------------------------------------------------
1. fetch_instagram_comments() — GET /{ig-media-id}/comments for each of
   Remedy's own recent media (found via GET /{ig-user-id}/media). Reads
   comments on Remedy's own posts.
2. fetch_instagram_mentions() — GET /{ig-user-id}/tags: media where the
   Remedy IG Business account is TAGGED by someone else — i.e. mentioned
   in someone else's caption/post. A distinct Graph API edge (and a
   distinct App Review permission) from (1) — do not collapse these into
   one function; Meta reviews and can grant/deny them independently.
3. fetch_facebook_comments() — GET /{page-post-id}/comments for each of
   Remedy's own Facebook Page posts (found via GET /{page-id}/posts).

Each function is independently callable and independently fails: a
missing/denied permission for one must never block the other two — the
same per-item resilience pattern fetch_owned_reviews.py already uses
(try/except per listing, never a script-wide abort on one branch's
failure). Each returns a dict:
  {"capability": <name>, "status": <status>, "items": [...], "error": str|None}
where `status` is one of:
  "ok"             - the Graph API call(s) succeeded (items may be empty
                      — a capability with zero comments/mentions right
                      now is a real "ok, nothing new" outcome, not a
                      failure).
  "not_configured" - the specific env var this capability needs isn't
                      set (see Credentials below). A team might have
                      META_ACCESS_TOKEN and META_PAGE_ID but not yet
                      META_IG_BUSINESS_ACCOUNT_ID, and that must not look
                      like a failed Instagram call.
  "access_denied"  - Graph API returned a permission-shaped error (missing
                      scope, App Review not granted, account not yet
                      connected as a Business asset, etc.) for this
                      specific capability.
  "error"          - the request failed even after retries (see
                      http_utils.get_with_retry), or Graph API returned
                      some other non-permission error.

Credentials (read via load_dotenv(), like every other connector here)
-----------------------------------------------------------------------
  META_ACCESS_TOKEN            - required for ALL THREE capabilities (a
                                  Page/user access token carrying whatever
                                  scopes have been granted so far).
                                  Missing entirely -> SystemExit; the
                                  script refuses to start at all, same as
                                  fetch_owned_reviews.py's missing token
                                  file.
  META_IG_BUSINESS_ACCOUNT_ID   - the Instagram Business Account ID linked
                                  to Remedy's Page. Required for BOTH
                                  fetch_instagram_comments() and
                                  fetch_instagram_mentions() — missing it
                                  marks just those two "not_configured",
                                  not the whole run.
  META_PAGE_ID                  - Remedy's Facebook Page ID. Required only
                                  for fetch_facebook_comments().
  META_API_VERSION              - optional, defaults to "v19.0". Graph API
                                  versions deprecate roughly yearly; bump
                                  this without touching the code.

Unlike the Google scripts (exactly one credential, one SystemExit check),
this single credential (META_ACCESS_TOKEN) unlocks up to three
independently configured capabilities layered on top of it — so main()
checks the token once (hard SystemExit if absent) and then checks each
capability's own env var independently (soft "not_configured" status,
the script keeps running and still attempts the other two).

PII masking (checklist 5.3's Meta slice)
-------------------------------------------
Instagram handles and Facebook commenter names get different masking
treatment, on purpose:
  - An Instagram handle (e.g. "@glowwithsab") is already a pseudonym, the
    same category of identifier as a Reddit username — there is no "real
    name" embedded in it to partially preserve the way
    fetch_owned_reviews.mask_reviewer_name() preserves a first name.
    mask_instagram_handle() below hashes it into a short, stable,
    non-reversible token instead: the same handle always maps to the same
    masked value (so dedup/analytics across repeated mentions from the
    same account still works), but the original handle text is never
    stored or displayed — unlike a truncation, which would still leak
    part of the original string.
  - A Facebook commenter name (e.g. "Sabrina Cruz") is typically a real
    name, the same shape as a Google reviewer's displayName — so this
    reuses mask_reviewer_name() from fetch_owned_reviews.py unchanged
    (imported, not reimplemented) rather than inventing new first-name-
    plus-initial logic here.

Usage:
    python fetch_meta_mentions.py
"""

import hashlib
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from fetch_owned_reviews import mask_reviewer_name
from http_utils import RetryExhaustedError, get_with_retry

load_dotenv()

API_VERSION = os.getenv("META_API_VERSION", "v19.0")
GRAPH_BASE = f"https://graph.facebook.com/{API_VERSION}"

# A budget on how many of Remedy's own media/posts this script walks per
# run, and how many comments per media/post — not a hard Graph API limit,
# just this script's own request budget, same "keep the request count
# bounded" spirit as fetch_news_articles.py's NEWS_SEARCH_TERMS note.
MAX_OWN_MEDIA = 25
MAX_OWN_POSTS = 25
MAX_COMMENTS_PER_ITEM = 100


class MetaAccessDenied(Exception):
    """Raised when Graph API responds with a permission-shaped error for a
    specific capability (missing scope, App Review not granted, the
    account isn't connected as a Business asset yet, etc.) — analogous to
    fetch_owned_reviews.ReviewsAccessDenied. Never swallowed into an empty
    result; the caller records it as this capability's own "access_denied"
    status, distinct from a generic "error"."""


def _is_permission_error(payload):
    """Graph API errors come back as {"error": {"message", "type", "code",
    ...}} alongside an HTTP 4xx. Permission problems are consistently
    surfaced as error type "OAuthException", or one of a small, documented
    set of codes for a missing/ungranted scope (10 = permission denied,
    200 = permissions error, 294 = insufficient developer role — see
    Meta's Graph API error-handling docs). Anything else (a bad ID, a
    malformed param) is a genuine "error", not "access_denied" —
    conflating the two would hide a real bug behind a "just waiting on
    App Review" status."""
    error = (payload or {}).get("error") or {}
    if error.get("type") == "OAuthException":
        return True
    return error.get("code") in (10, 200, 294)


def _graph_get(url, params):
    """GET one Graph API endpoint through http_utils.get_with_retry, and
    raise MetaAccessDenied for a permission-shaped error instead of
    letting it fall through to raise_for_status()'s generic HTTPError —
    the same explicit-status-check shape as
    fetch_owned_reviews.get_reviews's 403 check."""
    resp = get_with_retry(url, params=params)
    if resp.ok:
        return resp.json()
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if _is_permission_error(payload):
        raise MetaAccessDenied(
            f"Graph API denied access for {url}: {payload.get('error')}"
        )
    resp.raise_for_status()


def _paginate(url, params, *, max_items):
    """Follow Graph API cursor pagination (`paging.next`) until either the
    API stops returning a next page or `max_items` is reached. Meta embeds
    the access token and cursor in `paging.next` as a complete URL, so
    subsequent calls pass no extra params of their own."""
    items = []
    next_url = url
    next_params = params
    while next_url and len(items) < max_items:
        data = _graph_get(next_url, next_params)
        items.extend(data.get("data", []))
        next_url = (data.get("paging") or {}).get("next")
        next_params = None
    return items[:max_items]


def mask_instagram_handle(handle):
    """Masks an Instagram handle/username into a stable, non-reversible
    token. Deliberately NOT fetch_owned_reviews.mask_reviewer_name()'s
    first-name-plus-initial approach — see the module docstring's PII
    masking section for why an Instagram handle (a pseudonym with no
    "real name" component to partially keep) needs different treatment
    than a Facebook commenter's real name. Hashing rather than truncating
    is what makes this non-reversible while staying deterministic: the
    same handle always maps to the same masked value — which is what
    dedup across repeated mentions from one account needs — without ever
    storing or displaying the original handle text."""
    if not handle:
        return "ig_unknown"
    normalized = handle.strip().lstrip("@").lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"ig_user_{digest}"


def _mention_row(*, platform, author, text, published_at, source_url, venue, raw):
    return {
        "platform": platform,
        "author": author,
        "text": text,
        # Sentiment classification is Phase 6's job, applied consistently
        # across every source — not invented ad hoc inside this connector
        # (same stance fetch_news_articles.py already takes for articles).
        "sentiment": None,
        "date": published_at,
        "sourceUrl": source_url,
        "venue": venue,
        "raw": raw,
    }


# --- Capability 1: Instagram comments on Remedy's own posts ---


def fetch_instagram_comments(access_token, ig_business_account_id):
    """GET /{ig-media-id}/comments for each of Remedy's own recent media
    (found via GET /{ig-user-id}/media). Independent of
    fetch_instagram_mentions() and fetch_facebook_comments() — a missing
    or denied permission here must never block either of those."""
    capability = "instagram_comments"
    if not ig_business_account_id:
        return {"capability": capability, "status": "not_configured", "items": [], "error": None}

    try:
        media = _paginate(
            f"{GRAPH_BASE}/{ig_business_account_id}/media",
            {"fields": "id,caption,permalink,timestamp", "access_token": access_token},
            max_items=MAX_OWN_MEDIA,
        )
        items = []
        for m in media:
            comments = _paginate(
                f"{GRAPH_BASE}/{m['id']}/comments",
                {"fields": "id,text,username,timestamp", "access_token": access_token},
                max_items=MAX_COMMENTS_PER_ITEM,
            )
            for c in comments:
                items.append(_mention_row(
                    platform="instagram",
                    author=mask_instagram_handle(c.get("username")),
                    text=c.get("text"),
                    published_at=c.get("timestamp"),
                    source_url=m.get("permalink"),
                    venue="comment_on_own_post",
                    raw={"comment_id": c.get("id"), "media_id": m.get("id")},
                ))
        return {"capability": capability, "status": "ok", "items": items, "error": None}
    except MetaAccessDenied as exc:
        return {"capability": capability, "status": "access_denied", "items": [], "error": str(exc)}
    except RetryExhaustedError as exc:
        return {"capability": capability, "status": "error", "items": [], "error": str(exc)}


# --- Capability 2: Instagram mentions (tagged in someone else's post) ---


def fetch_instagram_mentions(access_token, ig_business_account_id):
    """GET /{ig-user-id}/tags: media where Remedy's IG Business account is
    TAGGED by someone else — i.e. mentioned in someone else's
    caption/post, a distinct Graph API edge (and a distinct App Review
    permission) from fetch_instagram_comments() above. See the module
    docstring's HEADS-UP: do not collapse this into the comments function
    even though both return "media-shaped" objects — Meta reviews and can
    grant/deny these two independently, so a team can have one approved
    and not the other."""
    capability = "instagram_mentions"
    if not ig_business_account_id:
        return {"capability": capability, "status": "not_configured", "items": [], "error": None}

    try:
        tagged_media = _paginate(
            f"{GRAPH_BASE}/{ig_business_account_id}/tags",
            {"fields": "id,caption,permalink,timestamp,username", "access_token": access_token},
            max_items=MAX_OWN_MEDIA,
        )
        items = [
            _mention_row(
                platform="instagram",
                author=mask_instagram_handle(m.get("username")),
                text=m.get("caption"),
                published_at=m.get("timestamp"),
                source_url=m.get("permalink"),
                venue="tagged_mention",
                # No per-comment id here (unlike capability 1) — a tag is
                # a property of the whole media object, not a comment.
                raw={"media_id": m.get("id")},
            )
            for m in tagged_media
        ]
        return {"capability": capability, "status": "ok", "items": items, "error": None}
    except MetaAccessDenied as exc:
        return {"capability": capability, "status": "access_denied", "items": [], "error": str(exc)}
    except RetryExhaustedError as exc:
        return {"capability": capability, "status": "error", "items": [], "error": str(exc)}


# --- Capability 3: Facebook Page comments on Remedy's own posts ---


def fetch_facebook_comments(access_token, page_id):
    """GET /{page-post-id}/comments for each of Remedy's own Facebook Page
    posts (found via GET /{page-id}/posts). Independent of both Instagram
    capabilities above — a Facebook Page permission lapsing must not
    affect either Instagram capability, and vice versa."""
    capability = "facebook_comments"
    if not page_id:
        return {"capability": capability, "status": "not_configured", "items": [], "error": None}

    try:
        posts = _paginate(
            f"{GRAPH_BASE}/{page_id}/posts",
            {"fields": "id,message,permalink_url,created_time", "access_token": access_token},
            max_items=MAX_OWN_POSTS,
        )
        items = []
        for p in posts:
            comments = _paginate(
                f"{GRAPH_BASE}/{p['id']}/comments",
                {"fields": "id,message,from,created_time,permalink_url", "access_token": access_token},
                max_items=MAX_COMMENTS_PER_ITEM,
            )
            for c in comments:
                commenter_name = (c.get("from") or {}).get("name")
                items.append(_mention_row(
                    platform="facebook",
                    author=mask_reviewer_name(commenter_name),
                    text=c.get("message"),
                    published_at=c.get("created_time"),
                    source_url=c.get("permalink_url") or p.get("permalink_url"),
                    venue="comment_on_own_post",
                    raw={"comment_id": c.get("id"), "post_id": p.get("id")},
                ))
        return {"capability": capability, "status": "ok", "items": items, "error": None}
    except MetaAccessDenied as exc:
        return {"capability": capability, "status": "access_denied", "items": [], "error": str(exc)}
    except RetryExhaustedError as exc:
        return {"capability": capability, "status": "error", "items": [], "error": str(exc)}


def main():
    access_token = os.getenv("META_ACCESS_TOKEN")
    if not access_token:
        raise SystemExit(
            "META_ACCESS_TOKEN is not set. Copy .env.example to .env and "
            "fill it in — see this file's module docstring for the "
            "Credentials section (META_ACCESS_TOKEN, "
            "META_IG_BUSINESS_ACCOUNT_ID, META_PAGE_ID). Nothing runs "
            "without at least the access token."
        )

    ig_business_account_id = os.getenv("META_IG_BUSINESS_ACCOUNT_ID")
    page_id = os.getenv("META_PAGE_ID")

    results = [
        fetch_instagram_comments(access_token, ig_business_account_id),
        fetch_instagram_mentions(access_token, ig_business_account_id),
        fetch_facebook_comments(access_token, page_id),
    ]

    all_mentions = []
    capabilities = {}
    for result in results:
        capabilities[result["capability"]] = {
            "status": result["status"],
            "itemsFetched": len(result["items"]),
            "error": result["error"],
        }
        all_mentions.extend(result["items"])
        print(f"{result['capability']}: status={result['status']} items={len(result['items'])}")
        if result["error"]:
            print(f"  -> {result['error']}")

    not_configured = [name for name, c in capabilities.items() if c["status"] == "not_configured"]
    if not_configured:
        print(
            "\nNote: these capabilities are not configured and were "
            f"skipped: {', '.join(not_configured)}. See the module "
            "docstring's Credentials section for the env var each needs."
        )

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open("meta_mentions.json", "w") as f:
        json.dump(
            {"fetchedAt": fetched_at, "mentions": all_mentions, "capabilities": capabilities},
            f,
            indent=2,
        )

    print(f"\nWrote {len(all_mentions)} mentions to meta_mentions.json")


if __name__ == "__main__":
    main()
