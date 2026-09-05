# Decision record: formal MediaWatch decommission decision (9.7)

**Status:** RECOMMENDATION — not yet ratified, and not a decision this session is positioned to make unilaterally. Canceling a paid vendor tool is a business/contract decision. This document does not decommission MediaWatch, and does not set a calendar date for doing so; it documents the decision that needs to be made, names exactly what it's gated on, and records why an unstructured version of this decision is risky — so it doesn't get lost the way the checklist's own skip-risk note warns it could ("Paying for both indefinitely; the stated goal never lands"). It covers checklist item 9.7, and its direct prerequisite, item 9.6.

## Context, verified in this session

- `README.md:3` describes Remedy Pulse's purpose as "replacing Media Meter/MediaWatch with a single view of what people are saying online."
- `remedy-pulse-prd.md:11` states the current problem: Remedy relies on Media Meter/MediaWatch, which "only surfaces traditional press coverage and gives no visibility into social conversation," and that gap is the reason Remedy Pulse exists.
- `remedy-pulse-prd.md:19` lists as a P0 goal: "Fully replace Media Meter/MediaWatch as the team's day-to-day source of truth for brand monitoring."
- `remedy-pulse-prd.md:117`, under Lagging indicators in Success Metrics: "Media Meter/MediaWatch fully decommissioned as the team's primary tool within one quarter of launch."
- `remedy-pulse-roadmap.md:45` lists a "Media Meter/MediaWatch cutover" line item: "Formal sunset of the old tool once Remedy Pulse is trusted," Status: Not Started, Owner: **Leadership** (a role, not a named individual), Dependencies: "30 days of stable use post-launch (per PRD success metrics)."
- Checklist item 9.6, the direct prerequisite to this decision: "Run Remedy Pulse in parallel with Media Meter/MediaWatch for 30 days... The PRD's own success metric gates decommissioning on 30 days of stable use." As of this session, 9.6 is unchecked — the 30-day parallel run has not happened, because Remedy Pulse itself has not launched yet (Phase 8's tabs and Phase 9's QA/cutover are still open per the checklist).

Two things are worth being precise about, since both matter to the reasoning below: the PRD sets a **quarter-long outer bound** ("within one quarter of launch") for full decommissioning as a lagging success metric, while the roadmap's cutover line item names a **30-day parallel-run threshold** as the trigger condition. These are not the same number, and neither, on its own, is a decommission decision — the quarter is a deadline by which decommissioning should have happened if things go well, and the 30 days is the minimum evidence window before it's even considered. Neither the PRD nor the roadmap describes who, concretely, reviews the 30-day parallel-run results and says "yes, cut it over" versus "no, keep both running."

## The decision to recommend

**Do not decommission MediaWatch until both of the following are true:**

1. The 30-day parallel run (checklist item 9.6) has actually completed — not just elapsed, but been run and evaluated against the criteria that make it meaningful (see Reasoning below); and
2. A named person, with the authority to cancel or continue the MediaWatch contract, has formally signed off on the cutover.

This is a two-part gate — a time-boxed trial *and* an explicit human decision — not a decision that a calendar date alone can make. Reaching day 30 is necessary but not sufficient; reaching day 30 without anyone actually reviewing what happened during those 30 days and saying "yes, cut it over" is not a decommission decision, it's just the passage of time.

## Options considered

**A. Decommission immediately at Remedy Pulse's launch.**
Rejected. This directly contradicts the PRD's own stated 30-day parallel-run success metric (`remedy-pulse-roadmap.md:45`) and risks losing the fallback tool before the new one is proven — if Remedy Pulse has a launch-week gap (a source that isn't live yet, a metric that doesn't compute correctly against real data), the team would have no monitoring tool of any kind during exactly the period when the new tool is least tested.

**B. Run both indefinitely with no decommission trigger.**
Rejected. This is precisely the skip risk the checklist names for item 9.7: "Paying for both indefinitely; the stated goal never lands." A plan with no trigger date and no named decision-owner is not a decision — it's an open-ended cost with no mechanism to ever close it out, and the PRD's own P0 goal ("fully replace Media Meter/MediaWatch") never actually gets reached even after the new tool works.

**C. The recommended two-part gate: 30-day parallel run completed and evaluated, plus a named sign-off (recommended).**
This is the option that actually operationalizes what the PRD and roadmap already state, rather than either jumping ahead of them (Option A) or leaving them as an intention with no mechanism (Option B).

## Reasoning

This decision is downstream of 9.6, and depends on 9.6 actually happening in substance, not just in name. The parallel run has to be *evaluated*, not merely elapsed — someone needs to check, at the end of the 30 days, that Remedy Pulse actually delivered what MediaWatch was delivering (traditional press coverage, per `remedy-pulse-prd.md:11`) plus the social-conversation visibility it was built to add, and that nothing MediaWatch was catching silently stopped being caught. A parallel run that nobody reviews at the end is functionally identical to Option B with an extra 30 days attached.

The second half — a named person with authority to make the final call — is a real fact about how Remedy operates, and this document does not have access to it and cannot invent it. The roadmap already names "Leadership" as the owner of the MediaWatch cutover line item, but that is a role, not a person, which leaves the same kind of gap `docs/decisions/10-assignment-roster.md` flags for a different question — "who is responsible for keeping the roster current" — where that document explicitly declines to invent an answer rather than guess at one ("there is no way to know from this codebase who that person should be... This needs a real person assigned before the roster can be trusted to stay accurate"). The same applies here: this document recommends that a specific named individual (not just "Leadership" as a role) be assigned owner of the go/no-go call on MediaWatch decommissioning, the same way the vendor/build path (checklist item 1.4) is separately flagged as needing "a named owner and a dated deadline." Until that person is named, the 30-day parallel run has no one accountable for closing the loop at the end of it, which is exactly how Option B happens by default even when nobody intends it.

## What would change this

If the 30-day parallel run surfaces a P0 gap that Remedy Pulse cannot yet cover — a source that's silently missing, a metric that's materially wrong, a workflow step the team can't complete without MediaWatch — the correct response is to **extend the parallel-run window**, not to decommission on the original schedule anyway. The gate here is about demonstrated stability, not elapsed calendar time; if 30 days of real use shows the new tool isn't yet a full replacement, the PRD's own goal ("fully replace Media Meter/MediaWatch") isn't served by cutting over on schedule regardless. Revisit the trigger date, not the requirement.
