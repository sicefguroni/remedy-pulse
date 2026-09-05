# Decision record: Reddit integration status — reconciling the written commitment with what exists

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on; nothing here has been agreed or implemented. It covers checklist item 0.8 and requirements C-1, C-3.

## Context (verified in this session)

`docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` was read directly. Its "What the integration does" section states, in writing, to Reddit:

> "Using PRAW (Python Reddit API Wrapper) with OAuth authentication, the tool performs keyword-based searches across public Reddit content for mentions of the client's brand name and its known variants, plus a small, fixed list of named competitor brands."

And its "Data handling" section commits to:

> "...a descriptive, versioned User-Agent string identifies the application per Reddit's required format."

This is a submitted commercial Data Access Request describing an integration as if it is the thing being built. It is not phrased as a future intention — it describes present-tense behavior ("performs keyword-based searches... Matched posts and comments are stored with sentiment/topic tagging").

Repo verification (this session):

- `grep -rni "praw"` across the entire repo (all file types, excluding `.git`): **zero hits** outside of documentation describing the gap (the implementation checklist itself). `backend/requirements.txt` lists exactly four packages — `google-auth`, `google-auth-oauthlib`, `requests`, `python-dotenv` — no PRAW, no `asyncpraw`, no Reddit HTTP client of any kind.
- `grep -rni "reddit"` across the entire repo: no ingestion code, no OAuth client setup, no User-Agent string construction, no schema field, no scheduled job. The only substantive hits are:
  - `remedy-pulse-mockup.html:679-686` — one static, hardcoded feed item rendering a Reddit mention (`u/skinseeker_mnl`, `r/PhilippinesSkincare`, "Reach: ~1,200") indistinguishable in the rendered UI from the Google/Instagram/News feed items around it, other than the platform tag and emoji avatar.
  - `remedy-pulse-mockup.html:548, 1093, 1660` — a hardcoded "Sentiment dip in Reddit thread" alert and a mention of a Reddit thread in the canned AI weekly-summary text, both presented as live findings.
  - `remedy-pulse-mockup.html:619` — the source-breakdown pie chart shows "Reddit 14%" of mention share as a static number.
  - `remedy-pulse-mockup.html:1406` — Reddit is one of the platform filter options (`['All', 'Google', 'Reddit', 'Instagram', 'Facebook', 'News', 'TikTok', 'X']`), implying it is a filterable, live-equivalent source in the UI's own data model.
  - `docs/README-Remedy-Pulse-Demo.md:15` — "Everything you see... is sample data, not live information pulled from Google, Instagram, Reddit, etc."
  - `docs/README-Remedy-Pulse-Demo.md:51` — "Nothing is connected to real Google, Instagram, X, Reddit, or news data yet."

## An important nuance found during verification

The task framing behind this checklist item characterizes the demo guide as listing Reddit among tracked channels "without disclosing it's sample data." Reading the demo guide directly shows this needs qualifying, not just repeating: **the demo guide does carry an explicit, repeated, document-level disclosure** that Reddit data is sample-only and that no real Reddit connection exists (the two quotes above). The gap is narrower and more specific than "no disclosure exists" — it is that:

1. The disclosure lives in a separate companion document (`docs/README-Remedy-Pulse-Demo.md`), not inside the mockup UI itself. Someone who opens `remedy-pulse-mockup.html` directly, without reading the demo guide first, sees the Reddit feed item, alert, pie-chart share, and platform filter rendered with the same visual treatment as sources that actually have backend code (Google) — the only in-UI cue is a single global "Demo" badge in the top-right corner, not a per-item or per-source label.
2. Neither the mockup nor the demo guide's disclosure communicates the *scale* of the gap — that Reddit isn't "not connected yet" in the same sense Google Reviews is (Google has a working, tested connector script gated only on API access approval — see `backend/README.md`), but has **zero ingestion code of any kind**, and is the only P0 source with a written third-party compliance commitment (C-1 through C-5) that is entirely unimplemented.

## Options considered

