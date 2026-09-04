# Decision record: Reddit 48-hour deletion propagation

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on; nothing here has been agreed or implemented. It covers checklist item 0.7 and requirement C-2.

## Context (verified in this session)

`docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` was read directly (not just relied on via the checklist's quotes) and its "Data handling" section states, in writing, to Reddit:

> "The tool complies with Reddit's Responsible Builder Policy: content and author-identifying data are removed within 48 hours of deletion on Reddit, OAuth is used for all requests, and a descriptive, versioned User-Agent string identifies the application per Reddit's required format."

This is a commitment made in a **submitted commercial Data Access Request**, signed by Angelo Mojica (`ai@remedy.ph`), not an internal aspiration.

Repo verification (this session, case-insensitive grep across the whole repo excluding `.git`):

- `praw` / `PRAW`: zero hits in any `.py`, `.md`, `.txt`, or `.html` file, and zero hits in `backend/requirements.txt` (which contains only `google-auth`, `google-auth-oauthlib`, `requests`, `python-dotenv`).
- `reddit` (any case): the only repo hits are (a) documentation/checklist text describing the gap, (b) the mockup rendering a single static Reddit feed item (`remedy-pulse-mockup.html:679-686`, `u/skinseeker_mnl` in `r/PhilippinesSkincare`), and (c) the disclaimer text in `docs/README-Remedy-Pulse-Demo.md` ("Nothing is connected to real Google, Instagram, X, Reddit, or news data yet").
- No Reddit ingestion code, no schema field for a stored source ID, no scheduled job, and no deletion-check logic exist anywhere in the repo.

There is consequently **no Reddit ingestion pipeline to attach a deletion job to today** — see `docs/decisions/reddit-integration-status.md` for that gap in full. This document is scoped only to the retention/deletion obligation itself.

## The nature of the obligation

Reddit does not notify integrators when a post or comment is deleted — there is no webhook, no deletion feed, nothing pushed to the consumer. Compliance therefore cannot be built as a delete handler reacting to an event. It has to be built as a **pull**: keep the Reddit-assigned ID of every post/comment you've stored, and periodically re-fetch each stored ID to check whether the source now 404s or returns `[deleted]`/`[removed]`; if so, purge your local copy (content and author-identifying fields) within the 48-hour window.

In other words: this is ingestion run in reverse — a recurring job with its own schedule, its own API quota consumption, and its own failure modes (a failed run doesn't just mean stale data, as it would for ordinary ingestion; it means an active breach of a written retention commitment, growing more overdue with every hour it stays down).

## Options considered

**A. Bundle deletion propagation into the same milestone as initial Reddit ingestion (build both together).**
The deletion worker and the ingestion adapter share the same ID/schema dependency and the same PRAW/OAuth client setup, so building them in the same pass avoids re-touching the same code twice and avoids a window where Reddit content is stored with no retention mechanism at all.
Cost: it makes the "Reddit adapter" milestone larger and later — checklist 4.3 (adapter) is already scoped as Effort L, and 5.1 (deletion worker) as Effort L·risky; combining them extends the single milestone rather than letting ingestion ship independently.

**B. Ship Reddit ingestion first, with deletion propagation as a fast-follow inside a short, explicitly committed window (e.g., before or immediately at the point real data starts flowing).**
Lets the ingestion adapter (which several other checklist items and the Mentions feed depend on) land without waiting on the retention worker's own build time.
Cost: this only stays safe if the window is genuinely short and explicitly committed to, not an open-ended "later" — every day between ingestion going live and the deletion worker going live is a day the written 48-hour commitment is not actually enforced by anything.

**C. Defer indefinitely / treat as a "nice to have" cleanup task.**
Not viable — see Reasoning below. Included only to be explicit that it was considered and rejected.

## Reasoning

The checklist's own risk framing for this item (0.7, and its Phase 5 counterpart 5.1) is CRITICAL: *"Breach of the terms the Reddit Data API access was granted under — the provider can revoke access, which removes a P0 source entirely."* Reddit's mentions requirement (P0-1: cross-source mentions feed) and the C-1/C-3 commitments (PRAW + OAuth, versioned User-Agent) both depend on Reddit access continuing to exist. A revoked grant doesn't just fail one checklist item — it removes an entire source the PRD lists as P0, retroactively invalidates the mockup's Reddit representation, and does so as a direct, foreseeable consequence of a written promise the team already made to the provider.

That is why Option C is rejected outright: once Reddit ingestion exists and third-party content is actually being stored, the retention obligation is live from day one, not from whenever it becomes convenient. The implementation roadmap's own Phase 5 header states this as a hard constraint: *"nothing in this phase may be deferred past the first production ingestion run against real data... Once third-party content is in your store, 5.1 is already overdue."*

Between A and B, the reasoning turns on how tightly the team can commit to and hold a fast-follow window. A costs schedule up front but removes any period of unenforced risk. B is faster to first Reddit data but only stays defensible if the follow-up window is dated and tracked with the same seriousness as the ingestion milestone itself — an undated "we'll get to it" is functionally Option C.

## Recommendation

Recommend **Option A or a tightly-dated Option B** — the team should pick between these two, not treat the deletion worker as generically lower priority than ingestion. Whichever is chosen, the commitment must be dated and tracked in the roadmap (today's roadmap has no Reddit line item at all — see `docs/decisions/reddit-integration-status.md`), and no production Reddit ingestion run should occur before the retention mechanism (or its committed, dated follow-up) exists, per the roadmap's own Phase 5 hard constraint.

## What would change this recommendation

- **A firm answer that the Reddit Data Access Request was never approved** (see checklist 1.2 — status currently unknown). If Reddit access was denied or never granted, this entire item becomes moot until/unless a future request succeeds, and effort should not be spent building it prematurely.
- **Reddit granting an extended or different retention exception** in writing (unlikely, but would directly change the 48-hour design constraint).
- **A vendor decision (Awario Enterprise / Brand24) that hosts Reddit data and contractually assumes deletion-propagation responsibility itself** — the roadmap's PRD lists the vendor path as still open (`remedy-pulse-prd.md`, Open Questions: "Vendor/build path"). If the chosen vendor owns this obligation contractually, the team would not need to build this worker at all, which would supersede this document.
