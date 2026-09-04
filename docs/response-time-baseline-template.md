# Response Time Baseline — Fill-In Template (3.3)

**This table starts empty. Do not fill it with invented, estimated, or "roughly remembered" numbers.** Every row must come from actually opening Remedy's Google Business Profile reviews and reading real dates off real reviews. A guessed number in this table is worse than no number — it would let someone later claim a pre-launch baseline exists when it doesn't.

---

## Why this exists

The PRD's own qualitative success metric asks whether "response time to negative reviews has improved since launch." That question is unanswerable without something to compare the post-launch number against — there is no "before" to measure "after" against unless someone captures it now, before Remedy Pulse changes how replies happen. The implementation checklist (3.3) calls this out as the one item on the whole list that becomes **permanently impossible to do** if it's skipped: once the tool is live and changing reply behavior, the pre-launch world is gone and can never be sampled again. This document exists to make that one-time capture as easy as possible to actually complete, instead of it quietly slipping.

## Who should do this and what they need

Anyone with access to Remedy's **Google Business Profile** (or Google Maps listing management) for its branches — specifically, whoever already manages and posts replies to Remedy's reviews today. That's it.

- No engineering access needed.
- No API key needed.
- No approval or waiting period — unlike the Phase 1 access requests, this is just the existing dashboard UI that account owners/managers already use to reply to reviews.

If you can already log in and reply to a Remedy review on Google, you have everything required to do this task.

## Exact steps

1. Open Google Business Profile (or Google Maps listing management) for each Remedy branch.
2. Go to **Reviews**, sort by date (newest first), and find the last ~20 reviews rated **2 stars or below** from before **[fill in: today's date, i.e. the date you are doing this capture]**. If a branch has fewer than 20 qualifying reviews, use however many exist and note that in the table.
3. For each one, note:
   - The review's posted date/time.
   - The reply's posted date/time, **if a reply exists**.
   - The gap between the two, in hours.
4. **A review with no reply yet is not missing data — it's a real, worth-recording outcome.** Do not skip it and do not leave it out because it has no gap to compute. Record it explicitly (e.g. "no reply as of [the date you're doing this capture]") so the baseline isn't quietly biased toward only the reviews that happened to get answered. If the no-reply reviews were excluded, the baseline would look better than reality.

## Fill-in template

Columns match the parameters `record_baseline_response_time()` (in `backend/app/repository.py`) takes: `source_description`, `response_time_hours`, `captured_by`, `notes`. Rows below are **examples only** — replace them entirely with real captured rows, and delete the example rows once real data exists.

| source_description | response_time_hours | captured_by | notes |
|---|---|---|---|
| `<example — replace>` Remedy BGC, 2★, posted 2026-05-14 | `<example — replace>` 61.5 | `<example — replace>` J. Dela Cruz | Replied 2026-05-17 |
| `<example — replace>` Remedy Greenhills, 1★, posted 2026-06-02 | `<example — replace>` — | `<example — replace>` J. Dela Cruz | No reply as of 2026-09-04 |
| `<example — replace>` Remedy Alabang, 2★, posted 2026-07-20 | `<example — replace>` 8.0 | `<example — replace>` J. Dela Cruz | Replied same day |

Notes on the columns:
- `source_description` — enough detail to identify which review this is later (branch, star rating, posted date). It does not need to be a URL.
- `response_time_hours` — a number, in hours. If there is no reply yet, leave this blank in the table (recorded as `None` in the database — see below) rather than inventing a number, and use `notes` to say so (as in the second example row above).
- `captured_by` — the name of whoever did the lookup, for accountability if a number is later questioned.
- `notes` — optional; use it for anything that doesn't fit the other columns (partial data, an unusual case, the "no reply yet" flag, etc).

## What happens after the table is filled in

Once real rows exist above, someone (engineering or whoever is comfortable running a short Python snippet) records each row in the database via `record_baseline_response_time()` in `backend/app/repository.py`, so the numbers land in the `response_time_baselines` table rather than staying stranded in this markdown file:

```python
from app.repository import record_baseline_response_time

record_baseline_response_time(
    session,
    source_description="Remedy BGC, 2★, posted 2026-05-14",
    response_time_hours=61.5,
    captured_by="J. Dela Cruz",
    notes="Replied 2026-05-17",
)
```

For a row with no reply yet, pass `response_time_hours=None` — the function accepts that explicitly (it's a real, expected outcome, not an error case) — and put the "no reply as of [date]" detail in `notes`, e.g.:

```python
record_baseline_response_time(
    session,
    source_description="Remedy Greenhills, 1★, posted 2026-06-02",
    response_time_hours=None,
    captured_by="J. Dela Cruz",
    notes="No reply as of 2026-09-04",
)
```

Run one call per row.

Once every row is recorded, `get_baseline_summary()` (also in `backend/app/repository.py`) reports back the total count, how many had no reply yet, and the median/mean response time across only the rows that did get a reply — the numbers the 30-day post-launch comparison will be measured against. The no-reply count is reported alongside on purpose: silently dropping those rows from the average would make the pre-launch baseline look better than it actually was.
