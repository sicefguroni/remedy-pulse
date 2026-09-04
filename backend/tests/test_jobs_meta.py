"""Tests for app/jobs/meta_job.py — the ledger-granularity decision (one
IngestionRun per Meta capability, not one combined run; see the module's
own docstring for the full reasoning) and the Mention.source vs.
ledger-source split. Runs against in-memory SQLite via
conftest.sqlite_session, the same fixture every other repository/job test
in this suite uses. fetch_meta_mentions.py's own HTTP layer is never
exercised here — these tests monkeypatch the three fetch_*() functions
meta_job imports, so only the job-wrapper logic (ledger writes,
record_ingestion calls, dedup key selection) is under test.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.jobs import meta_job
from app.models import Mention, RunStatus
from app.repository import get_source_freshness

ALL_LEDGER_SOURCES = (
    meta_job.LEDGER_SOURCE_INSTAGRAM_COMMENTS,
    meta_job.LEDGER_SOURCE_INSTAGRAM_MENTIONS,
    meta_job.LEDGER_SOURCE_FACEBOOK_COMMENTS,
)


def _result(status, items=None, error=None):
    return {"status": status, "items": items or [], "error": error}


def _item(*, comment_id=None, media_id=None, author="masked", text="hello",
          url="https://instagram.com/p/x", venue="comment_on_own_post", date="2026-06-01T00:00:00Z"):
    raw = {}
    if comment_id:
        raw["comment_id"] = comment_id
    if media_id:
        raw["media_id"] = media_id
    return {
        "platform": "instagram",
        "author": author,
        "text": text,
        "sentiment": None,
        "date": date,
        "sourceUrl": url,
        "venue": venue,
        "raw": raw,
    }


def _set_token(monkeypatch):
    # run()'s own top-level check treats a missing META_ACCESS_TOKEN as
    # "skip all three capabilities" (see test_run_skips_all_three_when_
    # access_token_missing below) - every test that wants run() to
    # actually reach the three fetch_*() calls needs a token present, even
    # though the fetch_*() functions themselves are monkeypatched below
    # and never really use it.
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")


def _patch_all_not_configured(monkeypatch):
    monkeypatch.setattr(meta_job, "fetch_instagram_comments", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(meta_job, "fetch_instagram_mentions", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(meta_job, "fetch_facebook_comments", lambda *a, **k: _result("not_configured"))


# --- ledger granularity: not_configured gets no row at all ---


def test_run_creates_no_ledger_row_for_not_configured_capabilities(sqlite_session, monkeypatch):
    _set_token(monkeypatch)
    _patch_all_not_configured(monkeypatch)

    meta_job.run(sqlite_session)
    sqlite_session.commit()

    for source in ALL_LEDGER_SOURCES:
        freshness = get_source_freshness(sqlite_session, source)
        assert freshness.last_status is None
        assert freshness.last_attempt_at is None
        assert freshness.last_success_at is None

    assert sqlite_session.execute(select(Mention)).scalars().all() == []


def test_run_skips_all_three_when_access_token_missing(sqlite_session, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_BUSINESS_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("META_PAGE_ID", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("a fetch_*() function must not run with no access token")

    monkeypatch.setattr(meta_job, "fetch_instagram_comments", fail_if_called)
    monkeypatch.setattr(meta_job, "fetch_instagram_mentions", fail_if_called)
    monkeypatch.setattr(meta_job, "fetch_facebook_comments", fail_if_called)

    meta_job.run(sqlite_session)
    sqlite_session.commit()

    for source in ALL_LEDGER_SOURCES:
        assert get_source_freshness(sqlite_session, source).last_status is None


# --- ledger granularity: three independent statuses in one run() call ---


def test_run_records_independent_ledger_rows_per_capability(sqlite_session, monkeypatch):
    _set_token(monkeypatch)
    monkeypatch.setattr(
        meta_job, "fetch_instagram_comments",
        lambda *a, **k: _result("ok", items=[_item(comment_id="c1")]),
    )
    monkeypatch.setattr(
        meta_job, "fetch_instagram_mentions",
        lambda *a, **k: _result("access_denied", error="missing scope"),
    )
    monkeypatch.setattr(
        meta_job, "fetch_facebook_comments",
        lambda *a, **k: _result("error", error="retries exhausted"),
    )

    meta_job.run(sqlite_session)
    sqlite_session.commit()

    ig_comments_fresh = get_source_freshness(sqlite_session, meta_job.LEDGER_SOURCE_INSTAGRAM_COMMENTS)
    assert ig_comments_fresh.last_status == RunStatus.SUCCESS
    assert ig_comments_fresh.last_success_at is not None
    assert ig_comments_fresh.last_error is None

    ig_mentions_fresh = get_source_freshness(sqlite_session, meta_job.LEDGER_SOURCE_INSTAGRAM_MENTIONS)
    assert ig_mentions_fresh.last_status == RunStatus.ACCESS_DENIED
    assert ig_mentions_fresh.last_error == "missing scope"
    assert ig_mentions_fresh.last_success_at is None

    fb_fresh = get_source_freshness(sqlite_session, meta_job.LEDGER_SOURCE_FACEBOOK_COMMENTS)
    assert fb_fresh.last_status == RunStatus.ERROR
    assert fb_fresh.last_error == "retries exhausted"
    assert fb_fresh.last_success_at is None

    # One capability being denied/erroring must never block the healthy
    # one's items from being ingested.
    mentions = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(mentions) == 1
    assert mentions[0].source == meta_job.MENTION_SOURCE_INSTAGRAM
    assert mentions[0].external_id == "c1"
    # published_at must land as a real datetime, not the raw ISO string -
    # Mention.published_at is a DateTime column. SQLite (unlike Postgres)
    # doesn't round-trip tzinfo, so compare naive - same pattern
    # test_jobs_news.py already uses for the identical reason.
    assert mentions[0].published_at.replace(tzinfo=None) == datetime(2026, 6, 1)


# --- Mention.source split: both IG capabilities share "instagram" ---


def test_instagram_mentions_capability_also_writes_mention_source_instagram(sqlite_session, monkeypatch):
    _set_token(monkeypatch)
    monkeypatch.setattr(meta_job, "fetch_instagram_comments", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(
        meta_job, "fetch_instagram_mentions",
        lambda *a, **k: _result("ok", items=[_item(media_id="m1", venue="tagged_mention")]),
    )
    monkeypatch.setattr(meta_job, "fetch_facebook_comments", lambda *a, **k: _result("not_configured"))

    meta_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.source == meta_job.MENTION_SOURCE_INSTAGRAM
    assert mention.external_id == "media:m1"


def test_facebook_capability_writes_mention_source_facebook(sqlite_session, monkeypatch):
    _set_token(monkeypatch)
    monkeypatch.setattr(meta_job, "fetch_instagram_comments", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(meta_job, "fetch_instagram_mentions", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(
        meta_job, "fetch_facebook_comments",
        lambda *a, **k: _result("ok", items=[_item(comment_id="fc1", author="Maria S.")]),
    )

    meta_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.source == meta_job.MENTION_SOURCE_FACEBOOK
    assert mention.author == "Maria S."


# --- idempotency on rerun ---


def test_run_is_idempotent_on_rerun_same_comment_id(sqlite_session, monkeypatch):
    _set_token(monkeypatch)
    item = _item(comment_id="c1")
    monkeypatch.setattr(meta_job, "fetch_instagram_comments", lambda *a, **k: _result("ok", items=[item]))
    monkeypatch.setattr(meta_job, "fetch_instagram_mentions", lambda *a, **k: _result("not_configured"))
    monkeypatch.setattr(meta_job, "fetch_facebook_comments", lambda *a, **k: _result("not_configured"))

    meta_job.run(sqlite_session)
    sqlite_session.commit()
    meta_job.run(sqlite_session)
    sqlite_session.commit()

    mentions = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(mentions) == 1

    # Two runs -> two ledger rows for the one configured capability, both
    # successful (a re-ingest is a legitimate repeat run, not a failure).
    freshness = get_source_freshness(sqlite_session, meta_job.LEDGER_SOURCE_INSTAGRAM_COMMENTS)
    assert freshness.last_status == RunStatus.SUCCESS


# --- _external_id ---


def test_external_id_prefers_comment_id_over_media_id():
    assert meta_job._external_id({"raw": {"comment_id": "999", "media_id": "111"}}) == "999"


def test_external_id_falls_back_to_prefixed_media_id_when_no_comment_id():
    assert meta_job._external_id({"raw": {"media_id": "123"}}) == "media:123"


def test_external_id_last_resort_fallback_when_raw_has_neither_id():
    result = meta_job._external_id({"raw": {}, "sourceUrl": "https://x.com/a", "date": "2026-06-01"})
    assert result == "https://x.com/a:2026-06-01"


# --- _parse_published_at ---


def test_parse_published_at_handles_meta_style_offset_with_no_colon():
    # Graph API's actual format - no colon in the offset.
    assert meta_job._parse_published_at("2026-06-02T00:00:00+0000") == datetime(
        2026, 6, 2, tzinfo=timezone.utc
    )


def test_parse_published_at_handles_z_suffix_too():
    assert meta_job._parse_published_at("2026-06-02T00:00:00Z") == datetime(2026, 6, 2, tzinfo=timezone.utc)


def test_parse_published_at_returns_none_for_missing_or_malformed():
    assert meta_job._parse_published_at(None) is None
    assert meta_job._parse_published_at("") is None
    assert meta_job._parse_published_at("not-a-date") is None


# --- module contract deviation (decision 3) ---


def test_meta_job_is_not_registered_in_jobs_yet():
    # See the module docstring's decision 3: this module deliberately does
    # not expose the single `SOURCE_NAME` app/jobs/__init__.py's JOBS
    # registry expects, and is not auto-registered - a separate
    # reconciliation pass decides how a 3-ledger-source job fits that
    # single-source-per-module contract. Pin both facts so a future,
    # unrelated edit to app/jobs/__init__.py doesn't silently break this
    # module's assumptions without a test noticing.
    from app.jobs import JOBS

    assert meta_job not in JOBS
    assert not hasattr(meta_job, "SOURCE_NAME")
    assert meta_job.SOURCE_NAMES == (
        meta_job.LEDGER_SOURCE_INSTAGRAM_COMMENTS,
        meta_job.LEDGER_SOURCE_INSTAGRAM_MENTIONS,
        meta_job.LEDGER_SOURCE_FACEBOOK_COMMENTS,
    )
