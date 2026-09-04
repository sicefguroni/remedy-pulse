"""Tests for app/scheduler.py (4.6) - the per-source cadence check and the
run_due_jobs() runner. Uses fake job objects (a SimpleNamespace exposing
SOURCE_NAME/run(), the same shape a real app/jobs/<name>_job.py module
has) passed via run_due_jobs()'s `jobs` override, so this doesn't depend
on the real google_reviews_job/google_places_job actually calling out to
Google."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.repository import start_run
from app.scheduler import is_due, run_due_jobs


def _fake_job(source_name, calls):
    def _run(session):
        calls.append(source_name)
        with start_run(session, source=source_name) as run:
            run.items_seen = 1
            run.items_ingested = 1

    return SimpleNamespace(SOURCE_NAME=source_name, run=_run)


# --- is_due ---


def test_is_due_true_when_no_prior_run(sqlite_session):
    assert is_due(sqlite_session, "never_run_source") is True


def test_is_due_false_within_cadence_window(sqlite_session):
    with start_run(sqlite_session, source="google_reviews"):
        pass
    sqlite_session.commit()

    now = datetime.now(timezone.utc)
    assert is_due(sqlite_session, "google_reviews", now=now) is False


def test_is_due_true_after_cadence_window_elapses(sqlite_session):
    with start_run(sqlite_session, source="google_reviews"):
        pass
    sqlite_session.commit()

    far_future = datetime.now(timezone.utc) + timedelta(hours=48)
    assert is_due(sqlite_session, "google_reviews", now=far_future) is True


def test_is_due_respects_a_custom_cadence_override(sqlite_session, monkeypatch):
    import app.scheduler as scheduler

    monkeypatch.setitem(scheduler.CADENCE_HOURS, "fast_source", 1.0)
    with start_run(sqlite_session, source="fast_source"):
        pass
    sqlite_session.commit()

    just_past_default = datetime.now(timezone.utc) + timedelta(hours=2)
    assert is_due(sqlite_session, "fast_source", now=just_past_default) is True


# --- run_due_jobs ---


def test_run_due_jobs_runs_a_source_with_no_prior_attempt(sqlite_session):
    calls = []
    jobs = [_fake_job("source_a", calls)]

    ran = run_due_jobs(sqlite_session, jobs=jobs)

    assert ran == ["source_a"]
    assert calls == ["source_a"]


def test_run_due_jobs_skips_a_source_still_within_cadence(sqlite_session):
    calls = []
    jobs = [_fake_job("source_a", calls)]

    run_due_jobs(sqlite_session, jobs=jobs)  # first pass: runs (no prior attempt)
    calls.clear()

    ran = run_due_jobs(sqlite_session, jobs=jobs, now=datetime.now(timezone.utc))
    assert ran == []
    assert calls == []


def test_run_due_jobs_reruns_once_cadence_elapses(sqlite_session):
    calls = []
    jobs = [_fake_job("source_a", calls)]

    run_due_jobs(sqlite_session, jobs=jobs)
    calls.clear()

    far_future = datetime.now(timezone.utc) + timedelta(hours=48)
    ran = run_due_jobs(sqlite_session, jobs=jobs, now=far_future)
    assert ran == ["source_a"]
    assert calls == ["source_a"]


def test_run_due_jobs_handles_multiple_independent_sources(sqlite_session):
    calls = []
    jobs = [_fake_job("source_a", calls), _fake_job("source_b", calls)]

    ran = run_due_jobs(sqlite_session, jobs=jobs)

    assert set(ran) == {"source_a", "source_b"}
    assert set(calls) == {"source_a", "source_b"}


def test_run_due_jobs_defaults_to_the_real_jobs_registry():
    # No `jobs=` override - exercises the actual app.jobs.JOBS wiring, so a
    # broken import or a bad JOBS entry surfaces here rather than only in
    # production. Asserts every job has the two attributes run_due_jobs()
    # actually needs (SOURCE_NAME, callable run) and that the two Google
    # jobs from this test file's original scope are present - deliberately
    # NOT an exact-membership check against the full registry, since
    # app/jobs/__init__.py's own docstring promises registering a new job
    # is a one-line addition there, not something that should also require
    # editing this test every time.
    import app.scheduler as scheduler
    from app.jobs import JOBS

    assert scheduler.JOBS is JOBS
    assert len(JOBS) >= 2
    for job in JOBS:
        assert isinstance(job.SOURCE_NAME, str) and job.SOURCE_NAME
        assert callable(job.run)
    source_names = {job.SOURCE_NAME for job in JOBS}
    assert len(source_names) == len(JOBS), "every job must have a distinct SOURCE_NAME"
    assert {"google_reviews", "google_places_competitor"} <= source_names
