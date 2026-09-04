"""app/api/serializers.py — shared response-shape builders.

Keeping mention_to_dict() in one place is what keeps GET /api/mentions,
GET /api/topics/{key}/mentions, and (indirectly, via
repository.list_mentions_filtered()) POST /api/exports/mentions_csv
byte-for-byte consistent with docs/api-contract.md's mention item shape,
instead of independently-drifting copies of the same field list in three
route modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import Mention


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def enum_value(value: Any) -> Any:
    """Mention.sentiment (and similar columns) are plain String columns,
    not SQLAlchemy Enum types (see models.Mention's docstring) - a
    freshly-loaded row's value is ordinarily already a plain str, but a
    still-identity-mapped row from earlier in the same session can hold
    the Python enum member it was assigned with. Normalizes either shape,
    mirroring app.status_report's identical normalization for
    IngestionRun.status."""
    return getattr(value, "value", value)


def mention_to_dict(m: Mention) -> dict:
    """The exact item shape docs/api-contract.md's Mentions section
    specifies, reused for the Topics drill-down (same shape, per that
    section's own text) and as the source of truth for the mentions CSV
    export's column list."""
    return {
        "id": m.id,
        "platform": m.source,
        "author": m.author,
        "text": m.text,
        "url": m.url,
        "publishedAt": iso(m.published_at),
        "sentiment": enum_value(m.sentiment),
        "topics": m.topics or [],
        "venue": m.venue,
        "assignedTo": m.assigned_to,
        "assignedAt": iso(m.assigned_at),
        "resolvedAt": iso(m.resolved_at),
        "alertCategory": m.alert_category,
    }
