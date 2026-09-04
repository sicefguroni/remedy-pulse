# Remedy Pulse — Implementation Roadmap

**Format:** Now / Next / Later
**Window:** August 14 – September 30, 2026 (~6.5 weeks)
**Team:** Angelo (AI Engineer), Ceferino (AI Engineer Intern) — 2 people, 70/20/10 capacity split assumed (feature work / integration hardening / buffer)
**Source:** Remedy-Pulse-PRD.md

---

## Status Overview

0 items in progress, 0 completed (pre-kickoff), **1 item blocking everything downstream**, and the overall v1 scope is **at risk** for a 2-person team in 6.5 weeks — flagged below with a recommended fallback cut order.

---

## Now (Weeks 1–2 · Aug 14 – Aug 28)

| Item | Description | Status | Owner | Dependencies |
|---|---|---|---|---|
| Lock vendor/build path | Decide in-house vs. Awario Pro+CSV vs. Awario Enterprise vs. Brand24 | **Blocked** — awaiting stakeholder decision | Angelo + leadership | None — this is the dependency everyone else waits on |
| Vendor-agnostic data schema | Define one internal schema for mentions/reviews/EMV so any vendor adapter slots in behind it | On Track | Ceferino | None (can start immediately, in parallel with the decision above) |
| Wire mockup UI to a real backend | Replace the validated mockup's static demo data with live API calls, keeping the UI as-is | On Track | Angelo | Data schema above |
| Google Reviews ingestion | Google Reviews doesn't depend on the Awario/Brand24 decision — build this adapter first for guaranteed early progress | On Track | Ceferino | Data schema above |

**Why this sequencing:** the vendor decision is the single biggest risk to the whole timeline (per the PRD's Open Questions), so it's called out as its own tracked item with an explicit owner, not left implicit. Everything else in "Now" is deliberately chosen to be vendor-agnostic so the team isn't idle while that decision is pending.

## Next (Weeks 3–5 · Aug 29 – Sep 18)

| Item | Description | Status | Owner | Dependencies |
|---|---|---|---|---|
| Overview + health score | Landing view: composite score, mention volume trend, today's alerts | Not Started | Angelo | Mentions ingestion live |
| Mentions feed + alerts/assignment | Core feed, negative-sentiment flagging, assign/resolve workflow, CSV export | Not Started | Angelo + Ceferino | Vendor decision resolved; Google Reviews adapter |
| Reviews management | Star ratings, pending-reply flow per branch | Not Started | Ceferino | Google Reviews adapter |
| EMV engine | Peso-value calculation per press article with expandable formula | Not Started | Angelo | EMV formula sign-off (Open Question in PRD) |
| Topics clustering | Group mentions into themes with per-topic sentiment | Not Started | Ceferino | Mentions feed live |

**Why this sequencing:** Overview, Mentions, and Reviews are prioritized first because they map directly to the stated success metric — faster response to negative reviews/mentions. EMV and Topics follow since they're valuable but not on the critical path to that metric.

## Later (Week 6+ · Sep 19 – Sep 30 and beyond)

| Item | Description | Status | Owner | Dependencies |
|---|---|---|---|---|
| Competitors / share of voice | Remedy vs. named competitors, keyword-variant alias matching | Not Started | Angelo + Ceferino | Vendor's competitor-tracking capability (most vendor-dependent item in the whole PRD) |
| QA + stakeholder demo prep | End-to-end test pass, buffer for fixes, walkthrough for leadership | Not Started | Angelo + Ceferino | All P0 sections above |
| Media Meter/MediaWatch cutover | Formal sunset of the old tool once Remedy Pulse is trusted | Not Started | Leadership | 30 days of stable use post-launch (per PRD success metrics) |

**P1/P2 items from the PRD (weekly AI summary, command palette, simulated mentions, real-time alerting, auto-draft replies, historical backfill, multi-location dashboards) are intentionally not scheduled in this window** — they're valuable but not required to hit the September 30 date or the core success metric, and pulling them in would only increase risk to the items above.

---

## Risks and Dependencies

- **Vendor decision (blocking, top risk).** No owner-assigned deadline exists yet for this decision. Recommend locking it by **end of Week 1 (Aug 21)** — every day it slips shortens an already-compressed build window, and it directly blocks Mentions, Competitors, and part of Topics.
- **Capacity vs. ambition.** Six dashboard sections plus a live backend integration in 6.5 weeks for a 2-person team (one an intern) is an aggressive scope. If the vendor decision slips past Aug 21, or if any adapter proves harder than expected, **Competitors should be the first thing cut to "Later"/post-launch** — it's the most vendor-dependent, least tied to the stated success metric, and the PRD already scopes it as reflecting "a later build phase, not day one" in the original mockup notes.
- **EMV formula sign-off (Open Question, unresolved).** If leadership hasn't approved the reach/placement-value assumptions by the time the EMV engine is scheduled to start (Week 3), that item should slip rather than ship with an unapproved formula.
- **No project tracker connected.** This roadmap isn't synced to Asana/Linear/Jira/etc. — statuses above are a snapshot as of today and will need manual updates as work progresses, or the team can connect a tracker so this stays live.

## Changes This Update

This is a first-draft roadmap (no prior version existed) built directly from the Remedy Pulse PRD — no changes to summarize yet. Future updates should track: vendor decision outcome, any date slips, and whether Competitors gets cut or stays in scope for September 30.