"""Tests for app/jobs/news_job.py — the GNews ingestion job (4.5).

Mocks the HTTP layer by monkeypatching `fetch_articles_for_term` as it is
bound in `app.jobs.news_job`'s own namespace (that module imports the name
directly, `from fetch_news_articles import fetch_articles_for_term`), the
same "don't hit the real API" approach test_http_utils.py uses for
http_utils.get_with_retry — nothing in this file makes a real network
call.
"""

from datetime import datetime

from sqlalchemy import select

import app.jobs.news_job as news_job
from app.models import IngestionRun, Mention, RunStatus
from http_utils import RetryExhaustedError


def _raw_article(outlet="Rappler", url="https://rappler.com/a1", title="Remedy BGC clinic review"):
    return {
        "title": title,
        "description": "A look at the new Rejuran treatment.",
        "url": url,
        "publishedAt": "2026-06-29T09:03:00Z",
        "source": {"name": outlet, "url": "https://rappler.com"},
    }


def test_run_success_ingests_articles_and_records_success_run(sqlite_session, monkeypatch):
    articles_by_term = {
        '"Remedy Skin Clinic"': [_raw_article(url="https://rappler.com/a1")],
        '"Remedy BGC"': [_raw_article(url="https://philstar.com/a2", outlet="Philippine Star")],
        '"Remedy Vertis North"': [],
        '"Skin Bar by Remedy"': [],
    }

    def fake_fetch(term):
        return articles_by_term.get(term, [])

    monkeypatch.setattr(news_job, "fetch_articles_for_term", fake_fetch)
    monkeypatch.setattr(news_job.fetch_news_articles, "API_KEY", "fake-key")

    news_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.source == news_job.SOURCE_NAME
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 2

    mentions = sqlite_session.execute(select(Mention).order_by(Mention.url)).scalars().all()
    assert len(mentions) == 2

    rappler = next(m for m in mentions if m.url == "https://rappler.com/a1")
    assert rappler.source == news_job.SOURCE_NAME
    assert rappler.kind == "article"
    assert rappler.external_id == "https://rappler.com/a1"
    assert rappler.headline == "Remedy BGC clinic review"
    assert rappler.text == "A look at the new Rejuran treatment."
    assert rappler.venue == "Rappler"
    assert rappler.tier == "National News"
    assert rappler.sentiment is None
    # SQLite doesn't round-trip tzinfo on a DateTime(timezone=True) column
    # (Postgres does — see test_app_repository_postgres.py), so compare the
    # naive value here; the job itself builds a tz-aware datetime either way.
    assert rappler.published_at.replace(tzinfo=None) == datetime(2026, 6, 29, 9, 3, 0)
    assert rappler.raw_payload["url"] == "https://rappler.com/a1"

    philstar = next(m for m in mentions if m.url == "https://philstar.com/a2")
    assert philstar.tier == "National News"


def test_run_missing_api_key_marks_error_without_crashing(sqlite_session, monkeypatch):
    monkeypatch.setattr(news_job.fetch_news_articles, "API_KEY", None)

    def fail_if_called(term):
        raise AssertionError("fetch_articles_for_term should not be called without an API key")

    monkeypatch.setattr(news_job, "fetch_articles_for_term", fail_if_called)

    news_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ERROR
    assert "GNEWS_API_KEY" in run_row.error

    assert sqlite_session.execute(select(Mention)).scalars().all() == []


def test_run_retry_exhausted_on_one_term_does_not_abort_the_others(sqlite_session, monkeypatch):
    articles_by_term = {
        '"Remedy Skin Clinic"': [_raw_article(url="https://rappler.com/a1")],
    }

    def fake_fetch(term):
        if term == '"Remedy BGC"':
            raise RetryExhaustedError("GET failed after retries: last status 503")
        return articles_by_term.get(term, [])

    monkeypatch.setattr(news_job, "fetch_articles_for_term", fake_fetch)
    monkeypatch.setattr(news_job.fetch_news_articles, "API_KEY", "fake-key")

    news_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 1

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.url == "https://rappler.com/a1"


def test_run_article_with_no_url_is_seen_but_not_ingested(sqlite_session, monkeypatch):
    no_url_article = _raw_article()
    no_url_article["url"] = None

    def fake_fetch(term):
        if term == '"Remedy Skin Clinic"':
            return [no_url_article]
        return []

    monkeypatch.setattr(news_job, "fetch_articles_for_term", fake_fetch)
    monkeypatch.setattr(news_job.fetch_news_articles, "API_KEY", "fake-key")

    news_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 0
    assert run_row.status == RunStatus.PARTIAL
    assert sqlite_session.execute(select(Mention)).scalars().all() == []
