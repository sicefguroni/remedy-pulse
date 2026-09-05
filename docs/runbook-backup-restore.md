# Backup and restore runbook (9.5)

**This restore was actually executed and verified during this pass, on
2026-09-05, against the real local Postgres container
(`backend-postgres-1`) — not just a written procedure.** The checklist's
own framing for this item is exactly why that distinction matters: *"A
backup with no tested restore is a hope, not a backup."* What follows is
the real procedure, the real commands, and the real verification output
from that run — not a hypothetical.

## What was actually done, step by step

1. **Seeded identifiable data** so the restore could be verified
   precisely, not just "the row counts look plausible": a `User` row
   (`backup-drill-verify@example.com`), an `assign_mention()` +
   `resolve_mention()` call against an existing `Mention` row, and a
   `log_event(EventType.LOGIN)` call. Recorded the exact baseline:

   ```
   mentions 6
   ingestion_runs 2
   events 3
   users 1
   response_time_baselines 0
   seeded user row: (1, 'backup-drill-verify@example.com', 'Backup Drill Verify User')
   assigned mention row: (1, 'Backup Drill Tester', True, True)
   ```

2. **Took a real backup**, custom format (`-F c` — supports selective/
   parallel restore, the standard choice for a real Postgres backup, not
   just a plain-SQL dump):

   ```
   docker exec backend-postgres-1 pg_dump -U remedy_pulse -d remedy_pulse -F c -f /tmp/remedy_pulse_backup.dump
   ```

3. **Copied the backup out of the container to host storage**
   (`docker cp`) — a backup that only ever lives on the same disk/
   container as the live database isn't a real backup; this step
   simulates it landing somewhere that survives the database (or the
   container) being destroyed:

   ```
   docker cp backend-postgres-1:/tmp/remedy_pulse_backup.dump <host path>
   ```

4. **Simulated total data loss for real** — terminated any open
   connections, then dropped the database outright (not a table
   truncate, an actual `DROP DATABASE`):

   ```
   psql -U remedy_pulse -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='remedy_pulse' AND pid <> pg_backend_pid();"
   psql -U remedy_pulse -d postgres -c "DROP DATABASE remedy_pulse;"
   ```

   Confirmed the loss was real by having the actual application try to
   connect and fail:

   ```
   psycopg.OperationalError: connection failed: ... FATAL: database "remedy_pulse" does not exist
   ```

5. **Recreated an empty database and restored into it** from the backup
   copied out in step 3:

   ```
   psql -U remedy_pulse -d postgres -c "CREATE DATABASE remedy_pulse OWNER remedy_pulse;"
   docker cp <host path> backend-postgres-1:/tmp/remedy_pulse_restore.dump
   pg_restore -U remedy_pulse -d remedy_pulse -v /tmp/remedy_pulse_restore.dump
   ```

   `pg_restore` recreated every table, sequence, default, constraint, and
   index, and reloaded every row — including `alembic_version`, so the
   restored database reports the correct migration head with no manual
   `alembic upgrade` needed.

6. **Verified the restore matched the baseline exactly**, re-querying
   the same values recorded in step 1:

   ```
   mentions 6
   ingestion_runs 2
   events 3
   users 1
   response_time_baselines 0
   seeded user row: (1, 'backup-drill-verify@example.com', 'Backup Drill Verify User')
   assigned mention row: (1, 'Backup Drill Tester', True, True)
   ```

   Every count and every specific value matched the pre-loss baseline
   exactly.

7. **Verified schema integrity**, not just data:
   - `alembic current` → `19e01c8137d1 (head)` — correct, matching what
     was current before the drop.
   - `alembic check` → `No new upgrade operations detected.` — zero
     drift between the restored schema and the SQLAlchemy models.

8. **Verified the application itself, not just raw SQL**, by running
   the real Postgres-backed integration test suite
   (`test_app_repository_postgres.py`, `test_app_events_postgres.py`,
   `test_app_auth_postgres.py`) against the freshly-restored database:
   **8/8 passed.** This proves ordinary application code (upserts,
   the ingestion ledger, user creation/authentication) works correctly
   against the restored database, not just that a `SELECT COUNT(*)`
   matches.

## What this proves, and what it doesn't

**Proves:** the backup/restore mechanism itself works correctly end to
end — schema, data, constraints, sequences, and the application code
that reads/writes through them, verified against a real, deliberately
destroyed and restored database, not a mocked or hypothetical one.

**Does NOT prove** (out of scope for this pass, named honestly rather
than silently implied):
- **Production backup scheduling/retention.** This drill was a manual,
  one-time `pg_dump`. A real operational schedule (how often, retained
  how long, stored where durably — see
  `docs/decisions/08-secrets-at-rest.md` for the parallel "which platform"
  open question this shares, since both depend on the still-undecided
  hosting choice) is a separate decision, not made here.
- **Restore time at production data volume.** This drill's database was
  small (6 mentions, a handful of other rows) — a real production
  restore's duration at real volume hasn't been measured and may differ
  meaningfully.
- **Point-in-time recovery** (restoring to a specific moment between
  backups, e.g. via WAL archiving) — this drill covers only "restore
  from the most recent full backup," the simpler and more commonly
  needed case, not continuous recovery.

## Recommended procedure going forward

Until a real hosting platform is chosen (still blocked, per the
roadmap's vendor decision) and its native backup tooling can be used
instead: run the exact procedure above on a schedule (daily is a
reasonable starting point given this project's stated same-day/
next-day freshness target — see `docs/runbook-source-failures.md` for
where that framing comes from elsewhere in this project), retain at
least the last 7 daily backups, and store them somewhere that survives
the database container being destroyed (this drill's `docker cp` to
host storage is the minimum viable version of that; a real object-store
upload is the production equivalent). Re-run this exact drill (steps
1-8) whenever the schema changes meaningfully or on some regular cadence
(quarterly is a reasonable floor) — a restore procedure that was
verified once and never checked again is close to as risky as one never
verified at all.
