"""reddit_public_deletion_job.py — the 48-hour deletion-propagation
commitment, honoured for `reddit_public` rows without an API credential.

Why this exists
----------------
`docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` commits, in writing
and under signature, to removing content and author-identifying data
within 48 hours of its deletion on Reddit. `reddit_deletion_job.py`
implements that — through PRAW, which needs the four Reddit credentials
that have never been obtained and now cannot be: Reddit closed self-serve
registration, and the free approval path requires declaring
non-commercial use, which contradicts that same signed application. So
that job has recorded `Missing required Reddit credential(s)` on every
run it has ever had, and the commitment has never once been honoured.

`reddit_public_rss_job.py` then made this worse rather than better: it
put real Reddit posts in the database via the public feed, with no
deletion re-check covering them at all, because `reddit_deletion_job` is
scoped to `source="reddit"`. Storing someone's Reddit post indefinitely
after they delete it is precisely what the commitment promises not to do,
and "we are waiting on Reddit" is not a defence for content we obtained
without waiting on Reddit.

This job closes that gap with the same public, unauthenticated feed the
ingestion job uses. It does not need Reddit's permission, so it works
today, and it does not wait on an approval that may never come.

How an item is re-checked without a credential
-----------------------------------------------
`https://www.reddit.com/comments/<base36 id>.rss`, with a descriptive
User-Agent. Probed live before this was written, because the obvious
candidates do not work:

  - `/api/info.json?id=t3_...`  -> HTTP 403. Reddit serves the JSON API
    only to OAuth clients now.
  - `/comments/<id>.json`       -> HTTP 403, same reason.
  - `/comments/<id>.rss`        -> HTTP 200 with the submission and its
    comments as Atom entries. This is the one that works.

THE CRITICAL DISTINCTION, and the whole reason this file is not a copy of
reddit_deletion_job.py with a different fetch call:

  - A post that does not exist returns **HTTP 404**, with a small
    well-formed feed carrying no entries.
  - Being rate-limited returns **HTTP 429** with a zero-length body.

Both were observed directly. They are cleanly distinguishable, and
everything below depends on that.

Why the failure policy is the INVERSE of reddit_deletion_job's
---------------------------------------------------------------
`reddit_deletion_job._is_deleted_upstream()` treats ANY fetch failure as
"deleted", and says so deliberately: a transient error scrubbing a live
row is the acceptable direction to err, versus missing a real deletion.
That reasoning is sound **for PRAW**, which distinguishes not-found from
transport failure internally and manages rate limits for you, so an
exception reaching that code really does mean the object is gone.

It would be actively dangerous here. Unauthenticated Reddit rate-limits
hard and often — a 429 arrived on the second consecutive request during
the probe that designed this file. Treating a 429 as a deletion would
scrub the entire Reddit store on a bad afternoon, and scrubbing cannot be
undone: `_scrub()` discards the text, and `deleted_at` is never cleared.
Losing real data to a rate limit is a worse and far more likely failure
than being one cycle late to a real deletion.

So this job scrubs ONLY on positive evidence of removal:

  - HTTP 404 on the submission's feed, or
  - the submission still served, but its body is Reddit's own
    "[deleted]"/"[removed]" tombstone.

Everything else — 429, 5xx, a network error, an unparseable body, a
comment whose presence cannot be established — is recorded as
**unverified** and the row is left exactly as it is, to be retried on the
next pass.

Unverified rows are not silently dropped, because "we could not check" is
the state that actually threatens the commitment. A pass that leaves any
row unverified reports PARTIAL with the count and reason, and
`python -m app.admin reddit-compliance` reports the oldest row that has
not been verified within the 48-hour window. That command, not this job's
SUCCESS status, is the thing that answers "are we keeping the promise."

Comments (t1_) are best-effort by design
-----------------------------------------
A comment has no feed of its own; it appears as an entry inside its
parent thread's feed. That gives one conclusive signal and one trap:

  - Parent thread 404s -> the comment is gone too. Conclusive, scrubbed.
  - Comment present, body tombstoned -> deleted. Conclusive, scrubbed.
  - Comment simply ABSENT from the parent's feed -> NOT treated as
    deleted. The feed truncates long threads, so absence genuinely does
    not prove removal. Recorded unverified.

The current `reddit_public` corpus is entirely submissions (t3_), because
`search.rss` returns posts; the comment path exists so a future comment
row is covered rather than silently unhandled.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Mention, RunStatus
from app.repository import start_run
from fetch_reddit_mentions import DELETED_MARKERS, fullname_kind
from fetch_rss import USER_AGENT, FeedParseError, parse_feed
from http_utils import RetryExhaustedError, get_with_retry

SOURCE_NAME = "reddit_public_deletion_check"

# Re-checks rows another job already fetched; never touches an external
# DATA source. Must not move lastSyncedAt. See app/jobs/__init__.py.
IS_DATA_SOURCE = False

# The source whose rows this job is responsible for. Deliberately NOT
# "reddit" - reddit_deletion_job.py owns those, through PRAW, and the two
# must not both scrub the same rows on different evidence.
TARGET_SOURCE = "reddit_public"

# Deliberately far smaller than reddit_deletion_job.BATCH_SIZE (100).
# That job spends one PRAW call per row against an authenticated rate
# limit; this one spends an unauthenticated HTTP request per row, and
# unauthenticated Reddit starts refusing at a handful of requests a
# minute. 25 rows at SECONDS_BETWEEN_CHECKS apart is about two and a half
# minutes of wall clock, which fits comfortably inside a scheduler pass.
BATCH_SIZE = 25

# Matches reddit_public_rss_job.SECONDS_BETWEEN_QUERIES, for the same
# measured reason: back-to-back unauthenticated requests get 429'd, and a
# fixed wait is the right tool for a limit that is known in advance -
# retry backoff just spends failed round trips discovering it again.
SECONDS_BETWEEN_CHECKS = 6.0

# Give up the pass after this many consecutive rate-limited checks.
# Past this point Reddit has made its position clear, and continuing only
# burns the remaining rows' turns without checking anything - worse than
# stopping, because a row that was "checked" against a 429 still had its
# place in the queue spent. Stopping early leaves them at the front of
# the next pass, since _select_batch() orders by last-touched.
MAX_CONSECUTIVE_RATE_LIMITS = 3

# The window the signed application commits to. Used only for reporting
# (see app.admin's reddit-compliance command) - this job scrubs as soon
# as it finds a deletion, rather than waiting for any deadline.
COMMITMENT_WINDOW_HOURS = 48


@dataclass(frozen=True)
class CheckResult:
    """The outcome of re-checking one row.

    `deleted` and `unverified` are deliberately separate booleans rather
    than a single tri-state string, so that the one dangerous mistake -
    reading "we could not check" as "it is gone" - requires actively
    ignoring a field rather than mishandling a default."""

    deleted: bool
    unverified: bool
    reason: str = ""

    @property
    def alive(self) -> bool:
        return not self.deleted and not self.unverified


GONE = CheckResult(deleted=True, unverified=False, reason="404")
TOMBSTONED = CheckResult(deleted=True, unverified=False, reason="body is [deleted]/[removed]")
ALIVE = CheckResult(deleted=False, unverified=False)


def _unverified(reason: str) -> CheckResult:
    return CheckResult(deleted=False, unverified=True, reason=reason)


def _base36_id(fullname: str) -> str:
    """"t3_abc123" -> "abc123"."""
    return fullname.split("_", 1)[1] if "_" in fullname else fullname


def _thread_id_from_url(url: str | None) -> str | None:
    """Pull the submission's base36 id out of a stored comment permalink.

    A comment has no feed of its own, so re-checking one means fetching
    its parent thread - and the parent's id is only available from the
    permalink we stored at ingestion:
    /r/<sub>/comments/<thread_id>/<slug>/<comment_id>/
    Returns None if the URL is not that shape, which is reported as
    unverified rather than guessed at."""
    if not url:
        return None
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if "comments" not in parts:
        return None
    index = parts.index("comments")
    return parts[index + 1] if len(parts) > index + 1 else None


def feed_url(base36_id: str) -> str:
    """The per-submission Atom feed. See module docstring for why this
    endpoint specifically, and what the alternatives return."""
    return f"https://www.reddit.com/comments/{base36_id}.rss"


def _fetch_thread(base36_id: str) -> tuple[list[dict] | None, CheckResult | None]:
    """Fetch and parse one submission's feed.

    Returns (entries, None) when the thread was served and parsed, or
    (None, CheckResult) when the outcome is already decided - GONE for a
    404, unverified for anything else. Never raises."""
    try:
        response = get_with_retry(
            feed_url(base36_id), headers={"User-Agent": USER_AGENT}, timeout=25
        )
    except RetryExhaustedError as exc:
        # http_utils retries 429/5xx and raises this once exhausted. The
        # single most likely outcome on this endpoint, and the single
        # most important one NOT to read as a deletion.
        return None, _unverified(f"rate-limited or unreachable ({exc})")

    if response.status_code == 404:
        # Positive evidence. Reddit serves a valid, empty feed with a 404
        # for an id it does not have - distinct from the zero-length 429
        # body above.
        return None, GONE

    if response.status_code != 200:
        return None, _unverified(f"HTTP {response.status_code}")

    try:
        return parse_feed(response.content), None
    except FeedParseError as exc:
        # A 200 carrying something that is not a feed. Could be an error
        # page or an interstitial; either way it is not evidence of
        # deletion.
        return None, _unverified(f"unparseable feed ({exc})")


def _is_tombstone(entry: dict) -> bool:
    """True if this entry's body is Reddit's own removal placeholder.

    An object can outlive its content: the submission still resolves, but
    the body reads "[deleted]" or "[removed]". reddit_deletion_job.py
    checks the same two markers, imported from the same place so the two
    jobs cannot drift on what a tombstone is."""
    body = (entry.get("summary") or "").strip()
    return body in DELETED_MARKERS


def check_submission(base36_id: str) -> CheckResult:
    """Re-check one submission. See module docstring for the policy."""
    entries, decided = _fetch_thread(base36_id)
    if decided is not None:
        return decided
    if not entries:
        # 200, parsed, but no entries at all. Unexpected - a live thread
        # always carries at least its own entry - so it is not treated as
        # evidence either way.
        return _unverified("feed served but empty")
    return TOMBSTONED if _is_tombstone(entries[0]) else ALIVE


def check_comment(fullname: str, url: str | None) -> CheckResult:
    """Re-check one comment via its parent thread. Best-effort by design -
    see the module docstring's final section for why an absent comment is
    NOT treated as a deleted one."""
    thread_id = _thread_id_from_url(url)
    if thread_id is None:
        return _unverified("no parent thread id in the stored permalink")

    entries, decided = _fetch_thread(thread_id)
    if decided is not None:
        # A 404 here means the whole thread is gone, which takes the
        # comment with it. Conclusive.
        return decided
    if not entries:
        return _unverified("parent thread served but empty")

    for entry in entries:
        if entry.get("guid") == fullname:
            return TOMBSTONED if _is_tombstone(entry) else ALIVE

    # Present-and-tombstoned is conclusive; simply absent is not, because
    # the feed truncates long threads.
    return _unverified("comment not present in the parent thread's feed (may be truncated)")


def check_row(mention: Mention) -> CheckResult:
    kind = fullname_kind(mention.external_id)
    if kind == "submission":
        return check_submission(_base36_id(mention.external_id))
    if kind == "comment":
        return check_comment(mention.external_id, mention.url)
    return _unverified(f"unrecognized fullname {mention.external_id!r}")


def _scrub(mention: Mention) -> None:
    """Remove content and author-identifying data, and mark the row.

    Deliberately identical in scope to reddit_deletion_job._scrub(): text,
    author and raw_payload go; venue, url, published_at, sentiment and
    rating stay. The commitment covers Reddit's content and author data,
    not Remedy Pulse's own record of having once observed an item, and
    keeping that record is what lets this system prove the scrub happened
    at all."""
    mention.text = None
    mention.author = None
    mention.raw_payload = None
    mention.deleted_at = datetime.now(timezone.utc)


def _select_batch(session: Session) -> list[Mention]:
    """Up to BATCH_SIZE not-yet-deleted rows, least-recently-touched
    first.

    updated_at advances whenever the row is re-ingested or scrubbed, so
    ordering by it ascending means every row gets its turn across enough
    passes instead of the same few starving the rest — the same fairness
    argument reddit_deletion_job.py's Batching section makes."""
    return list(
        session.execute(
            select(Mention)
            .where(Mention.source == TARGET_SOURCE, Mention.deleted_at.is_(None))
            .order_by(Mention.updated_at.asc())
            .limit(BATCH_SIZE)
        ).scalars()
    )


