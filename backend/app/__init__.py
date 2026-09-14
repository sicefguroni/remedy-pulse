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
  admin.py      - the operator CLI (create a login, inspect a run).
"""

from dotenv import load_dotenv

# Loaded HERE, at package import, so every module under app.* sees .env
# regardless of which one is imported first.
#
# This is a fix, not a tidy-up. app/auth.py reads SESSION_SECRET_KEY from
# os.environ at import time and falls back to a random per-process key
# when it is absent — and nothing in the `uvicorn app.api.main:app` import
# path called load_dotenv() before this line existed. So the API server
# silently signed sessions with a throwaway key even when
# SESSION_SECRET_KEY was correctly set in .env: every token stopped
# verifying the moment the process restarted, and no two worker processes
# ever agreed on a signature. The symptom a user sees is being logged out
# at random and not being able to explain why.
#
# app/config.py's pydantic Settings reads .env itself, and
# app/classification.py calls load_dotenv() itself; neither is enough,
# because neither is guaranteed to be imported before app.auth.
# override=False (the default) so a real environment variable — what a
# deployment platform actually injects — always beats the local file.
load_dotenv()
