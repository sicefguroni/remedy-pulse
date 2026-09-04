"""backend/app — Phase 2 foundations: schema, persistence, and service config.

This package is deliberately separate from the existing fetch_*.py scripts
at the top of backend/. Those scripts still run standalone (module-level
load_dotenv() + os.getenv(), exactly as Phase 0 left them) — this package
is the skeleton Phase 4 ("harden the connectors into scheduled jobs")
wires them into, not a rewrite of them done ahead of that phase. See
docs/implementation-checklist.md, Phase 2's status note, for that boundary.

Contents:
  config.py     - Settings: typed, validated service configuration (2.3).
  models.py     - the vendor-agnostic Mention/IngestionRun schema (2.1).
  db.py         - SQLAlchemy engine/session setup.
  repository.py - idempotent upsert (2.5) and the ingestion ledger (2.4).
"""
