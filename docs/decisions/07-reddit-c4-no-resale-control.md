# Decision record: the C-4 control — no resale, redistribution, or model training on Reddit data

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on; nothing here has been agreed or implemented. It covers checklist item 5.8 and requirement C-4.

## The commitment and its exact source

`docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` was read directly in this session (not just relied on via the checklist's paraphrase). The commitment appears under **"Role / nature of use,"** not under "Data handling" where the deletion/User-Agent commitments live:

> "Commercial developer / enterprise partner. This is a paid client engagement, not personal or academic use. Reddit data will be used internally to support a monitoring product built for a single business client; **it is not resold, redistributed, or used to train any model.**"

Signed by Angelo Mojica, `ai@remedy.ph`, in a submitted commercial Data Access Request.

Note the wording precisely: the checklist (item 5.8, and the requirement-coverage table's C-4 row) paraphrases this as *"no resale, redistribution, or model training on Reddit data"* — that paraphrase is accurate in substance but is not a verbatim quote of the PDF, whose actual sentence is the one block-quoted above. This document uses the verbatim text as the source of truth.

## The specific risk

This is a written commitment made to a third-party data provider (Reddit), not an internal aspiration — the same category of commitment as the 48-hour deletion propagation promise covered in `docs/decisions/03-reddit-deletion-propagation.md`. Today it exists only as a sentence in a submitted PDF. Nothing in the codebase checks, enforces, or even references it.

The concrete way this breaks, verified against what's actually in the repo:

- **P1-1 (AI weekly summary + regenerate) already exists in the mockup as a UI element with no real LLM behind it.** `remedy-pulse-mockup.html:472` wires a "Regenerate" button to `regenerateSummary()`; the function (`remedy-pulse-mockup.html:1991-2008`) cycles through a hardcoded array of exactly three canned strings (`summaries[]`, lines 1992-1996) with a fake opacity-fade delay — no network call, no model, no real input text of any kind today.
- **One of those three canned strings already references Reddit-sourced content**, as a preview of what the real feature is meant to do: `remedy-pulse-mockup.html:1994` — *"...a Reddit thread comparing Remedy to Aivee on Rejuran pricing is quietly gaining traction."* That is exactly the kind of sentence a real summarization feature would need to produce, and it can only produce it by having ingested Reddit content in its input.
- **The requirement-coverage table in `docs/implementation-checklist.md` (P1-1 row) confirms this is understood as future, unbuilt work** — "cycles 3 canned strings," "No" under Backend.

The risk is not that P1-1 is built carelessly today — it is that it isn't built at all yet, and the day someone wires "Regenerate" to a real LLM call, the natural, easiest implementation is to hand the model whatever mention text is on hand for the period being summarized. Once Reddit ingestion exists (checklist 4.3, C-1), that mention pool includes raw Reddit post/comment text by construction — the Mentions feed is explicitly cross-source (P0-1). An engineer doing that wiring has no reason to know that a signed commercial commitment to Reddit forbids using that specific slice of the input for model training or (arguably, depending on how "model training" is read against a live inference call) potentially even inference — because nothing in the code, the schema, or the PRD connects P1-1 to the Reddit PDF at all. The PRD lists Reddit only as a data source; the compliance commitment lives in a completely separate document that an engineer building a summary feature would have no reason to open.

## Recommended control

A data-source tag, checked before any content reaches an LLM call — not built in this pass, since this is a docs-only recommendation, but specified concretely enough to implement later:

- Every row that can feed an LLM prompt (mentions, reviews, articles — the same `Mention` table `backend/app/models.py` already defines, per Phase 2's schema work) carries a `source` field, which already exists per that schema (`source, external_id` is the table's own uniqueness key, per `docs/decisions/05-persistence-choice.md` and the Phase 2 status note in the checklist).
- Any code path that assembles text to send to an LLM (the eventual real implementation of `regenerateSummary()`, or any future feature that does the same thing) must filter or check that `source` field before the call is made, not after. Concretely: either (a) exclude rows where `source == "reddit"` from the assembled prompt entirely, or (b) require an explicit, separately-recorded compliance sign-off before Reddit-sourced rows are allowed into that code path — the equivalent of a feature flag or an assertion the LLM-calling code can't bypass by accident.
- The check belongs at the boundary where content enters the LLM call, not scattered across every place that reads mentions — a single guarded function (e.g., `assemble_summary_input()` or equivalent) that every future "feed the model" feature routes through, so the control exists in one place rather than needing to be remembered at every call site.
- Whichever mechanism is chosen, it should fail closed: if the tag is missing or unrecognized on a row, treat it as excluded rather than included, since the cost of accidentally omitting a non-Reddit mention from a summary is far lower than the cost of accidentally including Reddit content in a call that breaches a signed commitment.

This document recommends the mechanism; it does not build it. That is deliberate — this is a docs-only pass, and P1-1 has no real LLM wiring to attach a check to yet (per the checklist's own P1-1 row and the code cited above).

## What would change this

- **If the Reddit Data Access Request's terms are renegotiated** — e.g., if a future version of the access agreement permits model use of Reddit content under some condition — this control would need to be relaxed or removed to match the new terms exactly, not left stricter than what's actually agreed.
- **If P1-1 is scoped, when actually built, to never take raw mention text as input at all** (for example, if the "AI weekly summary" ends up built entirely from pre-aggregated metrics — Clarity Index, sentiment counts, EMV totals — rather than raw post/comment text), the control described here becomes moot for that feature specifically, since there would be no raw Reddit text in its input to guard against. It would still apply to any other future feature that does take raw mention text into a model call.
- **If the Reddit Data Access Request is confirmed denied** (checklist item 1.2, status currently unknown per `docs/decisions/04-reddit-integration-status.md`) — then there is no Reddit data in the store at all, and this entire control has nothing to guard; it would become unnecessary rather than merely lower-priority, unless a future request succeeds.
