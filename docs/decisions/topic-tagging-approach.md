# Decision: topic tagging, not topic clustering (6.5)

## Decision

The checklist titles item 6.5 **"Topic clustering."** What's built here (`backend/app/topic_tagging.py`) is deliberately a smaller, more honest thing: **LLM-based topic TAGGING against the mockup's existing fixed five-topic taxonomy**, not true unsupervised clustering.

Concretely:

- `TOPIC_TAXONOMY` is the five topic keys and labels already hardcoded in `remedy-pulse-mockup.html`'s `topicMentions` object (`facial-results`, `staff-service`, `rejuran`, `pricing`, `booking`), copied verbatim — not rediscovered from data.
- `tag_topics(text)` makes one Claude API call per item and asks the model to classify that item against the known list, returning zero, one, or several matching keys (or `[]` if none apply).
- The taxonomy is an **input** to the tagging function, not an **output** of it. Real clustering would be the reverse: the topics themselves would be discovered by analyzing the corpus, and the fixed five-item list wouldn't exist as a concept at all.

## Reasoning

True unsupervised topic clustering — discovering topics from the data itself, rather than classifying against a predefined list — needs real data volume to produce clusters that are meaningful and checkable. This project does not have that yet:

- Every Phase 4 ingestion adapter built so far is, per Phase 4/5's own status notes, either not live-verified against a real account/API yet, or blocked on external access that hasn't landed (Google Business Profile approval, Reddit's elevated Data Access tier, Meta App Review capabilities — see `docs/decisions/reddit-integration-status.md` and the adapters' own module docstrings for the specifics).
- With effectively zero real mentions flowing into the `mentions` table today, there is no corpus to cluster. Building a clustering pipeline (embeddings, a clustering algorithm, cluster-labeling) against zero real data would produce clusters nobody can evaluate — there's nothing to check them against, and no way to tell a genuinely discovered theme from an artifact of whatever placeholder/sample data happened to be used to build it. That's a worse outcome than not building it yet: it looks finished, and isn't trustworthy.
- The mockup itself already tells us what "done" should eventually look like on the UI side — five themed cards with per-topic sentiment splits (`openTopicModal()`) — but that's a fixed, small, human-curated list, not evidence that clustering was ever meant to run live yet either. It reads as a demonstration of the target shape, not a spec for how the topics were derived.

Scoping down to tagging-against-a-fixed-list is a real, working, testable version of "the Topics tab has content" (the checklist's own stated skip-risk for 6.5) that can ship now, rather than a clustering pipeline that would be untestable and probably wrong against the data this project actually has.

## What this does NOT close off

This scoping choice is not a dead end for real clustering later:

- **`Mention.topics` needed no schema change.** It's already a nullable JSON list-of-strings column (Phase 2), designed loosely enough to hold either "the fixed keys `tag_topics()` currently returns" or "whatever labels a future clustering pass discovers" — the column doesn't encode or assume a fixed taxonomy.
- **Migrating from tagging to clustering later is a different tagging *function*, not a migration.** `tag_and_store()`/`tag_untagged_batch()`'s contracts (load a `Mention`, decide topics for its text, write a list onto `Mention.topics`) stay the same whether the "decide topics" step is "classify against `TOPIC_TAXONOMY`" (today) or "look up this item's assigned cluster from a clustering pass over the whole corpus" (later). No `Mention` rows need to be touched, no downstream reader of `Mention.topics` needs to change, to make that swap.
- **Real clustering becomes viable once there's enough live data to validate against** — once at least one adapter is live-verified and ingesting a real, sustained volume of mentions, re-open this decision and evaluate clustering properly, with a real corpus to check candidate clusters against.

## Options considered

- **Build real unsupervised clustering now** (embed each mention's text, cluster with something like HDBSCAN/k-means, label clusters, possibly with an LLM). Rejected for now — see Reasoning above. This is the checklist's literal 6.5 title, but building it against no real data produces an untestable, probably-wrong result; the "Effort: L·risky" the checklist itself assigns to 6.5 already flags this as the riskier of the two shapes.
- **LLM-based tagging against a fixed taxonomy (recommended, implemented here).** Smaller, honestly scoped, testable today (mock the LLM call, assert the taxonomy match/no-match logic — see `backend/tests/test_topic_tagging.py`), and gives the Topics tab real content to render instead of the mockup's hardcoded sample mentions. The taxonomy itself is a known limitation, not a hidden one: any real mention topic outside the five listed keys is invisible until the taxonomy is revisited by hand or real clustering replaces it.
- **Do nothing until real data arrives.** Leaves the Topics tab with no content at all — the checklist's own named skip-risk for 6.5. Rejected: tagging-against-a-fixed-list is buildable and testable today without waiting on ingestion volume that may be weeks away, and it's a strict subset of what a future clustering pass would also need to exist (a place on `Mention` to write topic results).

## What would change this

- **At least one Phase 4 adapter going live-verified with sustained real ingestion volume** — the moment there's a real corpus of a few hundred-plus mentions, clustering stops being untestable and this decision should be revisited.
- **The fixed five-topic taxonomy proving too narrow in practice** (real mentions clustering around a theme none of the five keys cover) — that's itself a signal clustering is worth building, since a human maintaining the taxonomy by hand won't keep pace with what an actual corpus talks about.
- **`classification.py` (Phase 6's sentiment classifier, built in parallel with this batch) landing with an error-handling pattern for missing-API-key / malformed-LLM-response that differs from this module's** — see `topic_tagging.py`'s own module docstring: it didn't exist when this module was written, so `tag_topics()`'s error handling (return `[]` + log a warning, never raise, for both cases) is this module's own best-judgment choice, not copied from an established pattern. `classification.py` as it exists now raises a `ClassifierNotConfiguredError` on a missing key instead. A later reconciliation pass should align the two rather than leaving two different failure-handling conventions for what is, from a caller's perspective, the same kind of LLM-classification call.
