"""fetch_reddit_mentions.py — Pulls public Reddit mentions of Remedy via
keyword search (PRAW) across a small configured list of subreddits, and
writes a normalized JSON file:

  reddit_mentions.json -> {"fetchedAt": <ISO-8601 UTC>, "mentions": [...]}

This is the engineering half of checklist item 4.3. Read
docs/decisions/04-reddit-integration-status.md and
docs/decisions/03-reddit-deletion-propagation.md before touching this file —
both were written specifically about this gap and this script closes only
part of it (see "What this does and does NOT do" below).

GROUND TRUTH — no real Reddit access exists yet
------------------------------------------------
There is no Reddit API access granted at the time this script was written.
PRAW's basic "script" app registration (see "Auth flow" below) is
self-serve — any Reddit account can create one at reddit.com/prefs/apps,
no approval wait — but the elevated commercial Data Access tier the
submitted use-case PDF (docs/Remedy Pulse_Reddit Data Access_Use Case.pdf)
describes is a separate, still-pending approval. This script is built so
it works the moment real script-app credentials exist (same precedent as
the Google connectors elsewhere in this folder, built and merged before
Business Profile access was granted) — it does not fabricate Reddit data,
and fails clearly (see "Credential handling" below) rather than pretending
credentials exist.

What this does and does NOT do
--------------------------------
- Searches a small, fixed list of subreddits (config.REDDIT_SUBREDDITS)
  for a small, fixed list of keyword terms (config.REDDIT_SEARCH_TERMS),
  one `subreddit.search()` call per (subreddit, term) pair, on whatever
  schedule a caller (app/jobs/reddit_job.py) runs this on. This is
  deliberately the C-1/C-5 "keyword-based searches across public Reddit
  content... a small, fixed list" shape the submitted Data Access Request
  describes — NOT `praw.models.reddit.subreddit.SubredditStream` or any
  other firehose/streaming ingestion. Do not add streaming here without
  re-reading that use-case document first; it would put actual behavior
  out of step with what was represented to Reddit in writing.
- PRAW's `Subreddit.search()` (the classic Reddit search endpoint it
  wraps) returns matching SUBMISSIONS only, not a crawl of every comment
  in every matched thread. This script does not additionally walk each
  matched submission's comment tree to find comment-level matches — that
  would be a much higher-volume kind of ingestion (fetching every reply in
  every thread this touches, not "searching for keyword mentions") and a
  materially different C-1/C-5 shape than what was represented to Reddit.
  The normalization/schema below is still written generically for either
  a submission ("t3_" fullname) or a comment ("t1_" fullname) — see
  `fullname_kind()` — so a future revision that does add a narrow,
  deliberate comment-matching path (e.g. re-reading a submission's own
  top-level replies) doesn't need a schema change to land.
- Masks every Reddit username before it is written to disk at all — see
  `mask_reddit_username()` below. The raw handle is never stored or
  printed.
- Leaves `sentiment` as None on every row. Sentiment classification is
  Phase 6's job, applied consistently across every source (see
  fetch_news_articles.py's identical note) — not invented ad hoc here.
- Does NOT implement the 48-hour deletion-propagation obligation (C-2).
  That is a separate, recurring re-check job that re-validates already-
  stored rows against Reddit — see app/jobs/reddit_deletion_job.py and
  docs/decisions/03-reddit-deletion-propagation.md for why "ingestion in
  reverse" has to be its own job rather than something bolted onto a
  fetch here.
- Resilience here is PRAW/prawcore's own (prawcore retries transient
  5xx/429 responses internally before this code ever sees them), not
  `http_utils.get_with_retry` — that helper wraps `requests.get` directly
  and doesn't apply to PRAW's own HTTP transport. What this script does
  match from the other connectors' house style is the shape of the
  result: one failing (subreddit, term) query is caught, logged, and
  skipped, never allowed to abort the rest of the run — see
  `fetch_all_mentions()`.

Auth flow — PRAW "script" app (client_id + client_secret + username +
password), not a refresh-token flow
------------------------------------------------------------------------
PRAW supports either a password-grant "script" app (four credentials: the
app's own client_id/client_secret, plus the Reddit account's own
username/password) or a refresh-token flow (an app authorized once,
interactively, against a user's Reddit account — the same shape as
Google's oauth_setup.py elsewhere in this repo). This script uses the
script-app flow because:
  - A Reddit "script" app type is registered for exactly this case — an
    app that runs as, and is fully owned by, one specific Reddit account,
    with no separate end-user consent screen needed (unlike Google's
    Business Profile OAuth, which needs oauth_setup.py's one-time browser
    authorization because it acts on behalf of a Business Profile listing
    the running account doesn't necessarily own outright).
  - It has one fewer moving part operationally: no token.json-equivalent
    file to keep alive, refresh, or rotate out-of-band (see checklist
    5.6's open question about token.json itself) — PRAW re-authenticates
    with these four env vars on every process start.
  - It matches this script's own "no PRAW call without full credentials"
    requirement more simply: four required env vars, one clear SystemExit
    if any is missing, versus a token file that has to already exist from
    a separate interactive step this script would then also have to guard.
The trade-off: the Reddit account's own password becomes a credential that
must be kept secret (in .env, never committed — same handling as every
other secret this repo already keeps out of git). If that trade-off stops
being acceptable, PRAW's `refresh_token=` constructor argument is a
drop-in swap for the four password-grant kwargs in `get_reddit_client()`.

Versioned User-Agent (checklist 5.2)
--------------------------------------
Reddit requires a descriptive, versioned User-Agent identifying the
application and its author on every request — see
docs/decisions/04-reddit-integration-status.md's verified quote from the
submitted Data Access Request ("a descriptive, versioned User-Agent string
identifies the application per Reddit's required format"). USER_AGENT
below is a module-level constant, not built from env vars, so it is
visible in code review exactly as it will be sent over the wire:
  - The "REPLACE_ME_REDDIT_USERNAME" placeholder MUST be filled in with
    the actual Reddit username of the account this app authenticates as
    before this script can meaningfully identify itself to Reddit — a
    placeholder-looking User-Agent is not "descriptive identification,"
    it's an unfixed TODO shipped to a third party.
  - The version segment ("v0.1.0") is a REAL, ONGOING obligation, not a
    one-time string: bump it whenever this connector's actual behavior
    changes (a new search shape, a new field written to raw_payload,
    etc.), the same way you'd bump a package version. Reddit's own
    guidance uses the version to distinguish between behaviors of the
    same named app over time — a User-Agent that never changes after the
    first release stops serving that purpose.
Format used: "<platform>:<app ID>:<version string> (by /u/<username>)",
per Reddit's documented convention.

PII masking (checklist 5.3's Reddit slice) — see mask_reddit_username()
below for the full design reasoning; it is intentionally not a copy of
fetch_owned_reviews.mask_reviewer_name().

Credential handling
--------------------
Reads REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME,
REDDIT_PASSWORD via `load_dotenv()` + `os.getenv(...)`. `get_reddit_client()`
raises `SystemExit` naming every missing one if any are absent — no PRAW
call (and no `import praw`) is attempted otherwise.

Usage:
    python fetch_reddit_mentions.py
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from config import REDDIT_SEARCH_TERMS, REDDIT_SUBREDDITS

load_dotenv()

# --- Reddit's required User-Agent format — see the module docstring's
# "Versioned User-Agent" section for why this is a constant (not derived
# from env) and why the version segment is a real, ongoing obligation.
USER_AGENT = "python:remedy-pulse-monitor:v0.1.0 (by /u/REPLACE_ME_REDDIT_USERNAME)"

CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")

# Reddit's own placeholder strings for content/authors that no longer
# exist. A submission whose body already reads one of these at fetch time
# has nothing left to ingest; an author of None (PRAW's representation of
# a deleted account) is handled separately in `_author_name()`.
DELETED_MARKERS = {"[deleted]", "[removed]"}

# One `subreddit.search()` call per (subreddit, term) pair, capped low —
# this is deliberately low-volume keyword polling (C-1/C-5), not an
# attempt to exhaustively harvest every match Reddit has ever indexed.
SEARCH_LIMIT_PER_QUERY = 25

# A short pause between queries, matching the other connectors' own
# between-request pacing (see fetch_news_articles.py, fetch_competitor_
# ratings.py) — this project's stated freshness target is same-day/
# next-day, not real-time, so there is no reason to hammer Reddit's API
# back-to-back.
_BETWEEN_QUERY_SLEEP_SECONDS = 0.2


# --- PII masking (5.3) ---

_USERNAME_PREFIX_LEN = 4
_HASH_SUFFIX_LEN = 8
# A static, non-secret "pepper" mixed into the hash below. This does NOT
# make the output cryptographically unguessable against a determined
# attacker holding a list of candidate usernames — it exists only so the
# stored value isn't a bare, well-known-algorithm hash of the raw handle,
# trivially reversible via an off-the-shelf rainbow table for common
# words. This is a de-identification measure in the spirit of the PH Data
# Privacy Act's minimization principle (the same principle
# fetch_owned_reviews.mask_reviewer_name() cites), aimed at routine
# display/export/audit — not a security control, and it should not be
# treated as one.
_MASK_PEPPER = "remedy-pulse-reddit-username-mask-v1"


def mask_reddit_username(username):
    """Masks a Reddit username (e.g. "skinseeker_mnl", as in the mockup's
    "u/skinseeker_mnl") for storage/display, per the PH Data Privacy Act
    minimization principle already established by
    fetch_owned_reviews.mask_reviewer_name() — but NOT that same function
    copied over, because a Reddit username needs a different design:

    mask_reviewer_name() truncates a real name to "first name + last
    initial" ("Anna Reyes" -> "Anna R.") because a real name has a
    first/last structure worth partially preserving. A Reddit username is
    already a self-chosen pseudonym, not a real name — it has no
    first/last structure to truncate, and truncating it the same way
    (e.g. "skinseeker_mnl" -> "skinseeker_m.") would both leak most of the
    original handle in full AND fail to minimize anything, since the
    "initial" of one made-up word carries essentially the same
    identifying signal as the word itself.

    Design instead: keep a short prefix (enough to stay visually
    recognizable to a human scanning a feed or audit view) plus a
    fixed-length hash suffix of the FULL original username (not just the
    visible prefix). The same input always maps to the same output — no
    per-call randomness/salt-per-row — which two things this system
    genuinely needs even after masking:
      - Deduplication: two mentions from the same Reddit user must still
        be recognizable downstream as the same author (e.g. "this user
        posted 3 times this week") without ever storing the raw handle.
      - Audit consistency: someone following up on a masked author across
        multiple stored mentions needs that masked value to be stable,
        not a fresh random string every ingestion run.
    The hash suffix is what keeps this non-reversible-to-plaintext in
    practice for someone who only has the masked output (see the
    _MASK_PEPPER comment above for the one caveat on what "non-
    reversible" does and doesn't cover) — a plain prefix-only truncation
    would instead still show most of a short username in the clear.

    "[deleted]"/"[removed]" (Reddit's own tombstone placeholders for an
    account that no longer exists — distinct from this function's own
    output) and empty/None input all map to the fixed string
    "Reddit user" rather than being hashed: there is no real username
    there to mask, and hashing an already-anonymous placeholder would
    misleadingly imply a specific (if hidden) individual still exists
    behind it.
    """
    if not username or username in DELETED_MARKERS:
        return "Reddit user"
    cleaned = username.strip()
    if not cleaned or cleaned in DELETED_MARKERS:
        return "Reddit user"
    prefix = cleaned[:_USERNAME_PREFIX_LEN]
    digest = hashlib.sha256(f"{_MASK_PEPPER}:{cleaned}".encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:_HASH_SUFFIX_LEN]}"


# --- Credential handling ---

_REQUIRED_CREDENTIAL_NAMES = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
)


def missing_credentials():
    """Returns the subset of _REQUIRED_CREDENTIAL_NAMES that are currently
    unset, reading the module-level CLIENT_ID/CLIENT_SECRET/
    REDDIT_USERNAME/REDDIT_PASSWORD globals above (not os.environ again) —
    so a caller (a test, or app/jobs/reddit_job.py checking before it even
    calls get_reddit_client()) that has monkeypatched one of those module
    attributes directly sees an answer consistent with what
    get_reddit_client() itself would decide."""
    values = {
        "REDDIT_CLIENT_ID": CLIENT_ID,
        "REDDIT_CLIENT_SECRET": CLIENT_SECRET,
        "REDDIT_USERNAME": REDDIT_USERNAME,
        "REDDIT_PASSWORD": REDDIT_PASSWORD,
    }
    return [name for name in _REQUIRED_CREDENTIAL_NAMES if not values[name]]


def get_reddit_client():
    """Builds an authenticated praw.Reddit client using the script-app
    flow (see module docstring's "Auth flow" section). Raises SystemExit
    naming every missing credential if any are absent — no PRAW call, and
    no `import praw`, happens otherwise. `praw` is imported lazily here
    (rather than at module import time) so this module — including
    mask_reddit_username() and the normalize functions below — stays
    importable in an environment where the `praw` package isn't installed
    at all, which is the current state of this project's shared test
    environment; see this connector's test file for how that boundary is
    mocked."""
    missing = missing_credentials()
    if missing:
        raise SystemExit(
            "Missing required Reddit credential(s): " + ", ".join(missing) + ". "
            "REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET come from registering a "
            "\"script\" app at https://www.reddit.com/prefs/apps (self-serve, "
            "no approval wait); REDDIT_USERNAME/REDDIT_PASSWORD are the "
            "credentials of the Reddit account that app is registered "
            "under. Copy .env.example to .env and fill them in. No PRAW "
            "call is attempted without all four."
        )
    import praw  # noqa: PLC0415 -- see docstring: deliberately lazy.

    return praw.Reddit(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        user_agent=USER_AGENT,
    )


# --- Normalization ---


def fullname_kind(fullname):
    """"t3_..." -> "submission", "t1_..." -> "comment", anything else ->
    None. Reddit's own fullname prefix scheme — reused as-is by
    app/jobs/reddit_deletion_job.py to decide whether to re-check a stored
    row via reddit.submission(...) or reddit.comment(...)."""
    if not fullname:
        return None
    if fullname.startswith("t3_"):
        return "submission"
    if fullname.startswith("t1_"):
        return "comment"
    return None


def _author_name(author):
    # PRAW represents a deleted Reddit account as author=None on the
    # submission/comment object itself, NOT as an author object whose name
    # happens to be "[deleted]" -- so this has to check for None first,
    # separately from DELETED_MARKERS (which covers a still-attributed
    # item whose BODY text was removed/deleted, a different case).
    if author is None:
        return None
    return getattr(author, "name", None)


def normalize_submission(submission, subreddit_name, matched_term):
    """Normalizes one PRAW Submission (the only kind Subreddit.search()
    actually returns — see the module docstring) into this connector's
    row shape. `fullname_kind(row["fullname"])` will always be
    "submission" for a row this function produces; the shape is written
    generically anyway (see fullname_kind()'s own docstring) so a future
    comment-matching path doesn't need a schema change to land."""
    raw_author = _author_name(getattr(submission, "author", None))
    selftext = (getattr(submission, "selftext", "") or "").strip()
    removed = selftext in DELETED_MARKERS
    title = getattr(submission, "title", None)
    text = None if removed else (selftext or title)

    created_utc = getattr(submission, "created_utc", None)
    published_at = (
        datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
        if created_utc is not None
        else None
    )

    permalink = getattr(submission, "permalink", None)
    source_url = f"https://www.reddit.com{permalink}" if permalink else None

    return {
        "platform": "Reddit",
        "source": "reddit",
        "redditKind": "submission",
        "fullname": submission.fullname,
        "subreddit": subreddit_name,
        "matchedTerm": matched_term,
        "author": mask_reddit_username(raw_author),
        "title": title,
        "text": text,
        "publishedAt": published_at,
        "sourceUrl": source_url,
        "sentiment": None,
        "status": "removed_at_fetch" if removed else "ok",
    }


def fetch_all_mentions(reddit):
    """Runs one `subreddit.search()` call per (subreddit, term) pair over
    config.REDDIT_SUBREDDITS x config.REDDIT_SEARCH_TERMS, normalizes and
    dedupes the results by fullname (the same submission can legitimately
    match more than one search term), and returns the flat list of
    normalized rows. A query that raises (a banned/private/nonexistent
    subreddit, a transient prawcore error not already retried away
    internally, etc.) is logged and skipped — never allowed to abort the
    rest of the run, matching this project's other connectors' per-item/
    per-query resilience shape."""
    seen_fullnames = set()
    mentions = []

    for subreddit_name in REDDIT_SUBREDDITS:
        for term in REDDIT_SEARCH_TERMS:
            print(f"Searching r/{subreddit_name} for {term!r}...")
            try:
                results = list(
                    reddit.subreddit(subreddit_name).search(term, limit=SEARCH_LIMIT_PER_QUERY)
                )
            except Exception as exc:
                print(f"ERROR: search failed for r/{subreddit_name} / {term!r}: {exc}")
                continue

            print(f"  -> {len(results)} result(s)")
            for submission in results:
                row = normalize_submission(submission, subreddit_name, term)
                if row["fullname"] in seen_fullnames:
                    continue
                seen_fullnames.add(row["fullname"])
                mentions.append(row)

            time.sleep(_BETWEEN_QUERY_SLEEP_SECONDS)

    return mentions


def main():
    reddit = get_reddit_client()
    mentions = fetch_all_mentions(reddit)

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open("reddit_mentions.json", "w") as f:
        json.dump({"fetchedAt": fetched_at, "mentions": mentions}, f, indent=2)

    print(f"\nWrote {len(mentions)} mentions to reddit_mentions.json")


if __name__ == "__main__":
    main()
