# Decision: where does mention/review/article data live? (2.2)

## Decision

**Postgres**, accessed through SQLAlchemy 2.0 with Alembic migrations. This is the checklist's own stated "lean" for 2.2, adopted as-is rather than re-litigated — the reasoning below is why it still holds for this repo specifically, not a generic argument for Postgres.

This is a recommendation implemented as working code (schema, migrations, repository layer), not a unilaterally executed infrastructure decision — no production database has been provisioned; what exists is a local `docker-compose.yml` for development and a schema that runs the same way against any real Postgres instance the team points it at later.

## Options considered

- **Postgres.** Real concurrency, real migrations, a portable JSON column for raw source payloads, and the deletion/retention queries Phase 5 needs (find every row from a given Reddit post ID; purge on a schedule) are ordinary indexed queries.
- **SQLite.** Zero ops, a single file, genuinely enough volume headroom for one clinic group. The cost shows up the moment ingestion (Phase 4, running on a schedule) and the API/UI (Phase 7) read and write concurrently — SQLite's single-writer model turns that into lock contention, and a later multi-brand expansion (a stated P2 in the PRD) would force a migration anyway.
- **Whatever the vendor gives you.** If the Phase 1 vendor/build decision lands on a hosted platform (Awario Enterprise, Brand24, etc.), some of this storage might already exist. But it removes the team's ability to implement the C-2 Reddit deletion-propagation job (0.7) against that data, and to join it with Remedy's own Google review data for the Clarity Index and EMV calculations — both need direct query access, not whatever export the vendor exposes.

## Reasoning

Phase 5's deletion-propagation job and Phase 3's metrics query are both, mechanically, "find every row matching a source ID or a timestamp range, fast." That's exactly where SQLite's single-writer model starts hurting, and exactly what Postgres does unremarkably. The schema in this PR (`backend/app/models.py`) uses only portable SQLAlchemy column types (no Postgres-specific `JSONB`, no dialect-specific features) specifically so it also runs against SQLite in tests — Postgres is the target for anything that touches real data, SQLite is only ever the test harness, never a proposed production alternative.

SQLAlchemy + Alembic over raw SQL migrations: both are legitimate for a team this size, but Alembic's autogenerate-from-model-diff workflow keeps the schema and the migration history from drifting apart (2.6's explicit requirement) with less hand-maintained SQL, and it's the tool most Python engineers joining this project will already know.

## What would change this

- If the Phase 1 vendor decision lands on a platform that **both** hosts the mention store **and** contractually handles Reddit deletion propagation, the calculus changes completely — the only thing this project would own directly is Google review data, and SQLite (or no local store at all) becomes plenty. Re-open this decision if that happens.
- If Remedy Pulse stays single-clinic-group and single-brand indefinitely and ingestion never needs to run concurrently with the API, SQLite's cost never materializes — worth revisiting if the roadmap's multi-brand P2 gets dropped for good rather than deferred.
