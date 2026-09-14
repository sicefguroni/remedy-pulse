"""Tests for app/jobs/reddit_public_deletion_job.py — the 48-hour
deletion commitment honoured without an API credential.

The one behaviour this file exists to protect above all others: a
rate-limited check must NEVER scrub a row. Unauthenticated Reddit 429s
readily — two of seven rows were rate-limited on the first live pass — and
a scrub cannot be undone. Treating "we could not reach it" as "it is
gone" would quietly destroy the store on a bad afternoon.

That is the inverse of reddit_deletion_job.py's policy, which treats any
fetch failure as a deletion. Sound there (PRAW distinguishes not-found
from transport failure itself), dangerous here. Several tests below exist
purely to stop someone "making the two consistent" later.

HTTP is mocked at get_with_retry as this job binds it; no test here
touches the network.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.jobs.reddit_public_deletion_job as deletion_job
from app.models import IngestionRun, Mention, RunStatus
from http_utils import RetryExhaustedError

LIVE_THREAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom">'
    b"<entry><id>t3_abc123</id><title>Dermatologist reco in BGC please</title>"
    b'<link href="https://www.reddit.com/r/skincare_ph/comments/abc123/x/" rel="alternate"/>'
    b"<updated>2026-09-10T11:02:25+00:00</updated>"
    b'<content type="html">Please help me find a good clinic.</content></entry>'
    b"<entry><id>t1_def456</id><title>a comment</title>"
    b'<link href="https://www.reddit.com/r/skincare_ph/comments/abc123/x/def456/" rel="alternate"/>'
    b"<updated>2026-09-10T12:00:00+00:00</updated>"
    b'<content type="html">Try Remedy in BGC.</content></entry>'
    b"</feed>"
)

TOMBSTONED_THREAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom">'
    b"<entry><id>t3_abc123</id><title>Dermatologist reco in BGC please</title>"
    b'<link href="https://www.reddit.com/r/skincare_ph/comments/abc123/x/" rel="alternate"/>'
    b"<updated>2026-09-10T11:02:25+00:00</updated>"
    b'<content type="html">[deleted]</content></entry>'
    b"</feed>"
)

# What Reddit actually returns for an id it does not have: a valid but
# empty feed, with a 404.
EMPTY_FEED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
)


class _Response:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    """The job waits SECONDS_BETWEEN_CHECKS between rows to stay under
    Reddit's unauthenticated limit. Correct in production, dead time
    here."""
    monkeypatch.setattr(deletion_job, "SECONDS_BETWEEN_CHECKS", 0)


def _respond(monkeypatch, handler):
    """Install a fake get_with_retry. `handler` takes the URL and returns
    a _Response, or raises RetryExhaustedError."""

    def fake_get(url, *, headers=None, params=None, timeout=None):
        return handler(url)

    monkeypatch.setattr(deletion_job, "get_with_retry", fake_get)


def _add_row(session, *, external_id="t3_abc123", age_days=1, **overrides):
    fields = {
        "source": "reddit_public",
        "kind": "mention",
        "external_id": external_id,
        "text": "Dermatologist reco in BGC please",
        "author": "someone",
        "url": "https://www.reddit.com/r/skincare_ph/comments/abc123/x/",
        "raw_payload": {"guid": external_id},
        "updated_at": datetime.now(timezone.utc) - timedelta(days=age_days),
        **overrides,
    }
    mention = Mention(**fields)
    session.add(mention)
    session.flush()
    return mention


def _run(session):
    """Run one pass and flush, so assertions that refresh() a row are
    checking what actually reached the database rather than what is still
    pending in the session. run() deliberately does not commit — the
    caller owns the transaction, matching every other job in app/jobs."""
    deletion_job.run(session)
    session.flush()


def _latest_run(session):
    return session.execute(
        select(IngestionRun)
        .where(IngestionRun.source == "reddit_public_deletion_check")
        .order_by(IngestionRun.started_at.desc())
    ).scalars().first()


# --- the safety property: rate limiting must never scrub ---


def test_a_rate_limited_check_does_not_scrub(sqlite_session, monkeypatch):
    """THE test. A 429 means "we could not look", not "it is gone", and a
    scrub is irreversible."""
    row = _add_row(sqlite_session)

    def rate_limited(url):
        raise RetryExhaustedError("failed after 4 retries: last status 429")

    _respond(monkeypatch, rate_limited)

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.text == "Dermatologist reco in BGC please"
    assert row.author == "someone"
    assert row.deleted_at is None


def test_a_rate_limited_row_is_not_counted_as_verified(sqlite_session, monkeypatch):
    """Counting an unreachable row as checked would make the ledger
    overstate how much of the 48-hour commitment is actually being met."""
    _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: (_ for _ in ()).throw(RetryExhaustedError("429")))

    _run(sqlite_session)

    run = _latest_run(sqlite_session)
    assert run.items_seen == 0
    assert run.status == RunStatus.PARTIAL
    assert "unverified" in run.error


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_no_server_error_status_is_read_as_a_deletion(sqlite_session, monkeypatch, status_code):
    row = _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: _Response(status_code))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None


def test_an_unparseable_two_hundred_is_not_read_as_a_deletion(sqlite_session, monkeypatch):
    """An error page or interstitial served with a 200. Not evidence."""
    row = _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: _Response(200, b"<html>try again later</html>"))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None


def test_the_pass_stops_after_consecutive_rate_limits(sqlite_session, monkeypatch):
    """Past a few 429s Reddit has made its position clear. Continuing only
    spends the remaining rows' turns without checking them; stopping
    leaves them at the front of the next pass."""
    for i in range(10):
        _add_row(sqlite_session, external_id=f"t3_row{i}", age_days=10 - i)

    calls = {"n": 0}

    def count_and_fail(url):
        calls["n"] += 1
        raise RetryExhaustedError("failed after 4 retries: last status 429")

    _respond(monkeypatch, count_and_fail)

    _run(sqlite_session)

    assert calls["n"] == deletion_job.MAX_CONSECUTIVE_RATE_LIMITS
    assert "stopped after" in _latest_run(sqlite_session).error


# --- positive evidence: these DO scrub ---


def test_a_404_scrubs_the_row(sqlite_session, monkeypatch):
    """Reddit serves a valid empty feed with a 404 for an id it does not
    have — distinct from the zero-length 429 body. Verified live."""
    row = _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: _Response(404, EMPTY_FEED))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.text is None
    assert row.author is None
    assert row.raw_payload is None
    assert row.deleted_at is not None


def test_a_tombstoned_body_scrubs_the_row(sqlite_session, monkeypatch):
    """An object can outlive its content: the thread still resolves, but
    the body now reads [deleted]."""
    row = _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: _Response(200, TOMBSTONED_THREAD))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.text is None
    assert row.deleted_at is not None


def test_a_scrub_keeps_the_provenance_fields(sqlite_session, monkeypatch):
    """The commitment covers Reddit's content and author data, not our own
    record of having once observed the item — and keeping that record is
    what lets us prove the scrub happened. Same scope as
    reddit_deletion_job._scrub()."""
    row = _add_row(sqlite_session, venue="r/skincare_ph")
    _respond(monkeypatch, lambda url: _Response(404, EMPTY_FEED))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.url == "https://www.reddit.com/r/skincare_ph/comments/abc123/x/"
    assert row.venue == "r/skincare_ph"
    assert row.source == "reddit_public"


def test_a_live_thread_is_left_alone_and_counted(sqlite_session, monkeypatch):
    row = _add_row(sqlite_session)
    _respond(monkeypatch, lambda url: _Response(200, LIVE_THREAD))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None
    assert row.text == "Dermatologist reco in BGC please"
    run = _latest_run(sqlite_session)
    assert run.items_seen == 1
    assert run.items_ingested == 0
    # Nothing needing a scrub is the normal healthy outcome, and must not
    # be mislabelled PARTIAL by start_run()'s ingestion-shaped inference.
    assert run.status == RunStatus.SUCCESS


# --- comments are best-effort ---


def test_a_comment_absent_from_its_thread_is_not_scrubbed(sqlite_session, monkeypatch):
    """The feed truncates long threads, so absence does not prove removal.
    Scrubbing on it would silently destroy comments on popular posts."""
    row = _add_row(
        sqlite_session,
        external_id="t1_notinfeed",
        url="https://www.reddit.com/r/skincare_ph/comments/abc123/x/notinfeed/",
    )
    _respond(monkeypatch, lambda url: _Response(200, LIVE_THREAD))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None
    assert _latest_run(sqlite_session).status == RunStatus.PARTIAL


def test_a_comment_is_found_and_kept_when_present_in_its_thread(sqlite_session, monkeypatch):
    row = _add_row(
        sqlite_session,
        external_id="t1_def456",
        url="https://www.reddit.com/r/skincare_ph/comments/abc123/x/def456/",
    )
    _respond(monkeypatch, lambda url: _Response(200, LIVE_THREAD))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None
    assert _latest_run(sqlite_session).items_seen == 1


def test_a_comment_goes_when_its_parent_thread_is_gone(sqlite_session, monkeypatch):
    """Conclusive: no parent, no comment."""
    row = _add_row(
        sqlite_session,
        external_id="t1_def456",
        url="https://www.reddit.com/r/skincare_ph/comments/abc123/x/def456/",
    )
    _respond(monkeypatch, lambda url: _Response(404, EMPTY_FEED))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is not None


def test_a_comment_url_that_names_no_thread_is_unverified_not_deleted(sqlite_session, monkeypatch):
    row = _add_row(sqlite_session, external_id="t1_def456", url="https://example.com/nope")
    _respond(monkeypatch, lambda url: _Response(200, LIVE_THREAD))

    _run(sqlite_session)

    sqlite_session.refresh(row)
    assert row.deleted_at is None


# --- scope and fairness ---


def test_the_job_never_touches_rows_from_other_sources(sqlite_session, monkeypatch):
    """reddit_deletion_job owns source="reddit" via PRAW. The two must not
    both scrub the same rows on different evidence."""
    praw_row = _add_row(sqlite_session, source="reddit", external_id="t3_praw")
    news_row = _add_row(sqlite_session, source="news_google_rss", external_id="news-1")
    _respond(monkeypatch, lambda url: _Response(404, EMPTY_FEED))

    _run(sqlite_session)

    sqlite_session.refresh(praw_row)
    sqlite_session.refresh(news_row)
    assert praw_row.deleted_at is None
    assert news_row.deleted_at is None


def test_already_scrubbed_rows_are_not_rechecked(sqlite_session, monkeypatch):
    _add_row(sqlite_session, external_id="t3_gone", deleted_at=datetime.now(timezone.utc))
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return _Response(200, LIVE_THREAD)

    _respond(monkeypatch, counting)

    _run(sqlite_session)

    assert calls["n"] == 0


def test_least_recently_checked_rows_go_first(sqlite_session, monkeypatch):
    """Fairness across passes: BATCH_SIZE caps each run, so without this
    the same rows would starve the rest out of ever being verified."""
    monkeypatch.setattr(deletion_job, "BATCH_SIZE", 2)
    _add_row(sqlite_session, external_id="t3_newest", age_days=1)
    _add_row(sqlite_session, external_id="t3_oldest", age_days=30)
    _add_row(sqlite_session, external_id="t3_middle", age_days=15)

    seen = []
    _respond(monkeypatch, lambda url: (seen.append(url), _Response(200, LIVE_THREAD))[1])

    _run(sqlite_session)

    assert "oldest" in seen[0]
    assert "middle" in seen[1]
    assert len(seen) == 2


# --- the compliance question ---


def test_rows_overdue_for_verification_reports_only_stale_rows(sqlite_session):
    """This, not the job's own status, is what answers "are we keeping the
    48-hour promise" — a run can report SUCCESS having checked three rows
    while forty sit untouched for a week."""
    fresh = _add_row(sqlite_session, external_id="t3_fresh", age_days=0)
    stale = _add_row(sqlite_session, external_id="t3_stale", age_days=5)

    overdue = deletion_job.rows_overdue_for_verification(sqlite_session)

    ids = [row.external_id for row in overdue]
    assert stale.external_id in ids
    assert fresh.external_id not in ids


def test_already_scrubbed_rows_are_not_overdue(sqlite_session):
    """A scrubbed row has already had the commitment honoured; re-checking
    it forever would report a breach that does not exist."""
    _add_row(
        sqlite_session,
        external_id="t3_done",
        age_days=30,
        deleted_at=datetime.now(timezone.utc),
    )

    assert deletion_job.rows_overdue_for_verification(sqlite_session) == []


def test_job_is_registered_and_runs_more_often_than_the_commitment_window():
    """The gap this whole job exists to close was a module nothing called,
    so registration is worth a test. The cadence must also leave room for
    several passes inside 48h, since BATCH_SIZE caps each one."""
    from app.jobs import JOBS
    from app.scheduler import _cadence_for

    assert deletion_job in JOBS
    assert deletion_job.IS_DATA_SOURCE is False
    assert _cadence_for(deletion_job.SOURCE_NAME) < deletion_job.COMMITMENT_WINDOW_HOURS / 4