**A. Build the Reddit adapter now, as part of Phase 0/near-term work**, pulling it forward from the roadmap's current implicit placement.
This would close the C-1/C-3 gap directly and make the mockup's Reddit representation and the PDF's written commitment true rather than aspirational.
Cost: `remedy-pulse-roadmap.md` currently has **no Reddit line item anywhere** — not in Now, Next, or Later — and the checklist places full Reddit ingestion in Phase 4 (adapter, item 4.3) and Phase 5 (compliance, items 5.1/5.2), which the checklist's own sequencing table puts well after Phase 0–3 foundational work. Pulling a full adapter (PRAW client, OAuth, versioned User-Agent, keyword search, sentiment/topic tagging, plus the deletion-propagation worker from `docs/decisions/03-reddit-deletion-propagation.md`) into Phase 0/near-term work competes directly with the 27-day runway the checklist's "Timeline reality check" section describes, and with the vendor-path decision (`remedy-pulse-prd.md` Open Questions) that is still unresolved and that the roadmap's own top risk entry says is blocking Mentions, Competitors, and Topics.

**B. Explicitly re-scope Reddit ingestion into a later phase, with the team's shared understanding updated to match.**
The checklist already tentatively places full Reddit ingestion in Phase 4/5. This option formalizes that placement — adding it to the roadmap document itself (which currently omits Reddit entirely) with an owner and approximate timing — and, separately, getting an explicit team acknowledgment that "Reddit is nearly done" is not presently true, so the Mentions feature's estimate can account for a full missing adapter rather than assuming partial completion.
Cost: none in engineering effort, but it requires someone to update the roadmap and to correct the assumption in the team's own head — a social/communication cost, not a technical one.

## Reasoning

The checklist's own risk statement for this item is direct: *"A source everyone believes is nearly done does not exist; the estimate for Mentions is wrong by a whole adapter."* The evidence supports that framing precisely: a PDF was submitted describing Reddit ingestion in the present tense, the mockup renders Reddit content with full visual parity to real sources, and the roadmap — the document that should reflect what work remains — has no entry for Reddit at all. Anyone scoping the Mentions feature from the roadmap alone would not see Reddit as outstanding work, because it isn't listed as work at all.

This is not primarily an engineering-effort problem; it's a shared-understanding problem. The highest-leverage fix is making sure everyone building or estimating around Reddit knows the true state (Option B) before deciding whether to also accelerate the build (Option A). Building it now (Option A) doesn't fix the underlying estimation risk unless it's actually completed before anyone builds Mentions-feed work that assumes Reddit already works — and 27 days is not much runway to absorb an L-effort adapter plus its L·risky compliance worker without displacing something else.

## Recommendation

Recommend **Option B as the immediate action, with Option A's timing (build now vs. Phase 4/5) decided as its own follow-up call once the vendor-path decision (`remedy-pulse-prd.md` Open Questions; roadmap Risks) resolves.** Concretely: add Reddit to the roadmap explicitly (it currently has no line item), correct the team's shared assumption about its completion state, and only then decide whether it's worth pulling forward into Phase 0 given the 27-day runway.

**Explicitly out of scope for this document:** this decision record does not itself add a per-source disclaimer to the mockup or the demo guide — that is separate, already in-flight work (see checklist item 0.20, which addresses a related but different demo-guide claim, and the general instruction in this task not to edit those files). It is flagged here because the nuance found above — that the mockup UI itself carries no per-item disclosure, only a single global "Demo" badge — is a real gap worth closing, and whoever owns that work should be made aware of it.

## What would change this recommendation

- **The Reddit Data Access Request (checklist item 1.2) coming back denied.** If Reddit never grants access, Option A becomes moot regardless of team bandwidth, and the roadmap update in Option B should say so explicitly rather than listing Reddit as pending.
- **The vendor-path decision (roadmap's top blocking risk) landing on a vendor that provides Reddit data itself** (e.g., Awario Enterprise, Brand24) — in which case building a first-party PRAW adapter may be unnecessary work entirely, and this document's recommendation would need to be revisited against whatever the vendor contract actually provides.
- **A hard commitment of engineering time** from Angelo/Ceferino specifically carved out for Reddit within the current 27-day window — if that capacity is confirmed available without displacing higher-priority P0 items (per the checklist's Sequencing section), Option A becomes more attractive than this document currently recommends.
