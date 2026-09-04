# Decision: sentiment classification method, model choice, and the precision/recall bar (6.1)

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach, a model
choice, and a concrete accuracy bar for the team to review and decide on; nothing here has
been agreed or implemented as policy. It is a technical recommendation for how to build the
classifier — it is explicitly **not** a unilateral decision about how much classification
error the business is willing to accept before trusting the alert workflow unsupervised.
That threshold is a product/risk call (the PRD names it an "engineering" open question, but
the actual number is a business judgment about acceptable missed-crisis risk vs. reviewer
workload) — this document proposes a number and the reasoning behind it, for the team to
ratify, adjust, or reject. It covers checklist item 6.1.

## Decision

**A hosted LLM (Claude), called once per item, batched.** Every classification stores both
the raw text (already on `Mention.text`) and the model's output (`sentiment`,
`sentiment_confidence`, `alert_category`, plus a short `reasoning` string) so re-scoring
against a newer model or a corrected prompt is always possible later without re-fetching
anything. This is the checklist's own stated "lean" for 6.1, adopted largely as-is — see
`backend/app/classification.py` for the implementation and its module docstring for how it
also resolves 6.2 (the two conflicting definitions of `Mention.sentiment`) and 6.3 (the
crisis/digest routing rules, implemented verbatim from the mockup's
`openAlertRulesModal()`, citing spec §9.2).

