"""app/api/routes/overview.py — GET /api/overview.

Clarity Index: ports docs/decisions/01-clarity-index-formula.md's exact
formula (also implemented client-side today as
index.html's computeClarityIndex()) to Python against real
aggregate data, per docs/api-contract.md's own instruction not to invent
a second formula.

AI summary: `aiSummaryText` is `null` (updated 2026-09-11 - "remove mock
data from logged-in sessions" pass). This endpoint previously returned a
canned/templated string (one of the mockup's own 3 sample summaries,
verbatim) on every call, real data or not - docs/api-contract.md
originally blessed that explicitly ("this endpoint may keep returning a
canned/templated string for now"), but every caller of this endpoint is,
by construction, an authenticated session (get_current_user is a required
dependency) - there is no server-side "demo" mode to gate a fake summary
behind, so that canned string was presented as if it summarized the
caller's own real data on every single real login. Wiring this to a real
LLM is still explicitly out of scope (see
docs/decisions/07-reddit-c4-no-resale-control.md's note that P1-1 going
live needs its own compliance gate first) - the honest interim state is
null, not an invented paragraph. The mockup's own demo-mode-only sample
summaries (SAMPLE_DATA.aiSummaryVariants, cycled by regenerateSummary())
are unaffected - those never call this endpoint at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import ApiError, get_current_user, get_db
from app.jobs import JOBS
from app.models import User
from app.repository import OverviewStats, get_overview_stats, get_overview_trend, get_source_freshness

router = APIRouter(tags=["overview"])

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _parse_period(
    period: str, from_param: str | None, to_param: str | None
) -> tuple[datetime, datetime, datetime, datetime]:
    """Returns (period_from, period_to, prior_from, prior_to). The prior
    window is always the same length as the current one, immediately
    preceding it - what both totalMentions.priorPeriodValue and the
    Clarity Index's volume-trend term compare against."""
    now = datetime.now(timezone.utc)
    if period == "custom":
        if not from_param or not to_param:
            raise ApiError(400, {"error": "custom period requires both 'from' and 'to'"})
        try:
            period_from = _ensure_utc(datetime.fromisoformat(from_param))
            period_to = _ensure_utc(datetime.fromisoformat(to_param))
        except ValueError:
            raise ApiError(400, {"error": "'from'/'to' must be ISO-8601 dates"})
    elif period in _PERIOD_DAYS:
        period_to = now
        period_from = now - timedelta(days=_PERIOD_DAYS[period])
    else:
        raise ApiError(400, {"error": f"unknown period {period!r}"})
    span = period_to - period_from
    prior_to = period_from
    prior_from = period_from - span
    return period_from, period_to, prior_from, prior_to


def _clarity_and_net_sentiment(stats: OverviewStats) -> tuple[int, int]:
    """Ports computeClarityIndex() (docs/decisions/01-clarity-index-formula.md)
    to real aggregate data. Returns (clarityIndex score, netSentiment
    value) - the same sentiment counts feed both."""
    total_sentiment = stats.positive_count + stats.neutral_count + stats.negative_count
    net_sentiment = (
        round(100 * (stats.positive_count - stats.negative_count) / total_sentiment) if total_sentiment else 0
    )
    rating_score = _clamp((stats.avg_google_rating / 5) * 100, 0, 100)
    sentiment_score = _clamp(net_sentiment, 0, 100)
    response_rate_score = _clamp(stats.avg_response_rate_pct, 0, 100)
    mention_volume_trend_pct = (
        round(100 * (stats.total_mentions_now - stats.total_mentions_prior) / stats.total_mentions_prior)
        if stats.total_mentions_prior
        else 0
    )
    volume_trend_score = _clamp(50 + mention_volume_trend_pct, 0, 100)
    score = round(
        0.15 * rating_score + 0.40 * sentiment_score + 0.20 * response_rate_score + 0.25 * volume_trend_score
    )
    return score, net_sentiment


@router.get("/overview")
def get_overview(
    period: str = Query("7d"),
    source: str = Query("all"),
    entity: str = Query("all"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    period_from, period_to, prior_from, prior_to = _parse_period(period, from_, to)

    stats = get_overview_stats(
        db,
        period_from=period_from,
        period_to=period_to,
        prior_from=prior_from,
        prior_to=prior_to,
        source_category=source,
        entity=entity,
    )
    clarity_score, net_sentiment = _clarity_and_net_sentiment(stats)

    # deltaVsLastWeek/deltaPts: the Clarity Index / Net Sentiment computed
    # the exact same way, one period earlier - "last period's score,"
    # recursively defined against ITS OWN preceding period for its own
    # volume-trend term, rather than a second bespoke "what counts as
    # last week" definition.
    prior_span = period_to - period_from
    prior_stats = get_overview_stats(
        db,
        period_from=prior_from,
        period_to=prior_to,
        prior_from=prior_from - prior_span,
        prior_to=prior_from,
        source_category=source,
        entity=entity,
    )
    prior_clarity_score, prior_net_sentiment = _clarity_and_net_sentiment(prior_stats)

    total_mentions_delta_pct = (
        round(100 * (stats.total_mentions_now - stats.total_mentions_prior) / stats.total_mentions_prior)
        if stats.total_mentions_prior
        else 0
    )

    # Checklist 0.24 (docs/decisions/14-last-synced-excludes-non-data-sources.md):
    # a job whose own success/failure has no bearing on external data
    # freshness (app.jobs.classification_job, app.jobs.reddit_deletion_job -
    # see each module's own IS_DATA_SOURCE = False) must not count toward
    # "when was data last synced." getattr() defaults True so every
    # ordinary ingestion job (which doesn't set this attribute at all)
    # counts exactly as before.
    last_synced_at = None
    for job in JOBS:
        if not getattr(job, "IS_DATA_SOURCE", True):
            continue
        freshness = get_source_freshness(db, job.SOURCE_NAME)
        if freshness.last_success_at and (last_synced_at is None or freshness.last_success_at > last_synced_at):
            last_synced_at = freshness.last_success_at

    return {
        "clarityIndex": {"score": clarity_score, "deltaVsLastWeek": clarity_score - prior_clarity_score},
        "totalMentions": {
            "value": stats.total_mentions_now,
            "deltaPct": total_mentions_delta_pct,
            "priorPeriodValue": stats.total_mentions_prior,
        },
        "netSentiment": {"value": net_sentiment, "deltaPts": net_sentiment - prior_net_sentiment},
        "avgGoogleRating": {"value": stats.avg_google_rating, "reviewCount": stats.google_review_count},
        "activeAlerts": {
            "total": stats.active_alerts_total,
            "crisis": stats.active_alerts_crisis,
            "digest": stats.active_alerts_digest,
        },
        # See module docstring's "AI summary" section - null, not a
        # canned string, until a real LLM summary exists.
        "aiSummaryText": None,
        "lastSyncedAt": last_synced_at.isoformat() if last_synced_at else None,
    }


@router.get("/overview/trend")
def get_overview_trend_route(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Added in Phase 8 (8.1) - closes the Overview tab's Sentiment Trend
    chart, which had no backing endpoint at all when Phase 7 shipped (see
    docs/api-contract.md's own note on this route). No period/source/
    entity filter - this is the whole-brand trend the chart is about, per
    the contract; extend if a real filtered use ever needs it."""
    return {"days": get_overview_trend(db, days=days)}
