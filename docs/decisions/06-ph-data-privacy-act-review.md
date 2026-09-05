# Decision record: PH Data Privacy Act review is blocked on a spec document that does not exist in this repo

**Status:** RECOMMENDATION — not a completed compliance review. This document does not perform the legal review it describes; it documents the gap blocking that review and what the review must cover once unblocked. It covers checklist item 5.4.

## The gap, verified directly in this session

`fetch_owned_reviews.py`'s `mask_reviewer_name()` justifies its behavior by citing a specific section of a specific named document:

```python
def mask_reviewer_name(display_name):
    # Per spec §11 (PH Data Privacy Act) — minimize storage of personal
    # identifiers. Keep first name / initial only rather than full name.
```
(`backend/fetch_owned_reviews.py:168-170`)

That document — referred to throughout the codebase as `RemedyPulseSpec_1` — was searched for directly rather than taking the checklist's claim on faith: `grep -rni "RemedyPulseSpec"` across the entire repository (all file types, excluding `.git`) returns exactly one hit that isn't the checklist itself describing this gap:

```
remedy-pulse-mockup.html:982:  <span>Spec ref: RemedyPulseSpec_1 · v0.1 draft</span>
```

That is a footer label, not a document. There is no file named anything resembling `RemedyPulseSpec_1` anywhere in this repository, in `docs/`, or referenced by path from any code comment. **Verified: the document this code cites as its legal justification does not exist anywhere a person working from this repo can read it.** This is not a repetition of the checklist's assertion — it is independently confirmed by grep in this session.

## Every place that cites the document by name or section number

Searched with `grep -rni "§5\.5|§6\.3|§6\.4|§9\.2|§10\b|§11\b|§18\b"` and `grep -rni "RemedyPulseSpec"` across the whole repository. Full list, file:line:

| File : Line | What it cites | Context |
|---|---|---|
| `remedy-pulse-mockup.html:982` | `RemedyPulseSpec_1` (by name) | Footer: "Spec ref: RemedyPulseSpec_1 · v0.1 draft" |
| `remedy-pulse-mockup.html:670` | §10 | Card-sub text: "supports the KOL identification feature in §10 of the spec" |
| `remedy-pulse-mockup.html:683` | §18 | Card-sub text: "New tracked entities per Marketing's §18 update" |
| `remedy-pulse-mockup.html:878` | §6.3 | EMV tab description: "rate card updated to Media Meter's published card per Gian's §6.3 note" |
| `remedy-pulse-mockup.html:890` | §6.3 | Rate Card card-sub: "the Media Meter card Gian attached to §6.3" |
| `remedy-pulse-mockup.html:934` | §6.4 | PeopleAsia EMV row detail: "this replaces the §6.4 worked example, which was tuned to the old illustrative Tier 2 base" |
| `remedy-pulse-mockup.html:970` | §5.5 | ANC EMV row detail: "Online capture only, per §5.5" |
| `remedy-pulse-mockup.html:1238` | §9.2 | Alert-rules modal: "Per Gian's update to §9.2 — routes each item to a Crisis Alert (immediate) or the Daily Digest" |
| `backend/fetch_owned_reviews.py:169` | §11 | Comment inside `mask_reviewer_name()`: "Per spec §11 (PH Data Privacy Act)" |
| `backend/README.md:87` | §11 | Documentation of `reviews_raw.json`'s output shape: "masked to first-name-plus-initial per the PH Data Privacy Act note in §11 of the spec" |
| `backend/config.py:78` | §6.3 | Comment on `OUTLET_TIER_MAP`: "Gian/Marketing's call per the PRD's §6.3 note, not an engineering one" |

Two observations worth flagging, not resolving, since resolving them requires the document itself:

1. **`§6.3` is cited three times, from two different code locations, with two subtly different attributions** — the mockup (lines 878, 890) attributes it to "Gian's §6.3 note" about the Media Meter rate card; `backend/config.py:78` attributes the same section number to "the PRD's §6.3 note" about which outlet tier is a business judgment call. Whether these are the same §6.3 covering both topics, two different documents both numbered coincidentally, or a drift between what different people believed the spec said, is unanswerable without the document.
2. **`docs/implementation-checklist.md`** also references `RemedyPulseSpec_1` and several of the same section numbers (§§5.5, 6.3, 6.4, 9.2, 10, 18, 11) — but only in the course of describing this exact gap (items 5.4, 6.3, 8.7). It is not a citation of the spec as an authority; it is documentation of the same problem this record covers, and is not counted as a separate citation above.

## What a real PH Data Privacy Act compliance review needs to cover

This document does not perform that review. It is not a legal review, this session has no access to the actual spec, and no legal qualification to conduct one — producing compliance conclusions without the source document would be fabrication, and fabricated compliance conclusions are worse than an honest gap. What follows is a checklist of what the review must address once `RemedyPulseSpec_1` (or a legally-reviewed replacement) actually exists and can be read:

- **Retention period for personal data.** How long reviewer names, commenter handles, and any other personal identifiers may be stored before deletion or further anonymization is required — today, nothing in this repo defines a retention period for any personal data field, masked or not.
- **Lawful basis for processing reviewer/commenter names.** What legal basis under the PH Data Privacy Act (consent, legitimate interest, or another basis) justifies collecting and storing names/handles from Google reviews, and eventually Reddit, Instagram, and Facebook — `mask_reviewer_name()`'s masking is a minimization technique, not itself a lawful basis, and no lawful basis is documented anywhere in this repo today.
- **Data-subject access and deletion request handling.** Whether and how a reviewer or commenter can request to see or delete their own data as stored by Remedy Pulse, separate from the Reddit-specific 48-hour deletion-propagation obligation already covered in `docs/decisions/03-reddit-deletion-propagation.md` (which is a Reddit platform-policy commitment, not a PH Data Privacy Act data-subject-rights mechanism — the two are related but distinct obligations and the review needs to treat them as such).
- **Cross-border storage/processor considerations if hosting isn't in the Philippines.** The hosting/vendor decision (checklist item 1.4) is still open and unresolved; if the eventual hosting platform or any third-party processor (a sentiment classifier vendor, a hosted LLM provider per Phase 6's open question, a secrets manager per `docs/decisions/08-secrets-at-rest.md`) stores or processes this personal data outside the Philippines, the Act's cross-border transfer requirements apply and need to be addressed specifically once that hosting decision lands.
- **Consistency across sources.** Checklist item 5.3 already flags that `mask_reviewer_name()`'s minimization exists only for Google reviewer names — Reddit usernames, Instagram handles, and Facebook commenter names have no equivalent treatment anywhere in the repo, and the mockup displays some of these in full (e.g., `@glowwithsab`, `u/skinseeker_mnl`). Whatever the real review concludes about lawful basis and retention needs to be applied uniformly, not source-by-source as an afterthought.

## Recommendation

**Get `RemedyPulseSpec_1` into `docs/` verbatim as the very next step.** Every other part of this review — retention, lawful basis, data-subject rights, cross-border handling — is blocked on having the actual document to read, not on more analysis of the gap. This document cannot substitute for that: it makes the gap and its blast radius explicit (eleven citations across four files — the mockup, `fetch_owned_reviews.py`, `backend/README.md`, and `backend/config.py` — one compliance-relevant behavior already shipped and justified by a section number nobody can check) and points at exactly what the review needs to address once the spec is available. It does not, and cannot, replace that review.
