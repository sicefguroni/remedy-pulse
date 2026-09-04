"""Tests for app/status_report.py (4.7) - build_report()'s formatting,
exercised against the real app.jobs.JOBS registry so this also pins that
the registered jobs are wired in and readable via get_source_freshness().
Assertions check that google_reviews/google_places_competitor are present
among the sections, not that they are the ONLY sections - the registry
is expected to grow as more adapters land (see app/jobs/__init__.py)."""

from app.jobs import JOBS
from app.repository import start_run
from app.status_report import build_report


def test_build_report_covers_every_registered_source_never_run(sqlite_session):
    report = build_report(sqlite_session)

    assert "Source:        google_reviews" in report
    assert "Source:        google_places_competitor" in report
    assert report.count("Last attempt:  never") == len(JOBS)
    assert report.count("Last status:   (no runs yet)") == len(JOBS)


def test_build_report_shows_success_after_a_clean_run(sqlite_session):
    with start_run(sqlite_session, source="google_reviews") as run:
        run.items_seen = 3
        run.items_ingested = 3
    sqlite_session.commit()

    report = build_report(sqlite_session)
    reviews_section = next(s for s in report.split("\n\n") if "google_reviews" in s)

    assert "Source:        google_reviews" in reviews_section
    assert "Last status:   success" in reviews_section
    assert "Last attempt:  never" not in reviews_section


def test_build_report_surfaces_the_error_message_on_a_failed_run(sqlite_session):
    try:
        with start_run(sqlite_session, source="google_places_competitor"):
            raise RuntimeError("403 from Places API")
    except RuntimeError:
        pass
    sqlite_session.commit()

    report = build_report(sqlite_session)

    assert "Last status:   error" in report
    assert "Last error:    403 from Places API" in report


def test_build_report_keeps_sources_independent(sqlite_session):
    with start_run(sqlite_session, source="google_reviews") as run:
        run.items_seen = 1
        run.items_ingested = 1
    sqlite_session.commit()

    report = build_report(sqlite_session)
    sections = report.split("\n\n")
    assert len(sections) == len(JOBS)

    reviews_section = next(s for s in sections if "google_reviews" in s)
    places_section = next(s for s in sections if "google_places_competitor" in s)
    assert "Last status:   success" in reviews_section
    assert "Last status:   (no runs yet)" in places_section
