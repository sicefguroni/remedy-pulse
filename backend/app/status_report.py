"""status_report.py — per-source freshness CLI (4.7).

STOPGAP NOTICE: this script is NOT the real fix for the other half of 0.2
- "a failed source must be visible to the marketing team, not only to
whoever reads stderr." Marketing does not run CLI scripts and read their
stdout; the actual fix is a UI surface (a per-source "last synced"/"sync
failed" indicator, per the mockup's own P0-11 note), and that needs
Phase 7's API / data-driven refactor to exist before it can be built. This
script is what's buildable right now, ahead of that dependency - it makes
every source's freshness/failure state visible to *someone* technical
without them having to query the database directly, which is strictly
better than only-in-the-logs, but it is not 4.7 done.

Usage:
    python -m app.status_report
"""

from __future__ import annotations

from app.db import session_scope
from app.jobs import JOBS
from app.repository import SourceFreshness, get_source_freshness


def _format_freshness(freshness: SourceFreshness) -> str:
    def _fmt(dt):
        return dt.isoformat() if dt else "never"

    # last_status may come back as a RunStatus enum member (when the row is
    # still identity-mapped in this session, e.g. right after start_run) or
    # a plain str (a fresh row loaded straight from the DB, since
    # IngestionRun.status is a plain String column, not a SQLAlchemy Enum
    # type) - RunStatus.__str__ prints "RunStatus.ERROR", not "error", so
    # normalize via .value (falling back to the value itself for a plain
    # str, which has no .value attribute) rather than let that leak into
    # the report.
    status = getattr(freshness.last_status, "value", freshness.last_status)

    lines = [
        f"Source:        {freshness.source}",
        f"Last attempt:  {_fmt(freshness.last_attempt_at)}",
        f"Last success:  {_fmt(freshness.last_success_at)}",
        f"Last status:   {status or '(no runs yet)'}",
    ]
    if freshness.last_error:
        lines.append(f"Last error:    {freshness.last_error}")
    return "\n".join(lines)


def build_report(session) -> str:
    """Returns the full human-readable report as one string, one section
    per SOURCE_NAME in app.jobs.JOBS - split out from main() so tests can
    assert on the text directly instead of capturing stdout."""
    sections = [_format_freshness(get_source_freshness(session, job.SOURCE_NAME)) for job in JOBS]
    return "\n\n".join(sections)


def main() -> None:
    with session_scope() as session:
        report = build_report(session)
    print(report)


if __name__ == "__main__":
    main()
