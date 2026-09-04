"""Tests for app/jobs/reddit_deletion_job.py — the Reddit 48-hour
deletion-propagation worker (5.1).

No real PRAW/network calls: a fake reddit client exposes `.submission(id=)`
/ `.comment(id=)` returning canned objects, or raising, to simulate
"still there" vs "404'd"/"tombstoned". praw itself never needs to be
installed to run these — get_reddit_client() is monkeypatched directly,
the same approach test_jobs_reddit.py uses.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

import app.jobs.reddit_deletion_job as deletion_job
from app.models import IngestionRun, Mention, RunStatus
from app.repository import upsert_mention


def _seed_mention(session, fullname, *, updated_at=None, **extra):
    """Seeds one stored Reddit Mention row keyed on `fullname` (the full
    "t3_"/"t1_"-prefixed Reddit ID, passed in verbatim by every call site
    below) - mirrors exactly what reddit_job.py itself would have written
    (external_id == fullname, raw_payload carrying it too)."""
    fields = dict(
        source="reddit",
        kind="mention",
        external_id=fullname,
        author="skin_a1b2c3d4",
        text="Went last week, results were great.",
        venue="PhilippinesSkincare",
        url="https://www.reddit.com/r/PhilippinesSkincare/comments/x/",
        raw_payload={"fullname": fullname, "sourceUrl": "https://www.reddit.com/r/PhilippinesSkincare/comments/x/"},
        **extra,
    )
    upsert_mention(session, **fields)
    session.commit()
    if updated_at is not None:
        mention = session.execute(
            select(Mention).where(Mention.external_id == fields["external_id"])
        ).scalar_one()
        mention.updated_at = updated_at
        session.commit()
    return fields["external_id"]


def _set_credentials_present(monkeypatch):
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "CLIENT_ID", "cid")
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "CLIENT_SECRET", "csecret")
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "REDDIT_PASSWORD", "pw")


class _FakeReddit:
    def __init__(self, submissions=None, comments=None, raise_for=()):
        self._submissions = submissions or {}
        self._comments = comments or {}
        self._raise_for = set(raise_for)

    def submission(self, id):
        if id in self._raise_for:
            raise RuntimeError(f"404: submission {id} not found")
        return self._submissions[id]

    def comment(self, id):
        if id in self._raise_for:
            raise RuntimeError(f"404: comment {id} not found")
        return self._comments[id]


def test_run_scrubs_a_submission_that_now_404s(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t3_gone")
    reddit = _FakeReddit(raise_for={"gone"})
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    assert mention.deleted_at is not None
    assert mention.text is None
    assert mention.author is None
    assert mention.raw_payload is None

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.source == deletion_job.SOURCE_NAME
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 1
    assert run_row.status == RunStatus.SUCCESS


def test_run_scrubs_a_submission_whose_body_is_now_removed(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t3_removed")
    reddit = _FakeReddit(submissions={"removed": SimpleNamespace(selftext="[removed]")})
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    assert mention.deleted_at is not None
    assert mention.text is None


def test_run_leaves_a_still_present_submission_untouched(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t3_stillhere")
    reddit = _FakeReddit(submissions={"stillhere": SimpleNamespace(selftext="Still here, great service.")})
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    assert mention.deleted_at is None
    assert mention.text == "Went last week, results were great."
    assert mention.author == "skin_a1b2c3d4"
    assert mention.raw_payload is not None

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 0
    assert run_row.status == RunStatus.SUCCESS


def test_run_handles_comment_kind_via_t1_prefix(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t1_c1")
    reddit = _FakeReddit(raise_for={"c1"})
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    assert mention.deleted_at is not None


def test_run_mixed_batch_scrubs_only_the_deleted_one(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    gone_fullname = _seed_mention(sqlite_session, "t3_gone2")
    present_fullname = _seed_mention(sqlite_session, "t3_present2")
    reddit = _FakeReddit(
        submissions={"present2": SimpleNamespace(selftext="Still here.")},
        raise_for={"gone2"},
    )
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    gone = sqlite_session.execute(select(Mention).where(Mention.external_id == gone_fullname)).scalar_one()
    present = sqlite_session.execute(select(Mention).where(Mention.external_id == present_fullname)).scalar_one()
    assert gone.deleted_at is not None
    assert present.deleted_at is None

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 1
    # SUCCESS, not PARTIAL: for this job items_ingested < items_seen just
    # means "not every checked row turned out to be deleted," the normal
    # mixed-batch case - not a run failure. See run()'s own comment on
    # why it overrides start_run()'s default PARTIAL inference.
    assert run_row.status == RunStatus.SUCCESS


def test_run_already_deleted_rows_are_never_rechecked(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t3_alreadygone")
    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    mention.deleted_at = datetime.now(timezone.utc)
    sqlite_session.commit()

    def fail_if_called(id):
        raise AssertionError("should never re-check an already-deleted row")

    reddit = SimpleNamespace(submission=fail_if_called, comment=fail_if_called)
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 0


def test_run_batch_size_caps_rows_rechecked_per_run(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(deletion_job, "BATCH_SIZE", 3)

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fullnames = []
    for i in range(5):
        fullname = _seed_mention(
            sqlite_session, f"t3_batch{i}", updated_at=base_time + timedelta(minutes=i)
        )
        fullnames.append(fullname)

    checked_ids = []

    def track_submission(id):
        checked_ids.append(id)
        return SimpleNamespace(selftext="Still here.")

    reddit = SimpleNamespace(submission=track_submission, comment=lambda id: None)
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    assert len(checked_ids) == 3

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 3


def test_run_processes_oldest_updated_at_first(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(deletion_job, "BATCH_SIZE", 2)

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Seed newest first, but updated_at ordering should still pick the two
    # oldest-updated rows regardless of insertion order.
    _seed_mention(sqlite_session, "t3_newest", updated_at=base_time + timedelta(days=2))
    _seed_mention(sqlite_session, "t3_oldest", updated_at=base_time)
    _seed_mention(sqlite_session, "t3_middle", updated_at=base_time + timedelta(days=1))

    checked_ids = []

    def track_submission(id):
        checked_ids.append(id)
        return SimpleNamespace(selftext="Still here.")

    reddit = SimpleNamespace(submission=track_submission, comment=lambda id: None)
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    assert checked_ids == ["oldest", "middle"]


def test_run_missing_credentials_marks_error_without_crashing_or_calling_praw(sqlite_session, monkeypatch):
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "CLIENT_ID", None)
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "CLIENT_SECRET", None)
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "REDDIT_USERNAME", None)
    monkeypatch.setattr(deletion_job.fetch_reddit_mentions, "REDDIT_PASSWORD", None)
    _seed_mention(sqlite_session, "t3_untouched")

    def fail_if_called():
        raise AssertionError("get_reddit_client should not be called without credentials")

    monkeypatch.setattr(deletion_job, "get_reddit_client", fail_if_called)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ERROR
    assert "REDDIT_CLIENT_ID" in run_row.error

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.deleted_at is None


def test_run_row_with_unrecognized_external_id_prefix_is_skipped(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    fullname = _seed_mention(sqlite_session, "t9_weird")

    def fail_if_called(id):
        raise AssertionError("should not attempt a PRAW call for an unrecognized fullname prefix")

    reddit = SimpleNamespace(submission=fail_if_called, comment=fail_if_called)
    monkeypatch.setattr(deletion_job, "get_reddit_client", lambda: reddit)

    deletion_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention).where(Mention.external_id == fullname)).scalar_one()
    assert mention.deleted_at is None

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 0


def test_select_batch_only_returns_reddit_source_rows(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    _seed_mention(sqlite_session, "t3_isreddit")
    upsert_mention(
        sqlite_session, source="google_reviews", kind="review", external_id="rev-1", text="hi"
    )
    sqlite_session.commit()

    batch = deletion_job._select_batch(sqlite_session)
    assert len(batch) == 1
    assert batch[0].source == "reddit"


def test_reddit_id_strips_fullname_prefix():
    assert deletion_job._reddit_id("t3_abc123") == "abc123"
    assert deletion_job._reddit_id("t1_xyz789") == "xyz789"
