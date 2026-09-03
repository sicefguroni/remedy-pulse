# Remedy Pulse — Product Requirements Document

**Prepared by:** Angelo (AI Engineer) & Ceferino (AI Engineer Intern, working with Angelo)
**Status:** Draft — vendor path (in-house / Awario Pro+CSV / Awario Enterprise / Brand24) not yet finalized; this PRD is written to be vendor-agnostic so it holds regardless of which backend data source is chosen.
**Target v1 launch:** September 30, 2026

---

## Problem Statement

Remedy currently relies on Media Meter/MediaWatch, which only surfaces traditional press coverage and gives no visibility into social conversation — Google reviews, Instagram/Facebook comments, Reddit threads, and general online sentiment about the brand. Marketing and leadership have no single place to see what's being said about Remedy in near-real time, no way to compare their standing against competitors like Belo, Aivee, and others, and no fast path from "a bad review just came in" to "someone on the team has responded." Every day this gap persists, negative reviews and mentions sit unanswered longer than they should, and the team is flying blind on how competitors are perceived relative to Remedy.

## Goals

- Give the marketing team a single dashboard that surfaces new mentions, reviews, and press coverage across all tracked channels within one business day of publication.
- Cut the time between a negative review/mention appearing and someone on the team acknowledging or replying to it.
- Provide a like-for-like view of Remedy vs. named competitors (share of voice, sentiment) that the team can currently only get by manually checking competitor pages.
- Put a defensible peso figure (EMV) on press coverage so leadership can see the value of PR efforts in numbers they already understand.
- Fully replace Media Meter/MediaWatch as the team's day-to-day source of truth for brand monitoring.

## Non-Goals

- **Automated review/mention replies.** The tool surfaces and routes items for a human to act on; it will not draft or send responses on the team's behalf in v1 — reduces liability risk and matches what the validated mockup already scoped.
- **Real-time (sub-hour) alerting.** Given the API constraints on Meta and Reddit (below), v1 targets same-day/next-day freshness, not live streaming alerts — trying for real-time now would force a premature, possibly expensive vendor commitment.
- **Deep historical backfill (>90 days).** v1 starts tracking from launch forward; going back further is a data-availability and cost question best revisited once the core loop is proven.
- **Non-Remedy brand monitoring (e.g., individual doctor/staff reputation).** Scope is the Remedy brand and its named clinic locations only.
- **Choosing the vendor/build path.** That decision (in-house vs. Awario Pro+CSV vs. Awario Enterprise vs. Brand24) is tracked separately in the vendor decision doc and is treated as an input to this PRD, not an output of it.

## User Stories

**Marketing team member (primary user)**
- As a marketing team member, I want to see all new mentions and reviews in one feed so that I don't have to check five different platforms manually.
- As a marketing team member, I want negative reviews and mentions flagged and assigned to a teammate so that nothing sits unanswered because no one knew about it.
- As a marketing team member, I want to mark an item as resolved once handled so that the team has a clear record of what's been addressed.
- As a marketing team member, I want to filter mentions by platform, sentiment, or keyword so that I can quickly find what I'm looking for during a busy day.
- As a marketing team member, I want to export mentions, reviews, or EMV data to CSV so that I can share it in reports or slides without re-typing anything.

**Leadership / executive reader**
- As a leadership reader, I want a short written summary of the week's brand health so that I don't have to read the raw dashboard to stay informed.
- As a leadership reader, I want a single health score and trend line so that I can tell at a glance whether things are improving or getting worse.
- As a leadership reader, I want to see the peso value (EMV) of our press coverage so that I can talk about PR impact in financial terms.

**Marketing team member (competitive intelligence)**
- As a marketing team member, I want to see Remedy's share of voice and sentiment next to named competitors so that I know how we stack up, not just how we're doing in isolation.
- As a marketing team member, I want to see which topics/themes are driving conversation so that I can tell if a spike is about service quality, pricing, a specific location, etc.

**Edge cases**
- As a marketing team member, if a data source is down or delayed, I want to see when the dashboard was last successfully synced so that I don't mistake stale data for "no news."
- As a marketing team member, if there are zero new mentions in a period, I want a clear empty state rather than an ambiguous blank screen.

## Requirements

### Must-Have (P0)

**Mentions feed**
- Single chronological feed of mentions across all connected sources (Google reviews, Instagram/Facebook, Reddit, news/press), each tagged with platform, sentiment, and timestamp.
  - *Acceptance criteria:* Given a new mention is ingested, when the team opens the Mentions tab, then it appears in the feed within one business day with platform, sentiment, and source link visible.
- Search/filter by keyword, platform, and sentiment.
  - *Acceptance criteria:* Given the team types a keyword, when they search, then only matching mentions are shown, updating as they type.
- CSV export of the current filtered view.
  - *Acceptance criteria:* Given a filtered mentions view, when the team clicks export, then a CSV downloads containing exactly the filtered rows.

**Alerts & assignment workflow**
- Negative-sentiment items are flagged automatically and can be assigned to a named teammate.
  - *Acceptance criteria:* Given a mention is classified negative, when it's ingested, then it appears in an alerts list with an "Assign" action.
- Items can be marked resolved, with resolution reflected in alert counts.
  - *Acceptance criteria:* Given an alert is resolved, when the team views the alerts count, then it decreases by one and the item shows a resolved state.

