# Manual QA checklist (9.1)

This is the other half of 9.1's "one acceptance test per PRD acceptance
criterion." `backend/tests/test_acceptance_p0.py` automates the
substantive behavior of all 11 PRD Must-Have (P0) Given/When/Then
criteria at the API level — see that file's own module docstring for the
full cross-reference table (which test covers which P0-N, and why).

**This document exists for one narrow reason: three of those eleven
criteria have a sliver of behavior that only a real browser can confirm**
— a keystroke actually happening live, a modal actually opening, what's
actually on screen the instant a page loads. No browser is available in
the working session that built this phase, so these three items are
listed here instead of being silently skipped. Nothing below duplicates
`test_acceptance_p0.py` — each item names exactly the part an API test
structurally cannot observe, and points back to the automated test that
already covers the rest of the same criterion.

A twelfth criterion, P0-9 (EMV row calculation inputs), is **not** listed
here even though it's also not automated — it's blocked on the 8.7
formula sign-off, not on browser access. `test_acceptance_p0.py` explains
that distinction in its own skipped test's `reason=`. There is nothing
yet for a human to click and see calculated, so it has no place in a
manual *testing* checklist.

---

## 1. Reviews reply box actually opens (P0-6)

**PRD:** *"Given a review has no reply, when the team clicks 'pending
reply,' then a reply box opens and submitting it updates the status
immediately."*

**Already automated:** `test_p0_6_replying_to_a_pending_review_updates_status_immediately`
proves the API side — `POST /api/reviews/{id}/reply` marks the review
replied and the Reviews listing's `pendingReplies` count drops
immediately, in the same request/response cycle.

**What only a browser can confirm:** open the Reviews tab, find a
listing with a pending reply, click it, and confirm a reply box/modal
genuinely appears (not just that the count would be correct if it did).
Submit it and confirm the pending-reply indicator updates on screen
without a manual page refresh.

**Steps:**
1. Log in, open the Reviews tab.
2. Find a row with `pendingReplies > 0`; click into the pending reply.
3. Confirm a reply input/box opens.
4. Submit a reply.
5. Confirm the row's pending-reply indicator updates immediately, with
   no page reload.

---

## 2. Overview is the actual landing view (P0-7)

**PRD:** *"Given the team opens the dashboard, then the Overview tab
loads by default showing health score, volume trend, and outstanding
alerts without further navigation."*

**Already automated:** `test_p0_7_overview_returns_health_score_volume_trend_and_alerts_together`
proves `GET /api/overview` (+ `GET /api/overview/trend`, 8.1) returns
everything that view needs — health score (`clarityIndex`), volume trend
(`totalMentions.deltaPct`), and outstanding alerts (`activeAlerts`) — in
one round trip. Code inspection also confirms `remedy-pulse-mockup.html`'s
nav has `class="active"` hardcoded on the Overview tab's `<a>`, and
`initApp()` populates `STATE.overview` before rendering anything else.

**What only a browser can confirm:** that a *fresh* page load (a new
tab, or a hard refresh, logged in) genuinely lands on Overview with
score/trend/alerts visible, with zero clicks — not some other tab
reopening from a remembered state, and not a blank/loading flash that
never resolves.

**Steps:**
1. Close the app entirely; open it fresh (new tab or hard refresh),
   logged in with live data available.
2. Without clicking anything, confirm the Overview tab is what's shown.
3. Confirm the Clarity Index score, a volume trend, and the alerts
   summary are all visibly populated (not stuck on a loading state).

---

## 3. "Last synced" is visible from every tab (P0-11)

**PRD:** *"Given data was last refreshed at time T, when the team views
any tab, then T is visible and updates after a successful sync."*

**Already automated:** `test_p0_11_last_synced_reflects_the_most_recent_successful_run`
proves the data side — `GET /api/status` and `GET /api/overview`'s
`lastSyncedAt` both reflect a real run-ledger (`ingestion_runs`) row.
Code inspection also confirms `remedy-pulse-mockup.html`'s `#syncPill`
lives in `<header>`, outside every per-tab `<section class="view">`, so
the same element renders unconditionally regardless of which tab is
active.

**What only a browser can confirm:** that the sync pill is actually
*visible on screen* (not, say, clipped or hidden by a layout bug) while
each of the six tabs is the active one, and that its timestamp visibly
changes after triggering a real sync.

**Steps:**
1. Click through all six tabs (Overview, Competitors, Mentions, Reviews,
   Topics, EMV); confirm the "Last synced …" pill is visible in the
   header on every one of them, not just Overview.
2. Trigger (or wait for) a real ingestion run to complete.
3. Confirm the pill's timestamp visibly updates afterward, on whichever
   tab is currently open.

---

## Sign-off

Record the date, who ran this, and which of the three items passed as
written (not "looked probably fine") before treating 9.1 as fully closed
for launch purposes:

| Item | Date | Run by | Result |
|---|---|---|---|
| 1. Reviews reply box | | | |
| 2. Overview default landing | | | |
| 3. Last synced on every tab | | | |