def rows_overdue_for_verification(
    session: Session, *, now: datetime | None = None
) -> list[Mention]:
    """Stored rows not touched within COMMITMENT_WINDOW_HOURS.

    This is the compliance question, and it is deliberately not the same
    question as "did the last run succeed". A run can report SUCCESS
    having verified a handful of rows while others sit unchecked for
    days; only this tells you whether the 48-hour promise is actually
    being kept. Read by `python -m app.admin reddit-compliance`."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=COMMITMENT_WINDOW_HOURS)
    rows = session.execute(
        select(Mention)
        .where(Mention.source == TARGET_SOURCE, Mention.deleted_at.is_(None))
        .order_by(Mention.updated_at.asc())
    ).scalars()
    # Compared in Python rather than SQL: SQLite (the test backend) hands
    # back naive datetimes for a tz-aware column, so a direct comparison
    # against an aware cutoff raises. Same problem app.scheduler
    # ._as_aware_utc() documents.
    overdue = []
    for row in rows:
        updated = row.updated_at
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            overdue.append(row)
    return overdue


def run(session: Session) -> None:
    """One deletion-check pass.

    Ledger counters, repurposed the same way reddit_deletion_job.py
    repurposes them (and documented here so a future reader of the ledger
    is not misled):
      - items_seen     = rows CONCLUSIVELY checked this pass. A row we
                         could not reach is not counted as seen, because
                         counting it would make the ledger overstate how
                         much of the commitment is actually being met.
      - items_ingested = rows found deleted upstream and scrubbed.

    Status is marked explicitly rather than inferred: start_run() reads
    items_ingested < items_seen as PARTIAL, which is right for an
    ingestion job and wrong here, where scrubbing nothing is the normal
    healthy outcome. PARTIAL here means something could not be verified."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        batch = _select_batch(session)
        unverified: list[str] = []
        consecutive_rate_limits = 0
        stopped_early = False

        for index, mention in enumerate(batch):
            if index:
                time.sleep(SECONDS_BETWEEN_CHECKS)

            result = check_row(mention)

            if result.unverified:
                unverified.append(f"{mention.external_id}: {result.reason}")
                if "rate-limited" in result.reason:
                    consecutive_rate_limits += 1
                    if consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
                        stopped_early = True
                        break
                continue

            consecutive_rate_limits = 0
            recorder.items_seen += 1

            if result.deleted:
                _scrub(mention)
                recorder.items_ingested += 1

        if unverified:
            detail = "; ".join(unverified[:3])
            if stopped_early:
                detail = (
                    f"stopped after {MAX_CONSECUTIVE_RATE_LIMITS} consecutive rate limits; "
                    f"{len(unverified)} row(s) unverified this pass — {detail}"
                )
            else:
                detail = f"{len(unverified)} row(s) unverified this pass — {detail}"
            recorder.mark(RunStatus.PARTIAL, error=detail)
        else:
            recorder.mark(RunStatus.SUCCESS)