**Reviews management**
- Star ratings and reply status tracked per clinic branch, with a clear "pending reply" indicator that opens a reply flow.
  - *Acceptance criteria:* Given a review has no reply, when the team clicks "pending reply," then a reply box opens and submitting it updates the status immediately.

**Overview / health score**
- A single composite health score, mention volume trend, and "what needs attention today" summary as the landing view.
  - *Acceptance criteria:* Given the team opens the dashboard, then the Overview tab loads by default showing health score, volume trend, and outstanding alerts without further navigation.

**Topics**
- Mentions grouped into themes/topics with sentiment breakdown per topic, so a volume spike can be traced to a cause.
  - *Acceptance criteria:* Given a topic is selected, when the team drills in, then they see the mentions that make up that topic and their individual sentiment.

**EMV (Earned Media Value)**
- Peso value calculated per press article, with the underlying formula visible/expandable for each row.
  - *Acceptance criteria:* Given an EMV row, when the team clicks it, then the calculation inputs (reach, placement value, etc.) are shown, not just the final number.

**Competitors**
- Share-of-voice and sentiment comparison between Remedy and named competitors (Belo, Aivee, and others to be confirmed), including keyword-variant matching so brand aliases are captured.
  - *Acceptance criteria:* Given the Competitors tab, when the team views it, then Remedy and each named competitor show side-by-side share-of-voice and sentiment.

**Data freshness indicator**
- A visible "last synced" timestamp on every tab.
  - *Acceptance criteria:* Given data was last refreshed at time T, when the team views any tab, then T is visible and updates after a successful sync.

### Nice-to-Have (P1)

- AI-generated weekly written summary for leadership (already prototyped in the mockup as "This Week, Summarized"), with regenerate capability.
- Command palette / quick-jump navigation (⌘K) for faster power-user navigation.
- Simulated/test mention injection for QA and demo purposes, gated so it can't run against live data by accident.
- Per-mention drill-down showing full context (thread, comment chain) rather than just the top-level snippet.

### Future Considerations (P2)

- Real-time/near-real-time alerting once a data source with live API access is in place (depends on the vendor decision).
- Automated suggested-reply drafts (human-reviewed, not auto-sent).
- Historical backfill beyond 90 days.
- Expansion to individual clinic-location-level dashboards for branch managers.
- Multi-brand support if Remedy's parent company wants to monitor other brands in the same tool.

## Success Metrics

**Leading indicators (days–weeks post-launch)**
- Daily/weekly active use by the marketing team: target 80%+ of business days have at least one team login within 30 days of launch.
- Median time from a negative mention/review appearing to it being assigned: target under 4 business hours by 30 days post-launch (this is the core success focus for v1).
- Alert-to-resolution rate: target 90%+ of flagged negative items marked resolved within 5 business days.
- Export usage: at least one CSV export per week, indicating the data is being used in real reporting.

**Lagging indicators (weeks–months post-launch)**
- Media Meter/MediaWatch fully decommissioned as the team's primary tool within one quarter of launch.
- Leadership references EMV or share-of-voice figures from Remedy Pulse in at least one internal report or deck per month.
- Qualitative: marketing team reports (via a short check-in survey) that response time to negative reviews has improved since launch.

**Measurement method:** usage and timing metrics pulled from application logs (login events, alert timestamps, resolution timestamps); EMV/report references tracked manually via a monthly check-in with leadership until instrumentation exists to detect it automatically.

## Open Questions

- **Vendor/build path** *(stakeholder — Angelo/Ceferino + leadership)*: which of in-house, Awario Pro+CSV, Awario Enterprise, or Brand24 will supply the underlying data, and by when does that decision need to be locked to hit the September 30 launch? This is the single biggest risk to the timeline below.
- **Confirmed competitor list** *(stakeholder)*: is the competitor set for the Competitors tab limited to Belo and Aivee, or should others (e.g. SkinStation, DermHQ, Luminisce, Kamiseta — already stubbed in the mockup) be included at launch?
- **EMV formula sign-off** *(stakeholder/finance)*: has leadership formally approved the reach/placement-value assumptions behind the EMV calculation, or is that still provisional?
- **Negative-sentiment classification threshold** *(engineering)*: what precision/recall bar is acceptable for automatic sentiment tagging before it's trusted to drive the alert workflow unsupervised?
- **Assignment routing** *(stakeholder)*: is there a fixed roster of teammates alerts can be assigned to, and who owns keeping that roster current?

## Timeline Considerations

- **Hard deadline:** target v1 launch September 30, 2026 — roughly 6.5 weeks from this PRD's writing (August 14, 2026).
- **Critical dependency:** the vendor/build decision is not yet finalized. Every day that decision is open eats directly into the ~6.5-week build window, since the Mentions, Competitors, and Topics requirements above depend on which data source is feeding them. Recommend locking this within the first week.
- **Team capacity:** built and maintained by a two-person team (Angelo, AI Engineer; Ceferino, AI Engineer Intern). Given the six-section scope and the compressed timeline, phasing (see companion roadmap) will be necessary — not all P0 requirements above are equally fast to build regardless of vendor path.
- **Existing head start:** an interactive HTML mockup covering all six v1 sections has already been built and validated with stakeholders, which shortens the design/discovery phase considerably — engineering can build against an already-agreed-upon UI rather than starting from a blank page.