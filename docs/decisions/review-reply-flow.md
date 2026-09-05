# Decision record: does "Send reply" post to Google, or deep-link out to Business Profile? (8.5 HEADS-UP)

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on. It covers checklist item 8.5's HEADS-UP block.

## The decision to recommend

**Deep-link the user out to Google Business Profile's own reply UI for v1.** "Send reply" in Remedy Pulse should not post the reply text to Google programmatically; it should take the user to the specific review on Business Profile (or the Business Profile reply surface generally, if a direct per-review deep link isn't available) so a human posts the reply themselves, in their own words, through Google's own interface.

This matches the direction the checklist's own framing already leans toward ("the second is dramatically cheaper and lower-risk for v1"), and it matches what is already built: `backend/app/api/routes/reviews.py`'s `POST /api/reviews/{mention_id}/reply` endpoint already does not post to Google — it sets `mention.has_reply = True` and returns the updated per-branch listing aggregate, nothing more. `docs/api-contract.md` documents this explicitly:

> "Marks that one review's `has_reply = true` in the database (this API does **not** post a real reply to Google — that would need the Business Profile write scope, out of scope here) and logs nothing further beyond the row update."

and again, in the contract's list of what v1 deliberately does not do:

> "Writing back to Google/Reddit/Meta (the reply endpoint only updates this system's own copy)."

This decision doc ratifies that existing implementation choice going forward rather than proposing a change to it — it exists to make the reasoning explicit and recorded, since the checklist's HEADS-UP asked for it to be "worth recording in `docs/decisions/` either way."

Note on scope: this session made no changes to `backend/` or `remedy-pulse-mockup.html` — both were read only, per this task's boundaries. `sendReply()` in the mockup is not wired to this endpoint at all yet; that wiring gap is documented separately, in the mockup's own fetch-layer comments (`remedy-pulse-mockup.html`, near line 1039), as a distinct, deliberate, out-of-scope gap — a mismatch between the mockup's branch-level "N pending replies" reply flow and the API's per-review `mention_id` endpoint. This document does not attempt to resolve that wiring gap; it only settles which direction ("post" vs. "deep-link") the eventual wiring should point.

## Options considered

**A. Post the reply via the Google Business Profile API directly.**
Pros: a fully in-product workflow — the marketing team never leaves Remedy Pulse to close out a review.
Cons: this is an irreversible public action, taken programmatically in the clinic's name, on an API surface (`accounts/*/locations/*/reviews/*/reply`) that sits behind the *same* gated Business Profile access as review reads — access that Phase 1 (item 1.1) has not yet confirmed is even granted. Concretely, this can't be built at all right now regardless of preference, and even once read access lands, reply-*write* access is a separate, higher grant that would need to be requested and approved on its own.

**B. Deep-link out to Google Business Profile's own reply UI (recommended).**
Pros: matches the PRD's own stated Non-Goal principle (quoted exactly below) — the tool surfaces and routes, a human acts; requires zero additional API scope or approval; buildable today, since it only needs a URL construction, not a write-scoped credential.
Cons: leaves the reply workflow context-switched — the user leaves Remedy Pulse to actually post — and there is no in-product record that a reply was sent unless the user *also* takes an action back in Remedy Pulse to mark it done. That second half is already covered: `POST /api/reviews/{mention_id}/reply`'s existing local-only "mark `has_reply`" design is exactly that confirmation step, already built.

## Reasoning

The PRD's Non-Goals section states, verbatim:

> "**Automated review/mention replies.** The tool surfaces and routes items for a human to act on; it will not draft or send responses on the team's behalf in v1 — reduces liability risk and matches what the validated mockup already scoped."

(`remedy-pulse-prd.md`, Non-Goals, verified directly in this session.)

Option A — posting via the API — is in direct tension with that stated Non-Goal: an API-posted reply is a response sent by the tool, on the team's behalf, without a human's own hand on the final "post" action inside Google's own interface. Option B is a literal reading of "surfaces and routes items for a human to act on": Remedy Pulse surfaces which reviews are pending and routes the user to exactly where they need to go to act — Google's own reply surface — and the human does the acting.

There's a second, independent reason to prefer Option B beyond the Non-Goal principle: posting via the API requires Business Profile *write* scope, which is a higher, separate permission bar than the *read* scope Phase 1 (item 1.1) is already chasing with no confirmed grant and no SLA (per the checklist's own HEADS-UP on 1.1: "Business Profile API access has no SLA and is commonly rejected on the first submission"). Recommending Option A would mean building a v1 workflow around a permission tier that (a) doesn't exist yet even in its lower form, and (b) would need its *own* separate approval on top of that, compounding an already-uncertain dependency with a second one. Option B avoids that entirely — it needs no Google API scope beyond whatever minimal review-identifying data (e.g., a Business Profile review URL or location ID) is already available from the read path once that lands.

## What would change this

If the team specifically negotiates Business Profile **write** access as part of the same Business Profile approval Phase 1 (item 1.1) is already pursuing — not assumed, but explicitly requested and confirmed granted — and separately decides that the fully in-product workflow is worth taking on that additional scope and the liability/reversibility risk of an automated public post in the clinic's name, revisit this decision in favor of Option A. Absent both of those (the scope actually being granted, and a deliberate team decision to accept the added risk), Option B stands.