**Model: `claude-opus-5`** — this project's standing default per the `claude-api` skill's
explicit, non-negotiable policy ("ALWAYS use `claude-opus-5` unless the user explicitly
names a different model... never downgrade for cost — that's the user's decision, not
yours"). An earlier draft of this document picked `claude-sonnet-5` on a self-directed
cost/quality tradeoff — exactly the kind of default-model downgrade that policy rules out —
and was corrected on review before this was ever wired into `classification.py`. See "Model
tier" below for the cost table in case the team later wants to make that downgrade call
explicitly (it's a real, available option — it just isn't the default).

## Options considered

- **Hosted LLM per item (chosen).** Best quality on Taglish, code-switching, and sarcasm —
  the checklist's own framing of where lexicon-based sentiment models fail hardest on PH
  social content, and where the actual accuracy in this product lives. Costs per-item money
  and adds a latency/availability dependency to classification — both negligible at this
  project's stated volume (see Reasoning below).
- **Off-the-shelf multilingual sentiment model, self-hosted.** Free per item, predictable,
  offline-capable. Rejected for the same reason the checklist's own "YOUR CALL" rejects it:
  noticeably worse on mixed Tagalog/English and on short, star-rating-free text (Reddit
  comments, forum posts) — exactly the inputs this project needs classified well, and
  exactly the case where a lexicon/off-the-shelf model's errors would concentrate.
- **Whatever the vendor provides**, if the still-open Phase 1 vendor decision lands on a
  platform like Awario or Brand24. Rejected for now: it would mean inheriting that vendor's
  definition of "negative" with no ability to tune it against the precision/recall bar this
  document proposes below — the PRD explicitly asks for a tunable bar, and an opaque
  vendor classifier forecloses that. Revisit if the vendor decision lands somewhere with
  both a tunable sentiment score **and** an exportable confidence value (see "What would
  change this").

### Model tier: why Opus is the right default here anyway, not just the mandated one

Even setting the skill's policy aside, Opus is a defensible pick on the merits for this
specific task: this is exactly the kind of qualitative, judgment-heavy classification
(patient safety vs. routine complaint, high-velocity pile-on vs. low-level grumbling,
sarcasm in code-switched Taglish, a 10-condition crisis/digest routing call made in the
same pass) that benefits from the strongest available model — and it's the tier the alert
workflow's core safety property (not missing a real crisis) rests on. The policy and the
task-fit reasoning point the same direction here; this isn't a case of the mandate
overriding what the task would otherwise want.

### Cost, for context (not the deciding factor)

The mockup's own numbers put volume at a few hundred items a week — call it ~2,000/month.
Per item: a system prompt of roughly 500-650 tokens (the five-plus-five routing rules,
fixed and cacheable) plus the item's own text, and an output of roughly 100-200 tokens
(the JSON result plus a short `reasoning` string). At that volume:

| Model | Input $/1M | Output $/1M | Rough monthly cost (~2,000 items) |
|---|---|---|---|
| Claude Haiku 4.5 | $1.00 | $5.00 | ~$2-3 |
| Claude Sonnet 5 | $2.00 | $10.00 | ~$5-6 |
| **Claude Opus 5 (default)** | $5.00 | $25.00 | ~$13-15 |

All three are single-digit-to-low-double-digit dollars a month — the gap between tiers
doesn't meaningfully move this project's budget either way. If the team later wants a
cheaper tier for cost reasons, that's a legitimate call to make explicitly (see "What
would change this") — just not one this document makes by default.

## The precision/recall bar (the PRD's open question)

> *"what precision/recall bar is acceptable before it's trusted to drive the alert workflow
> unsupervised?"*

### Which error direction actually matters

Two error directions exist, and they are not equally costly for this product:

- **A false negative on the NEGATIVE class** (or, more specifically, on the "crisis"
  `alert_category`): the classifier reads a genuinely negative, potentially crisis-worthy
  item — a patient-safety complaint, a legal threat, a pile-on thread — as Neutral/Positive
  or routes it to Digest. This is **silent failure**: nobody on the team knows to go look,
  because nothing told them to. This is the exact failure mode the PRD's core v1 metric
  (median time from a negative mention appearing to being assigned) can't even measure,
  because the item never entered the alert workflow at all.
- **A false positive on the NEGATIVE class** (or on "crisis"): a Neutral/Positive or
  low-stakes item gets wrongly flagged Negative, or wrongly escalated to Crisis. This costs
  someone a few minutes reviewing and dismissing a non-issue. Annoying, and worth bounding —
  enough false alarms and the team starts ignoring the alert channel, which is its own real
  failure mode — but recoverable, and it fails loud (a human sees it and can tell it was
  wrong) rather than silent.

That asymmetry — a missed crisis is unrecoverable-by-definition until someone finds it some
other way, a false alarm is merely wasteful — is why **recall on the NEGATIVE class, and
recall on the "crisis" `alert_category` specifically, is the primary bar**, with precision
as a secondary bound so the alert queue stays trustworthy enough that people keep reading
it. (Note: the checklist's own phrasing of this item names "a minimum precision target …
since a false negative … lets a real crisis go unrouted" — precision and recall are being
used loosely there. The metric that directly measures "how many real negatives did we miss"
is recall, not precision; this document proposes recall as the primary bar for exactly that
reason, with precision retained as the secondary, noise-control bar.)

### Proposed concrete bar

- **Recall on the NEGATIVE class ≥ 90%** — no more than 1 in 10 truly negative items should
  be missed (classified Positive/Neutral) by the classifier.
- **Recall on `alert_category = "crisis"` ≥ 95%**, measured specifically on the subset of
  items a human reviewer would independently mark crisis-worthy (patient safety, legal/
  regulatory, mainstream media, founder/doctor reputation, high-velocity pile-on). This
  subset is smaller and higher-stakes than "negative" in general, so it gets the higher bar.
- **Precision on the NEGATIVE class ≥ 75%** and **precision on `alert_category = "crisis"`
  ≥ 70%** as the secondary, noise-control bars — loose enough that occasional over-flagging
  doesn't force a rewrite, tight enough that the crisis channel doesn't drown in false
  alarms.

Until these are measured and met, **this classifier should not be trusted to route items
without a human safety net** — in practice, that safety net is the workflow the PRD and
Phase 3 already build regardless (every alert still goes through assign/resolve), so the
practical effect of "not yet trusted unsupervised" is: someone should periodically spot-check
Digest-routed items for missed crises (a false negative, by definition, never surfaces on
its own), not that the alert workflow stops functioning until the bar is proven.

### How this would actually get measured

**No labeled validation set exists yet.** There is no prior human-reviewed corpus of
Remedy Pulse mentions/reviews with a trusted sentiment/crisis label to test against — this
is stated plainly rather than assumed away. Proposed path to build one:

1. `classify_and_store()` already writes `sentiment_confidence` and a `reasoning` string
   alongside every classification — this is the audit trail a future validation pass reads,
   not just a debugging nicety.
2. As classified items flow through the existing assign/resolve workflow (3.2/3.3), add a
   lightweight "was this classified/routed correctly?" signal at resolution time — a human
   who worked the item is already the best-positioned person to confirm or correct the
   sentiment and crisis/digest call they just acted on. (This is a Phase 7/UI concern to
   wire up, not something this module builds — noted here so the validation path has a
   concrete source, not a hand-wave.)
3. Once a few hundred human-confirmed labels exist (roughly one to two months of real
   volume at this project's stated scale), compute recall/precision on the NEGATIVE class
   and on `alert_category = "crisis"` against that sample, and compare against the bars
   above.
4. If the bar isn't met on `claude-opus-5` (already the strongest available tier): the
   lever is re-prompting/re-tuning the prompt and taxonomy, not moving to a bigger model —
   there isn't a bigger one to move to. Only after re-tuning fails would the underlying
   approach itself (LLM vs. something else) be worth reopening.

## What would change this

- If the still-open Phase 1 vendor decision lands on a platform with both a tunable
  sentiment score and an exportable confidence value, that inherited classifier may be
  worth adopting instead of this one — see "Options considered" above.
- If ingestion volume grows well past "a few hundred items a week" (e.g. the roadmap's
  multi-brand P2 lands) **and** cost becomes a real, explicit constraint someone raises,
  that's the trigger to revisit a cheaper tier (Sonnet or Haiku, re-evaluated against the
  same recall bar) — an explicit team decision at that point, not a default this document
  sets now.
- If the team decides the proposed recall/precision numbers above are wrong for the actual
  business risk they're willing to carry, that's exactly the ratification this document is
  asking for — the numbers here are a starting proposal, not a claim of authority over the
  team's risk tolerance.
