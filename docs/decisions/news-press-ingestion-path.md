# Decision record: news/press ingestion path for EMV

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on; nothing here has been agreed or implemented. It covers checklist item 0.9.

## Context (verified in this session)

`docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` was read directly. Its "Use case" section states:

> "...alongside other channels already in use (news via GNews, and pending review-site and social API integrations)."

This describes GNews as a channel **already in use**, in the same sentence structure that later (correctly) marks review-site and social integrations as "pending."

Repo verification (this session):

- `grep -rni "gnews"` across the entire repo (excluding `.git`): the only hits are documentation text (the implementation checklist) describing this exact gap. **No GNews code, API key reference, config value, or dependency exists anywhere.**
- `backend/requirements.txt` contains only `google-auth`, `google-auth-oauthlib`, `requests`, `python-dotenv` — no news API client of any kind.
- `remedy-pulse-roadmap.md` was read in full: it has **no line item for news or press ingestion** in Now, Next, or Later. The roadmap's EMV entry ("EMV engine — Peso-value calculation per press article with expandable formula," Next/Weeks 3-5) lists its only dependency as "EMV formula sign-off," not a data source — the roadmap does not surface that EMV has no ingestion path at all.
- The EMV tab is the largest set of peso figures anywhere in the product: verified directly in `remedy-pulse-mockup.html` — 6 hardcoded article rows (Rappler, Philippine Star, PeopleAsia, When In Manila, Manila Bulletin, ANC; lines 861-931), summing to **Gross ₱2,366,000** and **Net Favorable ₱2,809,000** (lines 842-843). Every one of these rows is static markup with no backing data source of any kind, per-source or otherwise.
- `backend/README.md`'s "Known limitations, honestly" section names a fallback concept, but only in the context of the **Google Reviews/Business Profile** connector, not news: *"If the Business Profile API access request gets rejected or stalls, the fallback is a licensed reviews aggregator — worth flagging to Paul/Marketing as its own §16-style decision if that happens."* This is a reviews aggregator, not a news aggregator — there is no equivalent sentence anywhere in the repo naming a news/press aggregator fallback. It is referenced below only because the task instructions asked to cross-reference it, and because the same "named fallback vendor, decided deliberately rather than discovered under pressure" pattern applies.

## Options considered

**A. Build a GNews adapter, as the PDF already asserts is in use.**
This makes the written commitment true rather than aspirational, and GNews is a low-friction, self-serve API (no approval-gate process like Google Business Profile or Reddit's Data Access Request).
Cost/risk: GNews's free tier is capped on both request volume and article history depth; whether its coverage of Philippine outlets (Rappler, Philippine Star, PeopleAsia, When In Manila, Manila Bulletin, ANC — the exact outlets in the mockup's EMV rows) and its historical lookback are sufficient has not been verified in this session — there is no code, config, or test call to check against. This is a real unknown, not a confirmed-safe option.

**B. A different news API or vendor** (e.g., NewsAPI.org, Mediastack, or a PH-market-specific news aggregation service).
Same self-serve advantage as GNews, without inheriting a possibly-mismatched written commitment. Some alternatives have stronger historical archive access or better regional-outlet coverage, which matters directly for EMV's backfill needs (the PRD non-goal caps backfill at 90 days, per `remedy-pulse-prd.md`, but even 90 days of PH outlet coverage needs verifying against whichever API is chosen).
Cost: abandons the written GNews commitment already made in a submitted compliance document, which itself needs reconciling (see Reasoning below) — choosing a different vendor doesn't remove that reconciliation work, it just changes what the reconciliation says.

**C. A licensed press-monitoring aggregator** (in the spirit of `backend/README.md`'s named reviews-aggregator fallback, applied here to news instead).
If the broader vendor/build decision (`remedy-pulse-prd.md` Open Questions: "in-house vs. Awario Pro+CSV vs. Awario Enterprise vs. Brand24") lands on one of the Awario/Brand24 paths, press/news monitoring may already be bundled into that vendor's product, making a separate GNews-style integration unnecessary.
Cost: this option is contingent on a decision (the vendor path) that the roadmap already flags as its single biggest blocking risk, with no owner-assigned deadline as of this session's read of the roadmap.

## Reasoning

Two facts make this more urgent than a typical "pick an API" decision:

1. **EMV currently has no data source of any kind** — not a stubbed one, not a partially-built one. The 6-row, ₱2.37M-gross table is entirely hand-written markup with zero backing code. This is a harder gap than most of the other Phase 0 findings, which mostly involve fixing behavior in existing code; there is no existing EMV ingestion code to fix.
2. **EMV is also the number most likely to be quoted to leadership.** The PRD's own leadership user story states this directly: *"As a leadership reader, I want to see the peso value (EMV) of our press coverage so that I can talk about PR impact in financial terms."* And the PRD's lagging success metric: *"Leadership references EMV or share-of-voice figures from Remedy Pulse in at least one internal report or deck per month."* A number explicitly designed to be repeated externally, with literally no identified ingestion path, is a materially different risk than an unimplemented UI affordance.

The written PDF commitment to GNews specifically matters because it was submitted to a third party (Reddit) as part of describing Remedy Pulse's overall data posture — "alongside other channels already in use." If GNews turns out not to be the chosen path, that sentence in an already-submitted document becomes inaccurate, which is a smaller version of the same problem documented in `docs/decisions/reddit-integration-status.md` for Reddit itself: a written claim about what's "already in use" that the repo does not support.

## Recommendation

Recommend starting with **Option A (GNews)**, since it is the path already asserted in writing and requires the least new decision-making to begin — but only as a time-boxed evaluation, not a default commitment: confirm GNews's coverage of the six-plus outlets the EMV mockup already exercises (Rappler, Philippine Star, PeopleAsia, When In Manila, Manila Bulletin, ANC) and its request-volume/quota terms against expected article volume, before building the full adapter (checklist item 4.5). If that evaluation fails, fall back to **Option B** for a like-for-like self-serve API swap, or **Option C** if the vendor-path decision resolves first and makes it moot. This mirrors the roadmap's own recommended pattern for the Google/Reddit/Meta access requests (checklist item 1.6): decide a fallback trigger now, rather than discovering the need for one under deadline pressure.

## What would change this recommendation

- **GNews pricing or quota proving incompatible with the required article volume or historical lookback** for the outlets already represented in the EMV mockup — this would move the recommendation to Option B outright.
- **The vendor/build decision landing on Awario Enterprise or Brand24** with press/news monitoring bundled in — this would settle the question by default in favor of Option C, and the GNews evaluation work would become unnecessary.
- **EMV formula sign-off (a separate open PRD question) stalling indefinitely** — if leadership doesn't approve the reach/placement-value assumptions the EMV calculation depends on (per the roadmap's own EMV risk note and checklist item 8.7's gate), the ingestion-path decision becomes lower urgency, since there would be no approved formula to feed data into regardless of source.
