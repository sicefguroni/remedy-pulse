"""Tests for app/jobs/reddit_job.py — the Reddit ingestion job (4.3).

Mocks fetch_all_mentions/get_reddit_client as they are bound in
app.jobs.reddit_job's own namespace (that module imports them directly,
`from fetch_reddit_mentions import fetch_all_mentions, get_reddit_client`),
the same approach test_jobs_news.py uses for fetch_articles_for_term.
Nothing here makes a real network/PRAW call, and praw itself never needs
to be installed to run these.
"""

from datetime import datetime, timezone

from sqlalchemy import select

import app.jobs.reddit_job as reddit_job
from app.models import IngestionRun, Mention, RunStatus


def _mention_row(
    fullname="t3_abc123",
    subreddit="PhilippinesSkincare",
    author="skin_a1b2c3d4",
    text="Went last week, results were great.",
    published_at="2026-07-01T09:00:00+00:00",
    source_url="https://www.reddit.com/r/PhilippinesSkincare/comments/abc123/",
):
    return {
        "platform": "Reddit",
        "source": "reddit",
        "redditKind": "submission",
        "fullname": fullname,
        "subreddit": subreddit,
        "matchedTerm": "Remedy BGC",
        "author": author,
        "title": "Anyone tried Remedy BGC?",
        "text": text,
        "publishedAt": published_at,
        "sourceUrl": source_url,
        "sentiment": None,
        "status": "ok",
    }


def _set_credentials_present(monkeypatch):
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "CLIENT_ID", "cid")
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "CLIENT_SECRET", "csecret")
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "REDDIT_PASSWORD", "pw")


def test_run_success_ingests_mentions_and_records_success_run(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(reddit_job, "get_reddit_client", lambda: object())
    monkeypatch.setattr(
        reddit_job,
        "fetch_all_mentions",
        lambda reddit: [
            _mention_row(fullname="t3_a1", source_url="https://www.reddit.com/r/PhilippinesSkincare/comments/a1/"),
            _mention_row(fullname="t3_a2", source_url="https://www.reddit.com/r/PhilippinesSkincare/comments/a2/"),
        ],
    )

    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.source == reddit_job.SOURCE_NAME
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 2

    mentions = sqlite_session.execute(select(Mention).order_by(Mention.external_id)).scalars().all()
    assert len(mentions) == 2

    a1 = next(m for m in mentions if m.external_id == "t3_a1")
    assert a1.source == "reddit"
    assert a1.kind == "mention"
    assert a1.venue == "PhilippinesSkincare"
    assert a1.author == "skin_a1b2c3d4"
    assert a1.text == "Went last week, results were great."
    assert a1.url == "https://www.reddit.com/r/PhilippinesSkincare/comments/a1/"
    assert a1.sentiment is None
    assert a1.published_at.replace(tzinfo=None) == datetime(2026, 7, 1, 9, 0, 0)
    assert a1.raw_payload["fullname"] == "t3_a1"
    assert a1.raw_payload["sourceUrl"] == "https://www.reddit.com/r/PhilippinesSkincare/comments/a1/"


def test_run_missing_credentials_marks_error_without_crashing_or_calling_praw(sqlite_session, monkeypatch):
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "CLIENT_ID", None)
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "CLIENT_SECRET", None)
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "REDDIT_USERNAME", None)
    monkeypatch.setattr(reddit_job.fetch_reddit_mentions, "REDDIT_PASSWORD", None)

    def fail_if_called():
        raise AssertionError("get_reddit_client should not be called without credentials")

    monkeypatch.setattr(reddit_job, "get_reddit_client", fail_if_called)

    def fail_if_called_fetch(reddit):
        raise AssertionError("fetch_all_mentions should not be called without credentials")

    monkeypatch.setattr(reddit_job, "fetch_all_mentions", fail_if_called_fetch)

    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ERROR
    assert "REDDIT_CLIENT_ID" in run_row.error

    assert sqlite_session.execute(select(Mention)).scalars().all() == []


def test_run_item_with_no_fullname_is_seen_but_not_ingested(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(reddit_job, "get_reddit_client", lambda: object())

    no_fullname_row = _mention_row()
    no_fullname_row["fullname"] = None

    monkeypatch.setattr(reddit_job, "fetch_all_mentions", lambda reddit: [no_fullname_row])

    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 0
    assert run_row.status == RunStatus.PARTIAL
    assert sqlite_session.execute(select(Mention)).scalars().all() == []


def test_run_rerun_is_idempotent_via_upsert(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(reddit_job, "get_reddit_client", lambda: object())
    monkeypatch.setattr(reddit_job, "fetch_all_mentions", lambda reddit: [_mention_row(fullname="t3_dup")])

    reddit_job.run(sqlite_session)
    sqlite_session.commit()
    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    mentions = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(mentions) == 1

    runs = sqlite_session.execute(select(IngestionRun)).scalars().all()
    assert len(runs) == 2
    assert all(run.status == RunStatus.SUCCESS for run in runs)


def test_run_author_already_masked_upstream_is_stored_as_is(sqlite_session, monkeypatch):
    # reddit_job.py itself does no masking - fetch_reddit_mentions.
    # normalize_submission() already masked the author before this job
    # ever sees the row. Pin that the job stores the value verbatim
    # rather than re-processing (or re-exposing) it.
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(reddit_job, "get_reddit_client", lambda: object())
    monkeypatch.setattr(
        reddit_job, "fetch_all_mentions", lambda reddit: [_mention_row(author="pre_masked1234")]
    )

    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.author == "pre_masked1234"


def test_run_no_published_at_stores_null(sqlite_session, monkeypatch):
    _set_credentials_present(monkeypatch)
    monkeypatch.setattr(reddit_job, "get_reddit_client", lambda: object())
    monkeypatch.setattr(
        reddit_job, "fetch_all_mentions", lambda reddit: [_mention_row(published_at=None)]
    )

    reddit_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.published_at is None


def test_parse_published_at_returns_none_for_malformed_value():
    assert reddit_job._parse_published_at("not-a-date") is None
    assert reddit_job._parse_published_at(None) is None


def test_parse_published_at_parses_offset_aware_iso_string():
    parsed = reddit_job._parse_published_at("2026-07-01T09:00:00+00:00")
    assert parsed == datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
