# Decision: where does the assignment roster come from? (6.4)

## Decision

The `User` table (`backend/app/models.py`, built in 5.5) **is** the assignment roster. `is_active=True` rows are assignable; `display_name` is what's shown in an "Assign to…" control — replacing the hardcoded four-name list (`Gian`, `Paul`, `Boom`, `Mixi`) currently baked into `remedy-pulse-mockup.html`'s `handleAssign()`.

No new table, no new column, no schema change. The query an assignment UI needs is exactly:

```python
from sqlalchemy import select
from app.models import User

assignable_users = session.execute(
    select(User).where(User.is_active == True)  # noqa: E712 — SQLAlchemy column comparison, not a Python bool check
).scalars().all()
```

That's a one-line query, not a function — it doesn't warrant its own file or helper in this batch's scope (`backend/app/topic_tagging.py` is about topic tagging; a `list_assignable_users()` helper bolted on there would be out of place, per this batch's own file-ownership note). Whichever module ends up owning the Phase 7 API's assignment endpoint is the natural home for it, once that module exists.

`Mention.assigned_to` stays free text for now (see `models.py`'s own comment on that column) — this decision is about where the *candidate list* for assignment comes from, not about turning `assigned_to` into a foreign key. That's a related but separate change or Phase 7 concern.

## Options considered

- **Keep the hardcoded list.** Simplest possible option — zero code. Rejected: this is exactly the failure mode the checklist names for 6.4 — *"Items get assigned to people who have left, or cannot be assigned at all."* A name leaving `handleAssign()`'s hardcoded array requires a code change and a deploy; a name that should be assignable (a new hire) can't be added without the same. Neither should require touching source code.
- **A separate `roster` / `team_member` table, distinct from `User`.** More flexible in the abstract — a roster entry doesn't have to imply a login. Rejected at this project's current scale: `User` (5.5) already has everything a roster needs (`display_name`, `is_active`) and was already built with dashboard login in mind. A second table duplicating `email`/`display_name`/`is_active` with no clear benefit yet is exactly the kind of over-building this project's own "don't build past what's actually asked" ethos (see the checklist's 4.6 item) warns against. If a real reason to split assignability from login rights shows up later (see "What would change this" below), split it then.
- **The `User` table itself (recommended).** It already exists, for exactly this purpose in spirit — `models.py`'s own docstring on `Mention.assigned_to` says the free-text roster "isn't this schema's business to hardcode — see 6.4," naming `User` as the eventual answer. `is_active` already gives a clean way to retire someone (a person who leaves stops being assignable) without touching the audit history on `Mention` rows they were previously assigned — `assigned_to` stays whatever string it already held, and every `Event` row they generated as `actor` stays put (per `Event`'s own docstring: that's a separate append-only log, not touched by deactivating a `User` row).

## The actual "who owns this roster" open question — still unresolved

The PRD names this as an open question, and this decision only answers the *mechanical* half of it (where the candidate list is read from). It does not answer, and nothing else in this repo answers, **who is responsible for keeping the `users` table itself current** — creating a row when someone joins the team, flipping `is_active=False` when someone leaves, correcting a `display_name`. That's an operational/staffing responsibility, not an engineering one, and this decision deliberately does not invent an answer for it — there is no way to know from this codebase who that person should be (Gian? Paul? whoever runs onboarding/offboarding at Remedy?). This needs a real person assigned before the roster can be trusted to stay accurate day to day; until then, `is_active=True` is only as correct as whoever last remembered to update it.

## What would change this

If the team ever needs **per-branch or per-role assignment restrictions** — e.g. only branch managers should be assignable for BGC's reviews, or only certain roles should see Reddit-sourced items — the flat `User` table stops being enough on its own. That would call for a roster-with-roles model (role/branch columns on `User`, or a join table between `User` and whatever branches/categories it should be scoped to), not a flat is-active flag. Revisit this decision if that requirement shows up; nothing in the current PRD or mockup asks for it yet, so it isn't built preemptively here.
