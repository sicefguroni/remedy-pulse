<!-- artifact-url: https://claude.ai/code/artifact/15b4a597-89c2-464b-96ac-2ceb0546f342 -->

# Remedy Pulse — Implementation Checklist

**Generated:** 2026-09-03 · fresh run · **updated 2026-09-11**
**Sources:** `remedy-pulse-prd.md`, `remedy-pulse-roadmap.md`, `remedy-pulse-mockup.html`, `backend/`, `docs/Remedy Pulse_Reddit Data Access_Use Case.pdf`
**Progress:** 70 / 83 (up from 64/79 — 7.4, 8.5, 8.8, 0.23 closed; 5.6 moved from not-started to partially done; two more NEW items (0.21, 0.22) found and closed; one NEW item (0.24) found and flagged, left open)

**Changelog (2026-09-11):** Re-ran the test suite as ground truth (313 passed, 1 skipped, matching the document's existing claim; `ruff check` clean) before touching anything, confirming the rest of this document was accurate before making changes. Closed three items, all zero-payment engineering work (see each item's own status note for specifics): **8.8** (Competitors/Reviews alias tooltips — also caught and fixed one real regression in `exports.py`'s CSV writer), **7.4** and **8.5** together (the reviews reply-flow wiring gap both items pointed at — a new `POST /api/reviews/by-venue/{venue}/reply` endpoint plus the mockup's modal wired to it). While wiring that, found and closed **0.21 (NEW)**: a `SystemExit` inside two shared fetch functions (Google reviews token loading, GNews article search) could crash the entire scheduler process on a routine failure. Advanced **5.6** (secrets at rest) from not-started to partially done — `GOOGLE_TOKEN_JSON` as a production alternative to a bare `token.json` file. A dedicated second sweep specifically for more bugs of 0.21's shape then found and closed **0.22 (NEW)**: a Meta capability's unexpected fetch failure got no ledger row at all, not even an error one — and surfaced **0.23 (NEW)**: classification is fully implemented but nothing in production ever actually calls it, so real ingested mentions never get a sentiment. Built and closed **0.23** in a follow-up pass: a new `app/jobs/classification_job.py`, registered with a deliberately short 15-minute cadence tied to the PRD's 4-business-hour alert metric, verified with a real Groq API call (not just mocks). Doing that surfaced one more thing, flagged rather than silently decided: **0.24 (NEW)**, a `YOUR CALL` on whether the sync pill's `lastSyncedAt` should exclude non-external-data sources now that classification's fast cadence makes an existing quirk far more visible. Final state: 340 backend tests passing (up from 313), ruff clean, real-Postgres tests added for the new reply endpoint's ordering query, a genuinely aborted Postgres transaction proving the scheduler fix's fallback path, and the new classification job's ordering query, plus a real-browser (Playwright) pass of the alias tooltips and reply-modal flow with zero console errors. Everything else in this document is unchanged from the 2026-09-03/09-05 passes; no other item's status was re-verified false or newly discovered in this update.

---

## How to read this document

Two markers in here are worth slowing down for.

Before reading the answer under a **YOUR CALL**, predict the tradeoff yourself — what would you pick, and what would it cost you? The gap between your guess and the reasoning is where the learning is; skipping straight to the answer converts a decision into a fact you'll forget.

For every **HEADS-UP**, ask: *how would I have found this myself?* Some are findable by reading a vendor's docs carefully. Some are only findable by getting burned. Knowing which is which tells you where to be paranoid next time.

**UNDERSTAND FIRST** blocks are concepts, not facts. If the surrounding items look arbitrary, the concept is the missing piece.

Record each YOUR CALL in `docs/decisions/` once decided — the decision, the options, the reasoning, and *what would change your mind*. That last line is what turns a decision made into a decision understood.

---

## Timeline reality check — read this first

These are facts from the repo, not opinions about it:

| Fact | Evidence |
|---|---|
| Last code commit: **2026-08-04** | `git log --date=iso` → `25eedf2`, `99985bc`, both Aug 4 |
| PRD kickoff date: **2026-08-14** | `remedy-pulse-prd.md`, Timeline Considerations |
| Today: **2026-09-03** | ~3 weeks after kickoff |
| Code committed since kickoff: **none** | `git log` shows no commit after Aug 4 |
| Target v1 launch: **2026-09-30** | PRD, "Hard deadline" — **27 days away** |
| The roadmap's blocking item (vendor decision) | still listed **Blocked**, no owner deadline set |
| PRD + roadmap themselves | untracked in git (`git status` → both `??`) |

The roadmap budgets 6.5 weeks. About 3 of those are spent and the "Now" column is not started. **This checklist is larger than 27 days of work for two people.** That is not a reason to shrink the checklist — it is a reason to make the cut explicit. See *Sequencing* at the bottom for a recommended cut line; the roadmap already nominates Competitors as the first thing to drop, and the evidence here supports that.

---

## Current state — what was actually executed

Everything below marked VERIFIED was run in this session. Everything marked UNVERIFIED says why it could not be.

### VERIFIED

| Check | Result |
|---|---|
| Toolchain | Python 3.13.14, Node 24.13.1, git 2.53.0 — all present |
| `python -m py_compile` on all 4 backend files | Clean, no syntax errors |
| `pip install -r backend/requirements.txt` into a fresh venv | **Exit 0.** Resolves cleanly on Python 3.13 — 18 packages, no conflicts, no build failures |
| `python fetch_competitor_ratings.py` (no credentials) | Exit 1, `GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env and fill it in.` No files written |
| `python fetch_owned_reviews.py` (no credentials) | Exit 1, `No token file at './token.json'. Run oauth_setup.py first.` No files written |
| `python oauth_setup.py` (no credentials) | Exit 1, client-secrets-not-found with the Cloud Console path in the message |
| `node --check` on the mockup's extracted 774-line `<script>` block | Clean, parses without error |
| Mockup structure | Single 1729-line file, one `<style>`, one `<script>`, no build step. Only external resources are three Google Fonts families |
| Test suite | **None exists.** No test files, no `.github/`, no lint/type/CI config anywhere in the repo |

The three credential failures are **correct behaviour**, not defects — each script refuses to start rather than half-running, and each error message names the exact file and console page to fix it. That is better than most scaffolding. It is recorded here as a verified positive, not a finding.

### UNVERIFIED

- **No live API call was made** against Google Business Profile or Places — no credentials in this environment, and Business Profile access is gated behind an approval that may not have been granted yet.
- **The mockup's interactive behaviours were not exercised in a browser.** Every finding below about click handlers, filters, and modals comes from reading the code, not from clicking. They are code-level facts (line numbers cited), but a browser pass may surface more.
- **The Reddit Data Access Request's status is unknown.** `docs/…Use Case.pdf` is the *submission*, not an approval. Whether it was submitted, and whether it was granted, is not observable from this repo.
- **Whether the vendor decision has been made** — the roadmap says Blocked; nothing in the repo says otherwise.

---

## Requirement coverage

The PRD has no explicit requirement IDs. The IDs below are **inferred** from its Must-Have (P0) and Nice-to-Have (P1) sections and are not authoritative — if the team adopts different IDs, update this table first.

`C-*` IDs are compliance commitments taken from `docs/Remedy Pulse_Reddit Data Access_Use Case.pdf`. These are promises made in writing to a third-party data provider. **They appear nowhere in the PRD or roadmap**, which is itself the finding.

| ID | Requirement | In mockup? | Backend? |
|---|---|---|---|
| P0-1 | Cross-source chronological mentions feed | Yes (6 hardcoded items) | Google reviews only |
| P0-2 | Filter by keyword, platform, **and sentiment** | Keyword + platform + date only — **no sentiment filter** | No |
| P0-3 | CSV export of the current filtered view | Yes (Mentions, Reviews, EMV) | No |
| P0-4 | Negative items auto-flagged + assignable | 4 hardcoded alerts; 1 of 4 has no Assign button | No |
| P0-5 | Mark resolved, counts update | Yes | No |
| P0-6 | Reviews per branch + pending-reply flow | Yes | Partial (read only, no reply) |
| P0-7 | Composite health score + trend + attention summary | Yes ("Clarity Index") | No |
| P0-8 | Topics with sentiment **+ drill into constituent mentions** | Cards only — **zero click handlers, no drill-down** | No |
| P0-9 | EMV per article with expandable formula | Yes | No |
| P0-10 | Competitor share of voice **and sentiment** | Share of voice only — **word "sentiment" appears 0 times on that tab** | Ratings only |
| P0-11 | "Last synced" on every tab | Header pill (global, so all tabs) | **No timestamp emitted by any connector** |
| P1-1 | AI weekly summary + regenerate | Yes (cycles 3 canned strings) | No |
| P1-2 | Command palette (⌘K) | Yes | n/a |
| P1-3 | Simulated mention injection, **gated from live data** | Present; **the gate does the opposite** (see 0.13) | n/a |
| P1-4 | Per-mention drill-down (thread/comment chain) | No | No |
| C-1 | Reddit access via PRAW + OAuth | n/a | **No Reddit code; PRAW not in requirements.txt** |
| C-2 | Delete content + author data within 48h of deletion on Reddit | n/a | **No code, no schema field, no job** |
| C-3 | Descriptive versioned User-Agent per Reddit's format | n/a | **No code** |
| C-4 | No resale, redistribution, or model training on Reddit data | n/a | Policy — needs a written control |
| C-5 | Public content only, keyword-driven, low-volume polling | n/a | **No code** |

### Uncovered requirements

Requirements with **no implementation and no plan** in either the PRD's build sections or the roadmap:

- **C-1 through C-5** — every Reddit compliance commitment. The roadmap does not mention Reddit at all; the PRD lists it as a source but never as an obligation. Addressed in Phase 5.
- **P0-8 drill-down** and **P0-10 sentiment** — both have explicit PRD acceptance criteria and no design in the "already validated" mockup. Addressed in 0.19 and Phase 8.
- **P0-2 sentiment filter** — same. Addressed in 0.19 and 8.2.
- **News/press ingestion** — P0-1 requires it and P0-9 (EMV, the largest peso figures in the product) is built entirely on it. The Reddit PDF names "GNews" as already in use; no GNews code, key, or config exists. The roadmap has no line item for news ingestion at all. Addressed in 1.5 and 4.5.
- **Authentication** — the dashboard has none, and the PRD never mentions it. The success metrics assume "team login" events exist to count. Addressed in 5.5 and 3.1.

---

## Phase 0 — Close the gaps in what already exists

Every item here is a place where the code, the docs, or a submitted document claims something that is not enforced. All were verified in this session; the evidence is cited so you can re-check any of them yourself.

Phase 0 comes before any new feature because building on a false claim propagates it, and that cost compounds.

**Status (2026-09-03): all 20 items closed on branch `phase-0-close-gaps`.**
0.1–0.6 and 0.17 landed in `backend/` (retry/backoff helper, per-listing `status`/`fetchedAt` fields, corrected README/docstring claims, 24 new pytest tests, ruff config, and CI in `.github/workflows/ci.yml` — verified: `pytest` 24/24 passing, `ruff check` clean, fresh-venv `pip install` exits 0). 0.10–0.16, 0.19, 0.20 landed in `remedy-pulse-mockup.html` and `docs/README-Remedy-Pulse-Demo.md` (escaping, PHT-correct timestamps, the fixed simulate-mention gate, per-branch pending-reply tracking, the EMV filter-label fix, real relative timestamps, the three closed P0 gaps, and the corrected offline claim). Since closed, also verified interactively with a headless-Chromium Playwright script driving the real page (not just static analysis): sync pill renders a PHT-correct time, the simulate-mention chip is disabled while Live and enabled once Paused, replying once on the Greenhills row (2 pending) leaves it at "1 pending reply" instead of "All clear", the EMV date chip shows "Filtered Gross" under a real filter and correctly clears it back to "Gross" on "All time", the Mentions sentiment filter narrows the feed, the Topic-card modal opens with constituent-mention content, and the Competitors tab renders a sentiment breakdown — 7/7 checks passed, zero console errors. 0.7, 0.8, and 0.9 could not be *built* yet — no Reddit or news ingestion code exists for a deletion-propagation job or ingestion adapter to attach to — so each was closed as a written, ratifiable recommendation instead: see `docs/decisions/03-reddit-deletion-propagation.md`, `docs/decisions/04-reddit-integration-status.md`, and `docs/decisions/02-news-press-ingestion-path.md`. 0.18 is closed by this same commit, which adds `remedy-pulse-prd.md` and `remedy-pulse-roadmap.md` to git.

> **UNDERSTAND FIRST — a guarantee that nothing exercises is not a guarantee.**
> Docstrings, dependency lists, column names, and submitted compliance forms all express *intent*. Only executed code paths express *behaviour*. Most of this phase is the distance between those two — a retention promise that is a sentence in a PDF rather than a scheduled job, a "matches the mockup exactly" claim that does not, an access-denied path that returns an empty list. When you read the items below, notice how many would pass a code review unremarked. That is exactly why they need to be written down.

- [x] **0.1 · Rename `backend/env.example` → `backend/.env.example`**
  `backend/README.md:31`, `oauth_setup.py:12`, and `fetch_competitor_ratings.py:80` all instruct the reader to copy `.env.example`. The file on disk is `env.example`. Following the README verbatim on a fresh clone fails.
  **Effort:** S · **Requirement:** — · **Skip risk:** Every new machine hits a 10-minute dead end that the docs actively cause · LOW

- [x] **0.2 · Make a 403 from the reviews endpoint fail loudly instead of writing empty results**
  `fetch_owned_reviews.py:96-103` catches HTTP 403, prints a note, and `return reviews` — an empty list. `main()` then calls `build_aggregate()`, which happily produces `{"rating": null, "reviewCount": 0, "pendingReplies": 0}` and writes it to `reviews_aggregate.json`. A branch whose data was **denied** is indistinguishable on disk from a branch with genuinely no reviews. The PRD names this exact edge case: *"if a data source is down or delayed, I want to see when the dashboard was last successfully synced so that I don't mistake stale data for 'no news.'"*
  **Effort:** M · **Requirement:** P0-11 · **Skip risk:** Dashboard shows "0 reviews · all clear" for a branch whose data never loaded — the failure mode the PRD explicitly asked to avoid · HIGH

- [x] **0.3 · Emit a fetch timestamp and per-source status from every connector**
  No connector output carries a timestamp. `fetch_owned_reviews.py:35` imports `from datetime import datetime, timezone` and **never uses either** — the unused import is the only trace of the intended feature. P0-11 requires a "last synced" indicator on every tab; today there is nothing for it to display.
  **Effort:** S · **Requirement:** P0-11 · **Skip risk:** The freshness indicator has no data source, so it either lies or is omitted — and stale data reads as current · HIGH

- [x] **0.4 · Fix or retract the "matches the mockup exactly" claim on `reviews_aggregate.json`**
  `backend/README.md` says it *"Matches the Reviews tab table exactly"*; `fetch_owned_reviews.py:1-10` says *"shaped to match remedy-pulse-mockup.html directly."* Verified false in three ways: the Reviews table has a **Trend (30d)** column `build_aggregate()` never produces; `responseRate` is a fraction (`0.88`) where the table renders `88%`; there is no `status` field behind the "All clear" / "N pending replies" tag.
  **Effort:** S · **Requirement:** P0-6 · **Skip risk:** Whoever wires the tab trusts the claim, and finds the mismatch at render time instead of at read time · MEDIUM

- [x] **0.5 · Drop `google-auth-httplib2` or switch to the official client library**
  Declared in `requirements.txt:3`, imported nowhere — verified: the only repo-wide hit for `httplib2|googleapiclient|discovery` is the manifest line itself. Both scripts call REST endpoints with `requests` and a hand-built `Authorization: Bearer` header. Worth noting *why* this matters beyond tidiness: it means quota handling, retries, and backoff are all hand-rolled, which is 0.6.
  **Effort:** S · **Requirement:** — · **Skip risk:** Dead weight, and it hides that no adapter uses Google's own client · LOW

- [x] **0.6 · Add retry, backoff, and rate-limit handling to every outbound call**
  There is none. `resp.raise_for_status()` on every request; `time.sleep(0.2)` between review pages is the entire strategy. A single 429 or transient 500 aborts the run — and because the previous JSON is still on disk, the dashboard keeps serving it with no indication anything failed.
  **Effort:** M · **Requirement:** P0-11 · **Skip risk:** One transient network blip silently serves yesterday's data as today's · HIGH

- [x] **0.7 · Implement the Reddit 48-hour deletion propagation the access request promises**
  `docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` states: *"content and author-identifying data are removed within 48 hours of deletion on Reddit."* There is **no code, no schema field, no scheduled job, and no mention of this obligation in the PRD or the roadmap.** This is a term stated in writing to a data provider in a commercial access request.
  **Effort:** L·risky · **Requirement:** C-2 · **Skip risk:** Breach of the terms the Reddit Data API access was granted under — the provider can revoke access, which removes a P0 source entirely · CRITICAL

  > **HEADS-UP — deletion propagation is a *pull* problem, not a push problem.**
  > Reddit does not call you when a post is deleted. Complying means keeping the source IDs of everything you stored and re-checking them on a schedule, then deleting your copy when the source 404s or returns `[deleted]`. That is a recurring job with its own failure modes and its own quota cost — not a `DELETE` handler. Design it as ingestion-in-reverse. The reason this is easy to get wrong: the sentence in the PDF reads like a policy, and policies feel like documentation. This one is a cron job.

- [x] **0.8 · Reconcile the Reddit integration claim with the absence of any Reddit code**
  The same PDF commits to PRAW with OAuth and *"a descriptive, versioned User-Agent string … per Reddit's required format."* There is no PRAW in `requirements.txt` and no Reddit code in the repo — yet the mockup renders a Reddit mention (`r/PhilippinesSkincare`) as though the source is live, and the demo guide lists Reddit among the tracked channels.
  **Effort:** M · **Requirement:** C-1, C-3 · **Skip risk:** A source everyone believes is nearly done does not exist; the estimate for Mentions is wrong by a whole adapter · HIGH

- [x] **0.9 · Identify the news/press ingestion path — the EMV tab has none**
  The Reddit PDF names *"news via GNews"* as a channel already in use. No GNews code, key, or config exists anywhere in the repo. Meanwhile the EMV tab (₱2.37M gross across 6 articles) is built **entirely** on press coverage, and the roadmap has no line item for news ingestion at all.
  **Effort:** S to decide, M to build · **Requirement:** P0-1, P0-9 · **Skip risk:** The tab carrying the largest numbers leadership will quote has no identified data source · HIGH

- [x] **0.10 · Define and document the Clarity Index formula**
  The composite health score (`remedy-pulse-mockup.html:417`) is the landing view's headline number and the one figure leadership is most likely to repeat. Its formula is documented **nowhere** — verified: zero hits for "clarity" across every Markdown file in the repo. The EMV tab deliberately shows its math on every row; the score above it shows none.
  **Effort:** S to define, M to implement · **Requirement:** P0-7 · **Skip risk:** The number leadership acts on cannot be audited, reproduced, or defended when someone disagrees with it · MEDIUM

  > **UNDERSTAND FIRST — a composite score without a published formula gets argued with instead of acted on.**
  > EMV and the Clarity Index are the same kind of object: a single number standing in for a pile of judgement calls. The EMV tab handles this correctly — click any row and the chain of multipliers is right there, so a disagreement becomes "I think Prominence should be ×1.0, not ×1.5," which is a productive conversation. With no published formula, the Clarity Index can only be disagreed with as a whole ("that number feels wrong"), which is not actionable and quietly erodes trust in the whole dashboard. Publishing the formula is not a documentation task; it is what makes the metric usable.

- [x] **0.11 · Settle escaping before the data-driven refactor, not after**
  The mockup renders through `innerHTML` in 4 places and builds an `onclick` attribute by string concatenation from mention content (`simulateIncomingMention()`, ~line 1500: `viewSource('…', '…' + m.author.replace(/'/g,'') + '…')`). Today the content is a static pool. That same code path is the one that will carry real Google review text, Reddit comments, and Instagram captions.
  **Effort:** M · **Requirement:** P0-1 · **Skip risk:** Stored XSS via any review or comment body, in a tool whose entire purpose is displaying text strangers wrote · HIGH

- [x] **0.12 · Make the sync timestamp timezone-correct instead of labelling local time "PHT"**
  `simulateSync()` (~line 1690) formats `new Date().getHours()` from the **browser's** clock and unconditionally appends `PHT`. The hardcoded header value `Last synced 06:12 PHT` has the same problem. Viewed from any non-Manila machine, the freshness pill is wrong and confidently labelled.
  **Effort:** S · **Requirement:** P0-11 · **Skip risk:** The one control meant to answer "am I looking at stale data?" gives a wrong answer to anyone outside PHT · MEDIUM

- [x] **0.13 · Fix the simulated-mention gate — it currently does the opposite of what P1-3 asks**
  The PRD asks for simulated injection *"gated so it can't run against live data by accident."* The implemented gate is the Live/Paused toggle: `simulateIncomingMention()` **refuses when paused and allows when live** — precisely inverted. The "+ Simulate mention" chip sits in the main Mentions toolbar with no environment check of any kind.
  **Effort:** S · **Requirement:** P1-3 · **Skip risk:** Fabricated mentions injected into a production feed that leadership reads and quotes · MEDIUM

- [x] **0.14 · Fix `sendReply()` marking a branch "All clear" after one reply of two**
  `sendReply()` (~line 1364) unconditionally sets the row tag to "All clear" and the response rate to `100%`. The Greenhills row shows **"2 pending replies"** — replying once clears both. The demo overstates completion in the exact dimension the product exists to track.
  **Effort:** S · **Requirement:** P0-6 · **Skip risk:** A branch reads "all clear" with a review still unanswered — the failure the whole tool is meant to prevent · MEDIUM

- [x] **0.15 · Fix the EMV "Filtered" label sticking on after "All time" is selected**
  `recomputeEmvTotals()` tests `emvDateMode !== 'all'`, but choosing "All time" from the menu assigns the **string** `'All time'`, not `'all'`. After any interaction with the date chip, the totals read "Filtered Gross ₱…" permanently, even with nothing filtered.
  **Effort:** S · **Requirement:** P0-9 · **Skip risk:** An unfiltered total gets screenshotted into a deck labelled "filtered", or vice versa · LOW

- [x] **0.16 · Derive feed timestamps from data instead of hardcoded strings**
  Feed items carry both a `data-date` attribute (which the filters use) and a hardcoded relative string ("41 min ago", "3 hrs ago") in the markup (which the reader uses). Two sources of truth for the same fact, and only one of them is real.
  **Effort:** S · **Requirement:** P0-1 · **Skip risk:** Timestamps visibly wrong the moment data is live; needs a real relative-time formatter anyway · LOW

- [x] **0.17 · Stand up tests, linting, and CI — there are none**
  Verified: no test files, no `.github/`, no `pyproject.toml`, no lint or type config anywhere in the repo. Start small — the normalize/aggregate functions in `fetch_owned_reviews.py` are pure and testable today, before any of the harder work lands.
  **Effort:** M · **Requirement:** — · **Skip risk:** Every regression is found by a human clicking, on a two-person team with 27 days · MEDIUM

- [x] **0.18 · Commit the PRD and roadmap to git**
  `git status` shows `remedy-pulse-prd.md` and `remedy-pulse-roadmap.md` as untracked (`??`). Every requirement ID in this checklist cites a document that has no version history.
  **Effort:** S · **Requirement:** — · **Skip risk:** Requirements change with no diff, no date, and no record of who changed them · MEDIUM

- [x] **0.19 · Close the three P0 acceptance criteria the "validated" mockup does not cover**
  Verified gaps against the PRD's own acceptance criteria: **(a)** Mentions has no sentiment filter — only keyword, platform, and date (P0-2 requires sentiment); **(b)** Topic cards have **zero click handlers** — there is no drill-down to constituent mentions (P0-8's acceptance criterion is exactly that drill-down); **(c)** the Competitors tab contains the word "sentiment" **zero times** (P0-10 requires side-by-side share-of-voice *and* sentiment).
  What makes this more than a to-do list: the PRD claims *"an interactive HTML mockup covering all six v1 sections has already been built and validated with stakeholders,"* and tells engineering to *"build against an already-agreed-upon UI."* For these three, there is no agreed-upon UI to build against. Design them and re-validate before they enter a sprint.
  **Effort:** S to design, M to build · **Requirement:** P0-2, P0-8, P0-10 · **Skip risk:** Three P0 acceptance criteria fail at launch review, with a design round still to do and no time to do it · HIGH

- [x] **0.20 · Correct the demo guide's "no internet connection required" claim**
  `docs/README-Remedy-Pulse-Demo.md` says the mockup needs *"no installation, no login, no internet connection."* It loads three font families from `fonts.googleapis.com`. Fallbacks are declared so it degrades rather than breaks, but a stakeholder opening it offline sees a different-looking product than the one that was signed off.
  **Effort:** S · **Requirement:** — · **Skip risk:** Design sign-off happens against a rendering the reviewer may not have actually seen · LOW

- [x] **0.21 · NEW (2026-09-11) · A `SystemExit` inside a shared fetch function crashes the whole scheduler, not just one source**
  Found while wiring the reply flow (7.4/8.5), not from a checklist item — `google_reviews_job.run()` and `news_job.run()` both call functions (`fetch_owned_reviews.load_credentials()`, `fetch_news_articles.fetch_articles_for_term()`) that were still raising `SystemExit` — a `BaseException`, not an `Exception` — for a routine failure (no `token.json` yet; a GNews 403 from an invalid key **or the free tier's ordinary 100-requests/day quota being spent**). `app.repository.start_run()`'s context manager only catches `except Exception`, so either `SystemExit` propagated straight out of `app.scheduler.run_due_jobs()`'s loop **uncaught** — worse, `run_due_jobs()` itself had no try/except *at all* around `job.run(session)`, so even an ordinary exception from any job skipped every other due source for the rest of that pass, and crashed `run_forever()`'s standing process outright. `google_reviews_job.py`'s own docstring already flagged and fixed this exact class of bug for its "no Business Profile accounts" case (using `RuntimeError`, not `SystemExit`) — the fix here closes the same gap the two places that earlier fix missed.
  **Effort:** M · **Requirement:** P0-1, P0-11 (the freshness/status promise this ledger backs) · **Skip risk:** One expired token or one exhausted daily API quota — both routine, expected conditions, not edge cases — silently stops ingestion for every other source too, or kills the standing scheduler process entirely, with nothing in the ledger to explain why · HIGH
  **Status:** Done. `fetch_owned_reviews.load_credentials()` and `fetch_news_articles.fetch_articles_for_term()` now raise `RuntimeError`; each script's own CLI `main()` converts it back to a clean `SystemExit` so direct/manual runs keep today's UX, and `news_job.run()`/the fix below give the scheduler a real exception to catch. `fetch_news_articles.main()`'s per-term loop and `news_job.run()` both also stop early (not `continue`) on this specific error — a 403 is account-wide, so every remaining term would just 403 again — while still ingesting whatever was already fetched from terms that succeeded first, instead of discarding it the way the old bare `SystemExit` did. `news_job.run()` explicitly marks the ingestion run `PARTIAL`/`ERROR` with the real reason in this case, rather than falling through to `start_run()`'s automatic inference, which would have silently reported `SUCCESS` when `items_seen`/`items_ingested` are both `0` — the exact "silent 403 reads as an all-clear" failure mode item 0.2 already named for a different connector. `app.scheduler.run_due_jobs()` now wraps `job.run(session)` in its own try/except: prints which source failed and why, tries to commit the ERROR-marked `IngestionRun` row `start_run()` already flushed (rather than just rolling it back and losing the one record of what happened), and only falls back to a rollback if that commit itself fails (a genuinely aborted Postgres transaction, which SQLite can't reproduce) — verified against a real Postgres container forcing an actual aborted transaction, not just SQLite, confirming the next job in the same pass still runs cleanly afterward. Audited every other `fetch_*.py`/job pair for this specific `SystemExit`-escapes-a-shared-function shape (`fetch_competitor_ratings.py`, `fetch_meta_mentions.py`, `fetch_reddit_mentions.py`) — all three were already correctly guarded for it (credentials checked up front, or per-item errors already caught as a typed result rather than raised); this exact bug shape was specifically a Google-reviews-and-GNews gap, not a repo-wide pattern. That same audit pass surfaced a related-but-distinct gap in `meta_job.py` instead — see **0.22**. 328 tests pass (up from 313 before this session's changes today), ruff clean.

- [x] **0.22 · NEW (2026-09-11) · An unexpected exception from a Meta capability's fetch left NO ledger row at all, not even an error one**
  Found in the same audit pass as 0.21, a distinct bug shape rather than the same one: `meta_job._run_capability()` called `fetch_fn()` (one of `fetch_instagram_comments`/`fetch_instagram_mentions`/`fetch_facebook_comments`) **before** opening `start_run()` — so it could decide whether a `"not_configured"` result should get no ledger row at all (a deliberate design choice, see the module's own decision 2). Those three functions only convert two specific failure shapes into their own typed result (`MetaAccessDenied` -> `"access_denied"`, `RetryExhaustedError` -> `"error"`) — anything else (`fetch_meta_mentions._graph_get()`'s fallback `resp.raise_for_status()` for a genuine non-retryable HTTP status, a malformed JSON body, …) raised instead, and escaped **before** `start_run()` even began. Thanks to 0.21's scheduler fix this no longer crashes anything, but the specific capability's failure vanished with no ledger row — indistinguishable from "never run" via `get_source_freshness()`, which decision 2 explicitly reserves for the genuinely-not-configured case, not a real crash.
  **Effort:** S · **Requirement:** P0-11 · **Skip risk:** A capability silently stops reporting any status at all after an unexpected failure, and nothing in the ledger or the UI's per-source freshness pill can tell "never configured" apart from "crashed and nobody noticed" · MEDIUM
  **Status:** Done. `_run_capability()` now wraps the `fetch_fn()` call in its own try/except; an unexpected exception gets its own `start_run()` opened just for this, marked `ERROR` with the exception message, preserving decision 2's "not_configured gets no row" behavior exactly (that check still only happens after a successful `fetch_fn()` call) while closing the "silently invisible" gap for everything else. New regression test exercises exactly this path, confirming the other two capabilities in the same `run()` call are unaffected. 333 tests pass (up from 328), ruff clean.

- [x] **0.23 · NEW (2026-09-11) · Classification is fully built but never actually runs — nothing calls it in production**
  Found while auditing the job/scheduler layer for 0.21/0.22, a discovery rather than a bug: `app/classification.py`'s `classify_unclassified_batch()` — the function that would classify every newly-ingested `Mention` row's sentiment/alert-category via Groq — is called from nowhere except its own test file. Its own docstring says so explicitly: *"This is what a future scheduled job (app/jobs/, out of this module's scope) would call — this function only does the classification work, not the scheduling/wiring."* That scheduled job was never built. Concretely: today, any mention a real ingestion job writes gets `sentiment=None`/`classified_at=None` forever — the alert routing (6.3), the sentiment filter (P0-2), the topic sentiment split (P0-8), and the competitor sentiment card (P0-10) all read real classifier output, and none of it will ever populate against live data until something calls this function on a schedule.
  **Effort:** S to build (mirrors `reddit_deletion_job.py`'s already-established "maintenance sweep reusing start_run()'s ledger machinery, not a fetch-new-items job" pattern) · **Requirement:** P0-2, P0-4, P0-8, P0-10 (every feature that depends on real, non-null sentiment) · **Skip risk:** The entire product looks fully built and passes every acceptance test against seeded data, then silently does nothing once real ingestion is live — the exact "code claims more than it enforces" gap this whole checklist exists to catch, just found one phase later than usual · HIGH
  **Status:** Done. `app/jobs/classification_job.py` calls `classify_unclassified_batch()` inside `start_run()`, registered in `app/jobs/__init__.py`'s `JOBS` list under its own `SOURCE_NAME = "classification"` (its own independent ledger entry, same reasoning as `meta_job.py`'s three — a stalled classifier is a different, independently-diagnosable fact from a stalled ingestion source). `app.scheduler.CADENCE_HOURS["classification"] = 0.25` (15 minutes, vs. every other job's 12-hour default) — deliberately, not an oversight: the PRD's core v1 metric (median time from a negative mention *appearing* to being *assigned*, target under 4 business hours) is unreachable if a mention sits unclassified — and therefore unrouted to the alert workflow — for up to 12 hours before this job even looks at it. The one failure mode this job's own docstring identifies (`ClassifierNotConfiguredError` — missing `GROQ_API_KEY`) is caught and recorded as an `ERROR` run with the real reason, matching every sibling job's convention, rather than left to propagate. Verified two ways: 7 new mocked tests (SQLite) plus a **real-Postgres** test proving the oldest-ingested-first ordering query works on the one dialect this project ships against, **and** a live smoke test against the real Groq API with a real key (not mocked) — a Taglish review describing an allergic reaction classified correctly as `Negative`/`crisis` at 0.97 confidence, with the ledger recording a clean `SUCCESS` run. 340 tests pass (up from 333), ruff clean.
  One real product-facing consequence surfaced and flagged, not silently decided: this job's fast cadence means `GET /api/status`'s `lastSyncedAt` (`docs/api-contract.md`'s own documented "most recent successful run across every registered source") can now read "synced minutes ago" even when every real external data source has been stale for half a day — classification succeeding masks exactly the staleness P0-11 exists to surface. Left the documented contract behavior unchanged rather than silently redefining it; see **0.24** for the decision this needs.

- [x] **0.24 · NEW (2026-09-11) · Decide whether `lastSyncedAt` should exclude non-external-data sources (classification, reddit_deletion_check)**
  A direct consequence of 0.23: `docs/api-contract.md` defines `lastSyncedAt` as "the most recent successful run across every registered source" — which already quietly included `reddit_deletion_check` (also a re-processing, not a fetching, job) before this pass, but wasn't very visible since nothing ran that often. `classification_job`'s 15-minute cadence (0.23) makes the same quirk far more likely to actually mislead someone: the header sync pill can show a very recent time while Google/Reddit/GNews/Meta have all been stale for hours.
  **Effort:** S · **Requirement:** P0-11 · **Skip risk:** The one control meant to answer "am I looking at stale data?" reads as fresh because an unrelated background job happens to be healthy · MEDIUM

  > **YOUR CALL — does "last synced" mean any registered job, or only external data sources? — DECIDED: only external data sources.**
  > **Leave it as every registered source (status quo).** Simplest, already shipped and documented, and arguably still defensible — "the system is functioning" is a real thing to communicate too. Costs: the pill can mislead about data freshness specifically, which is the one thing P0-11 asks it to answer.
  > **Redefine it to only external-data-fetching sources** (exclude `classification`, `reddit_deletion_check`). Restores the pill's original intent precisely. Costs: a small, real change to a documented contract value, and needs a way to mark which registered sources count as "data" vs. "maintenance" (today nothing in the job contract distinguishes them).
  > **The lean, and the decision:** redefine it — P0-11's own PRD wording ("so I don't mistake stale data for 'no news'") is specifically about data staleness, and a job that touches zero external sources has no bearing on that question by construction.
  > **What would change this:** if the team decides the pill is meant more broadly as "is the whole ingestion+processing pipeline alive," in which case status quo is actually the more honest reading and this should be reverted — see `docs/decisions/14-last-synced-excludes-non-data-sources.md`'s own "What would change this."

  **Status:** Done. `docs/decisions/14-last-synced-excludes-non-data-sources.md` records the decision. Implementation: an optional `IS_DATA_SOURCE: bool` job-contract attribute (`app/jobs/__init__.py`, defaulting `True` via `getattr()` so every ordinary job is unaffected), set `False` on `classification_job.py`/`reddit_deletion_job.py`; `GET /api/overview`'s `lastSyncedAt` now skips those; `GET /api/status` gains an `isDataSource` field per source (a label, not a filter — failed non-data runs still show up for the per-source banner); the mockup's `mostRecentSuccessAt()` applies the same exclusion client-side; `docs/api-contract.md` updated in both places. Verified: new backend tests proving a newer `classification` success doesn't win over an older real `google_reviews` success, `isDataSource` shape assertions, and a real-browser check of the client-side exclusion. 342 tests pass (up from 340), ruff clean.

---

## Phase 1 — Long-lead external access (start today, runs in parallel with everything)

None of this is engineering effort; all of it is calendar time you do not control. That is exactly why it goes first. Every item here can be in flight while Phase 2 is being built.

**Status (2026-09-03):** by explicit direction, 1.1–1.4 and 1.6 were left undone in this pass — they are procurement/vendor/org decisions (submitting access requests, chasing approvals, locking a vendor path, setting trigger dates), not engineering, exactly as this phase's own framing says. **1.5's engineering half is done:** `backend/fetch_news_articles.py` now exists (GNews connector, same shape/conventions as the two Google connectors — retry via `http_utils`, `fetchedAt` + per-article `status`, graceful no-key failure, 7 new tests, ruff clean). It does not decide the vendor question — see `docs/decisions/02-news-press-ingestion-path.md` — it's the evaluation harness that decision doc recommends, ready to run the moment a (self-serve, no-approval-wait) GNews key exists.

- [ ] **1.1 · Submit or chase the Google Business Profile API access request**
  `backend/README.md` correctly identifies this as *"the one real blocker"* and *"the long pole here, not the code."* Confirm whether it has actually been submitted, and get a status.
  **Effort:** L·risky · **Requirement:** P0-6 · **Skip risk:** No owned-review data at all — the Reviews tab cannot exist, and Google Reviews is the roadmap's designated "guaranteed early progress" source · CRITICAL

  > **HEADS-UP — Business Profile API access has no SLA and is commonly rejected on the first submission.**
  > It is a human review of a written justification, not a checkbox, and rejections are frequently about the *description of intended use* rather than anything technical. Write the justification as carefully as the Reddit one in `docs/` was written — that document is a good template. Also: the quota, once granted, is per-project QPM, not per-account, so testing and production share a budget unless you split projects. You would find the approval gate by reading the prereqs page; you would only find the QPM sharing by hitting it.

- [ ] **1.2 · Confirm the status and outcome of the Reddit commercial Data Access Request**
  `docs/Remedy Pulse_Reddit Data Access_Use Case.pdf` is the submission. Whether it was sent, and whether it was granted, is not observable from this repo. The answer determines whether Phase 5's compliance work is urgent or hypothetical.
  **Effort:** S to check, L·risky to wait · **Requirement:** C-1, P0-1 · **Skip risk:** Building Reddit ingestion against terms you have not been granted · HIGH

- [ ] **1.3 · Start Meta app review for Instagram/Facebook comment and mention access**
  Both platforms are in the mockup's feed and its source-breakdown pie (37% of mention volume combined).
  **Effort:** L·risky · **Requirement:** P0-1 · **Skip risk:** The two largest social channels in the product's own charts have no ingestion path · HIGH

  > **HEADS-UP — Instagram *mentions* and Instagram *comments* are different permissions, and both need the account connected as a Business asset first.**
  > Reading comments on your own posts, reading comments where you were tagged, and reading mentions in other people's captions are three distinct scopes with three distinct review outcomes. Budget for the possibility that one is approved and another is not, and know now which of the mockup's feed items depend on which. This is findable in the docs, but only if you go looking for the plural — the permission list reads as one feature until you need it to be three.

- [ ] **1.4 · Lock the vendor/build path with a named owner and a dated deadline**
  The roadmap's top risk, still `Blocked`, with *"No owner-assigned deadline exists yet."* It recommended locking by Aug 21. Today is Sep 3.
  **Effort:** M·risky · **Requirement:** — (blocks P0-1, P0-8, P0-10) · **Skip risk:** Mentions, Topics, and Competitors cannot be scoped, let alone built · CRITICAL

- [ ] **1.5 · Decide the news/press ingestion path (see 0.9)**
  GNews, as the Reddit PDF asserts is already in use? A vendor feed, if the vendor decision lands there? A licensed aggregator?
  **Effort:** M · **Requirement:** P0-1, P0-9 · **Skip risk:** EMV has no input · HIGH

- [ ] **1.6 · Set a fallback trigger date for each access request above**
  For each of 1.1, 1.2, 1.3: the date past which you stop waiting and switch to the licensed-aggregator fallback `backend/README.md` already names. Decide the dates now, while nobody is under pressure.
  **Effort:** S · **Requirement:** — · **Skip risk:** The team waits on an approval that is never coming, and discovers it in the last week · HIGH

  > **YOUR CALL — how long do you wait on each approval before triggering the fallback?**
  > **Wait it out.** Free, first-party, full data fidelity. Costs you the launch date if it doesn't land, and you find out late.
  > **Set a hard trigger date (recommended).** Pick a date per source, write it down, and switch to the licensed aggregator when it passes. Costs money and some fidelity — aggregators typically give you less granular review metadata and no reply-write capability. Buys you a launch date you can actually commit to.
  > **Build both paths behind the adapter interface.** Real work, but it makes the decision reversible and turns a blocking dependency into a config switch.
  > **The lean:** hard trigger dates, set today, one per source. With 27 days to the target, the cost of guessing wrong on "it'll come through" is the entire launch, and the aggregator fallback is already identified in `backend/README.md` — it is a decision, not a discovery.
  > **What would change my mind:** a Google or Reddit rep giving a concrete date, or a fallback quote that turns out to be prohibitively expensive — in which case the honest move is to move the launch date, not to hope.

---

## Phase 2 — Foundations

**Status (2026-09-04): all 6 items closed on branch `phase-2-foundations`, new `backend/app/` package.** 2.1: `app/models.py` — one `Mention` table covering review/social-mention/press-article rows, unique on `(source, external_id)`, plus `raw_payload`/`deleted_at` pre-built for 0.7's not-yet-built Reddit deletion job. 2.2: Postgres + SQLAlchemy + Alembic, per `docs/decisions/05-persistence-choice.md` (adopts this item's own stated "lean"). 2.3: `app/config.py` — a validated `Settings`, replacing scattered `os.getenv`+`SystemExit` for whatever Phase 4 job adopts it (the existing `fetch_*.py` scripts are untouched — see the boundary note in `app/__init__.py`). 2.4: `app/repository.py`'s `start_run()`/`get_source_freshness()` — freshness derived from an append-only run log, never a mutable field. 2.5: `upsert_mention()` — idempotent `ON CONFLICT DO UPDATE` on `(source, external_id)`. 2.6: Alembic, autogenerated from the models.

Verified against a **real local Postgres** (`docker compose up -d` in `backend/`, not just SQLite): `alembic upgrade head` succeeds, `alembic check` reports zero drift between the models and the applied schema, a downgrade-then-upgrade roundtrip is clean, and the ON CONFLICT upsert + ledger logic were exercised by hand against it (duplicate upsert stays one row, last-write-wins on update, a failed run's error message and a prior success both remain independently queryable). 53 automated tests pass (`pytest backend/tests/`) — the repository/config logic against fast in-memory SQLite for every run, plus 3 tests in `test_app_repository_postgres.py` that hit the same code path against real Postgres and skip cleanly (verified: 50 passed/3 skipped, ~14s, no hang) when none is reachable, which is CI's default state before this change. `ruff check backend/` is clean. CI (`.github/workflows/ci.yml`) now also runs a Postgres service container and `alembic upgrade head` on every push, so those 3 tests execute for real there too, not just locally.

- [x] **2.1 · Define the vendor-agnostic mention/review/article schema**
  The roadmap's one "Now" item that was never blocked on the vendor decision, assigned to Ceferino, and still not started. Every adapter and every tab depends on it.
  **Effort:** M · **Requirement:** P0-1 · **Skip risk:** Each adapter invents its own shape; the UI refactor has nothing stable to render · HIGH

- [x] **2.2 · Choose and stand up persistence**
  **Effort:** M · **Requirement:** — · **Skip risk:** JSON files on disk cannot support assignment state, resolution timestamps, or the metrics in Phase 3 · HIGH

  > **YOUR CALL — where does the data live?**
  > **Postgres.** Real concurrency, real migrations, JSONB for the source payloads you'll want to keep, and the deletion/retention queries in Phase 5 are trivial. Costs you a service to run and a migration discipline to maintain.
  > **SQLite.** Zero ops, a single file, genuinely adequate for one clinic group's mention volume. Costs you concurrent-write headaches the moment ingestion and the API run at once, and a painful migration later if this becomes multi-brand (a stated P2).
  > **Whatever the vendor gives you.** If the vendor decision lands on Awario Enterprise or Brand24, some of this may be hosted. Costs you the ability to implement C-2 deletion propagation on their data, and the ability to join their data with your Google reviews.
  > **The lean:** Postgres. The deciding factor is not scale — it is that Phase 5's deletion job and Phase 3's metrics are both "query across everything by timestamp and source ID," and that is where SQLite's single-writer model starts hurting exactly when you have real data in it.
  > **What would change my mind:** if the vendor decision lands somewhere that hosts the mention store *and* contractually handles Reddit deletion propagation, the calculus changes completely — then the only thing you own is Google reviews, and SQLite is plenty.

- [x] **2.3 · Service skeleton with proper config and secrets handling**
  Today: module-level `load_dotenv()` and `raise SystemExit` in scripts. Fine for a script, not for a service.
  **Effort:** M · **Requirement:** — · **Skip risk:** Credentials handling gets improvised under deadline pressure · MEDIUM

- [x] **2.4 · Build the ingestion run ledger**
  Per source: `last_attempt_at`, `last_success_at`, `status`, `error`, `items_ingested`. This is the single source of truth for P0-11 and the fix for 0.2, 0.3, and 0.6 all at once.
  **Effort:** M · **Requirement:** P0-11 · **Skip risk:** No honest answer to "is this data current?" anywhere in the system · HIGH

  > **UNDERSTAND FIRST — freshness is a property of the pipeline, not of the page.**
  > The instinct is to timestamp the page render, which is what the mockup does. But "last synced 06:12" is only meaningful if it means *"the Google adapter last completed successfully at 06:12."* Those are different facts, and the gap between them is where 0.2's silent-403 lives: a run that failed still produces a page render. The ledger inverts the relationship — the page *reads* freshness from the pipeline instead of asserting it. Once you have a per-source ledger, "Reddit is 3 days stale but Google is current" becomes expressible, which is the thing the marketing team actually needs to know.

- [x] **2.5 · Idempotent upsert keyed on (source, external_id)**
  Polling adapters re-fetch the same items constantly.
  **Effort:** S · **Requirement:** P0-1 · **Skip risk:** Duplicate mentions inflate volume counts, share of voice, and the Clarity Index · MEDIUM

- [x] **2.6 · Migrations from the first schema commit**
  **Effort:** S · **Requirement:** — · **Skip risk:** Schema and code drift with no way to reconcile them · MEDIUM

---

## Phase 3 — Instrumentation, before the features it measures

This phase sits here deliberately. A metric added after launch has no baseline, which makes it worthless at exactly the moment it is needed — the 30-day review the PRD's success metrics are written around.

**Status (2026-09-04): 3.1, 3.2, and 3.4 closed; 3.3 is engineering-ready but still needs a human to actually do the lookup.** New `Event` table + `Mention.assigned_at`/`assigned_to`/`resolved_at` columns in `backend/app/models.py`, migration `4f7259f81e17` (verified against real Postgres — `alembic upgrade head`/`alembic check` zero drift/downgrade-upgrade roundtrip, same rigor as Phase 2). `backend/app/repository.py` adds `log_event`, `record_ingestion` (fires `ITEM_INGESTED` only on genuine first-insert, not on re-ingest — `upsert_mention()` now returns whether it inserted), `assign_mention` (first-assignment-wins on `assigned_at`, always updates `assigned_to`, always logs an event), `resolve_mention`, `log_export`/`get_export_activity` (3.4), `log_login` (schema-ready, no caller yet — Phase 5.5 builds auth), and `get_median_time_to_assignment()` — the actual 3.2 metric, median computed in Python so the same logic is correct on SQLite and Postgres. 80 tests pass (`pytest backend/tests/`, including real-Postgres coverage for the trickiest semantics), ruff clean.

One thing caught and fixed during review, worth recording as an example of why cross-checking parallel work matters: the schema was first built with `response_time_hours` as non-nullable, but the 3.3 template doc (written in parallel, see below) correctly insists a review with no reply yet is "a real, worth-recording outcome," not something to omit. Fixed by making the column nullable and `get_baseline_summary()` report `no_reply_count` alongside the median/mean — computed only over rows that did get a reply — rather than silently dropping no-reply rows from the average, which would have made the baseline look better than reality.

3.3 itself — the actual "look at Remedy's last 20 negative reviews and note reply times" — is a one-time human task against real historical data, not something to fabricate. `docs/response-time-baseline-template.md` is the ready-to-fill template plus the exact `record_baseline_response_time()` call shape; the table starts empty on purpose. Left unchecked below until someone with Google Business Profile access actually does it (no API approval needed — this is Phase 1's kind of blocker, not engineering's).

- [x] **3.1 · Event log: login, item_ingested, item_assigned, item_resolved, export_downloaded**
  The PRD's measurement method says these come *"from application logs (login events, alert timestamps, resolution timestamps)."* Those logs do not exist, and neither does the login they assume.
  **Effort:** M · **Requirement:** — (enables all Success Metrics) · **Skip risk:** No evidence for or against the launch at the 30-day review · HIGH

- [x] **3.2 · Query for the core metric: median time from negative mention appearing → assigned**
  The PRD names this *"the core success focus for v1"* with a target under 4 business hours. It needs an ingested-at timestamp and an assigned-at timestamp on the same row, which means it constrains the Phase 2 schema.
  **Effort:** S · **Requirement:** — · **Skip risk:** The one metric v1 is judged on cannot be computed · HIGH

- [ ] **3.3 · Capture a pre-launch baseline for response time**
  Even a rough manual sample of "how long did the last 20 negative reviews take to get a reply, before this tool existed."
  **Effort:** S · **Requirement:** — · **Skip risk:** "Response time improved" is unprovable — there is nothing to compare against · MEDIUM

- [x] **3.4 · Instrument export usage**
  Target: at least one CSV export per week. Cheap to add now, impossible to backfill.
  **Effort:** S · **Requirement:** P0-3 · **Skip risk:** A stated success metric goes unmeasured · LOW

---

## Phase 4 — Ingestion adapters

**Status (2026-09-04, updated 2026-09-05): 4.1–4.7 all closed.** `backend/app/jobs/` wires every connector (Google reviews, Google Places, GNews, Reddit, Instagram ×2, Facebook) into the Phase 2/3 ledger/repository via a shared contract (`SOURCE_NAME` + `run(session)`, documented in `app/jobs/__init__.py`), plus `scheduler.py` (4.6, deliberately simple per this phase's own instruction) and `status_report.py`. **Reddit and Meta adapters are built and tested against mocks but not live-verified** — no credentials exist for either (Phase 1's Reddit commercial-tier and Meta App Review approvals are both still open) — see `backend/README.md`'s "Ingestion adapters" section for exactly what's pending before either goes live. 4.7's real fix (not `status_report.py`'s stopgap) needed Phase 7's API layer to exist first; once Phase 7/8 built `GET /api/status` and the mockup's data layer, 4.7 closed by wiring a real per-source-failure banner into the mockup (see 4.7's own status note). 313 tests pass (`pytest backend/tests/`), ruff clean, migration verified against real Postgres.

- [x] **4.1 · Harden the Google owned-reviews connector into a scheduled job**
  Depends on 0.2, 0.3, 0.6, and 1.1. The normalize/aggregate logic is sound and reusable; the runner around it is not.
  **Effort:** M · **Requirement:** P0-6, P0-1 · **Skip risk:** No review data · HIGH

- [x] **4.2 · Google Places competitor-ratings adapter**
  **Effort:** S · **Requirement:** P0-10 · **Skip risk:** No competitor benchmark · MEDIUM

  > **HEADS-UP — the Places `reviews` field returns at most 5 reviews, chosen by an undocumented rule that changes between calls.**
  > `fetch_competitor_ratings.py` documents the cap honestly in its module docstring, which is more than most code does. What the docstring does not say is the consequence: **any trend you compute from `sampleReviews` is noise.** The `rating` and `user_ratings_total` fields are stable and trendable; the sample is not. Do not let a competitor sentiment trend get built on that sample — which is directly relevant to P0-10, where "competitor sentiment" is a stated requirement and this is the only competitor data source you have. That tension needs resolving before 8.8, not during it.

- [x] **4.3 · Reddit adapter — PRAW, OAuth, versioned User-Agent, source IDs retained for 5.1**
  **Effort:** L · **Requirement:** C-1, C-3, C-5, P0-1 · **Skip risk:** A P0 source is absent while the demo implies it works · HIGH

- [x] **4.4 · Meta (Instagram + Facebook) adapter**
  **Effort:** L·risky · **Requirement:** P0-1 · **Skip risk:** 37% of the product's own charted mention volume has no source · HIGH

- [x] **4.5 · News/press adapter, per the 1.5 decision**
  **Effort:** M · **Requirement:** P0-1, P0-9 · **Skip risk:** EMV has no input · HIGH

- [x] **4.6 · Scheduler with per-source cadence**
  The PRD scopes v1 at same-day/next-day freshness, not real-time — so this can be simple. Say so in the code, or someone will over-build it.
  **Effort:** M · **Requirement:** P0-1 · **Skip risk:** Ingestion runs when someone remembers to run it · HIGH

- [x] **4.7 · Surface per-source failures in the UI, not just in logs**
  The other half of 0.2. A failed source must be visible to the marketing team, not only to whoever reads stderr.
  **Effort:** M · **Requirement:** P0-11 · **Skip risk:** Silent partial outages presented as complete data · HIGH
  **Status:** Done. `GET /api/status` (Phase 7) already returned `lastStatus`/`lastError` per source, but nothing in the mockup ever surfaced it beyond computing the sync pill's timestamp — `renderDataSourceBanner()` now shows a coral banner (visible on every tab, same element the demo-mode notice already used, mutually exclusive with it) naming the failing source(s) and the real error text when any source's `lastStatus` is `error`/`access_denied`, and a softer note for `partial`. Verified against a real API + Postgres with a genuinely errored ingestion run.

---

## Phase 5 — Compliance, privacy, and security

**Hard constraint: nothing in this phase may be deferred past the first production ingestion run against real data.** Not past launch — past the first real run. Once third-party content is in your store, 5.1 is already overdue and 5.3 has already happened.

**Status (2026-09-04, updated 2026-09-05): 5.1, 5.2, 5.3, 5.5, 5.7, 5.8 closed; 5.4, 5.6 left open — each genuinely blocked on something this session cannot produce.** 5.1/5.2 (`reddit_deletion_job.py`, the User-Agent format) and 5.3 (masking extended to Reddit/Instagram/Facebook — see `backend/README.md`) are built and tested, same live-credential caveat as Phase 4. 5.7: `pip-audit` is now a CI step, and it found and fixed 3 real CVEs in `requests`/`python-dotenv` during this pass — not just wired in, actually used (the "whatever the frontend build ends up being" half doesn't apply — the mockup has no build step per Phase 0's findings). 5.8: `docs/decisions/07-reddit-c4-no-resale-control.md` is the documented control this item asks for. **5.5 (updated 2026-09-05):** left open at the time of this note because "there's no HTTP framework yet for a login route to attach to (Phase 7)" — Phase 7 subsequently built exactly that, so this is now genuinely done; the checkbox below was simply never revisited until now. **Left open, with reasoning documented in each area:** 5.4 needs the actual `RemedyPulseSpec_1` document, which doesn't exist in this repo — `docs/decisions/06-ph-data-privacy-act-review.md` documents the gap precisely rather than fabricating a legal review. 5.6's `docs/decisions/08-secrets-at-rest.md` recommends an approach but stays generic pending the still-undecided hosting/vendor choice.

- [x] **5.1 · Reddit deletion-propagation worker (implements 0.7)**
  Scheduled re-check of stored source IDs; delete local copies of content deleted upstream, within 48 hours.
  **Effort:** L·risky · **Requirement:** C-2 · **Skip risk:** Breach of granted API terms; revocation removes a P0 source · CRITICAL

- [x] **5.2 · Versioned descriptive User-Agent on every Reddit request**
  Reddit's required format, and the PDF commits to it explicitly.
  **Effort:** S · **Requirement:** C-3 · **Skip risk:** Rate-limited or blocked, and in visible breach of a stated term · HIGH

- [x] **5.3 · Extend PII minimization to every source**
  `mask_reviewer_name()` in `fetch_owned_reviews.py:113` masks Google reviewer names to first-name-plus-initial, citing the PH Data Privacy Act. Nothing equivalent exists for Reddit usernames, Instagram handles, or Facebook commenters — and the mockup displays `@glowwithsab` and `u/skinseeker_mnl` in full.
  **Effort:** M · **Requirement:** C-2, — · **Skip risk:** One source is privacy-minimized and four are not, under a law the code already cites · HIGH

- [ ] **5.4 · PH Data Privacy Act review: retention period, data-subject requests, lawful basis**
  `mask_reviewer_name()` cites *"spec §11."* That spec (`RemedyPulseSpec_1`, referenced in the mockup footer and in §§5.5, 6.3, 6.4, 9.2, 10, 18) **is not in this repo.** Get it into `docs/` — several code comments are traceable only to a document nobody working from this repo can read.
  **Effort:** M·risky · **Requirement:** — · **Skip risk:** Legal exposure, and code decisions justified by a document that cannot be checked · CRITICAL

- [x] **5.5 · Add authentication to the dashboard**
  There is none, and the PRD never mentions it. The success metrics assume login events exist to count (3.1).
  **Effort:** M · **Requirement:** — · **Skip risk:** Competitor intelligence, unpublished press valuations, and patient review content readable by anyone with the URL · CRITICAL
  **Status:** Done — checkbox retroactively corrected. This item's own Phase 5 status note explained it was left open only because "there's no HTTP framework yet for a login route to attach to (Phase 7)" — Phase 7 built exactly that (`POST /api/auth/login`, `Authorization: Bearer` on every other route, the mockup's login modal), closing the one thing this was waiting on. Not new work this pass; the checkbox was simply never revisited when Phase 7 shipped.

- [ ] **5.6 · Secrets at rest: `token.json` is a live refresh token**
  `oauth_setup.py:54` says so explicitly — *"Keep this file out of version control — it's a live credential."* `.gitignore` covers it correctly today. Decide where it lives in production, because a file next to the code is not it.
  **Effort:** M · **Requirement:** — · **Skip risk:** A long-lived Google credential with `business.manage` scope on a shared box · HIGH
  **Status (2026-09-11):** Partially done — left unchecked deliberately, since the actual decision this item asks for (where it lives in production) still can't be made without the hosting platform (1.4), unchanged. What's now real, not just recommended: `docs/decisions/08-secrets-at-rest.md`'s tier-2 option (env vars over a bare file — true "regardless of which platform 1.4 lands on," per that document's own reasoning) is implemented. `fetch_owned_reviews.load_credentials()` reads `GOOGLE_TOKEN_JSON` in preference to `TOKEN_FILE` when set, so a production host's secrets store can inject the credential as a plain env var today, with zero further code changes needed once 1.4 lands on a platform. Local dev is untouched (`GOOGLE_TOKEN_FILE` still the default, per the decision doc's own "local development is explicitly out of scope" note). One real tradeoff surfaced and handled honestly rather than papered over: an env var can't be rewritten by the running process, so a mid-run OAuth token refresh now prints the new value for an operator to update in whichever secrets store set `GOOGLE_TOKEN_JSON`, instead of silently writing to a file (which would defeat the point) or silently discarding the refreshed token (which would eventually break ingestion once the refreshed token itself expires). 5 new tests, 332 passing overall, ruff clean.

- [x] **5.7 · Dependency audit in CI**
  `pip-audit` on the Python side; whatever the frontend build ends up being on the other.
  **Effort:** S · **Requirement:** — · **Skip risk:** Known CVEs ship unnoticed · MEDIUM

- [x] **5.8 · Write down the C-4 control: no resale, redistribution, or model training on Reddit data**
  The PDF commits to it. It needs to be a documented control someone can point at, not an intention — particularly relevant if any part of the AI weekly summary (P1-1) is ever fed raw mention text.
  **Effort:** S · **Requirement:** C-4 · **Skip risk:** An accidental breach through a feature nobody connected to the commitment · HIGH

---

## Phase 6 — Sentiment classification and alert routing

**Status (2026-09-04, updated 2026-09-05): all 5 items closed on branch `phase-6-7-classification-api`.** `backend/app/classification.py` (6.1/6.2/6.3): one model call per item returns sentiment + confidence + crisis/digest routing together (originally `claude-opus-5`; switched to Groq on 2026-09-05 by explicit user direction, landing on `openai/gpt-oss-120b` after a live smoke test with a real API key caught the first-picked `llama-3.3-70b-versatile` as deprecated on Groq's side — see `docs/decisions/09-sentiment-classifier-choice.md`'s "Update" section) (the five-plus-five conditions copied verbatim from the mockup's `openAlertRulesModal()`), stores both the raw text and the result so re-scoring is always possible, and is what now overwrites a review's star-derived placeholder sentiment (6.2's reconciliation — `classified_at IS NULL` is the queryable seam between the two populations). `docs/decisions/09-sentiment-classifier-choice.md` proposes a concrete recall bar for the PRD's open question (recall ≥ 90% on Negative, ≥ 95% on `alert_category="crisis"` specifically, since a missed crisis is silent and unrecoverable while a false alarm merely wastes review time) — a recommendation for the team to ratify, not a unilateral bar. `backend/app/topic_tagging.py` (6.5) is scoped honestly as LLM **tagging** against the mockup's fixed five-topic taxonomy, not true unsupervised clustering (the item's literal title) — `docs/decisions/11-topic-tagging-approach.md` explains why: real clustering needs real ingestion volume no adapter has live yet, and building it against ~zero real data would be untestable. `docs/decisions/10-assignment-roster.md` (6.4) makes the existing `User` table (5.5) the roster, replacing the mockup's hardcoded four names — who owns keeping it current is named as still genuinely unresolved, not invented.

One real bug caught and fixed during review: the classifier initially defaulted to `claude-sonnet-5` on a self-directed cost/quality tradeoff — a direct violation of the `claude-api` skill's non-negotiable "always use `claude-opus-5` unless the user explicitly names a different model, never downgrade for cost" policy. Corrected before commit, with the decision doc's reasoning rewritten to match (Opus is the right call on task-fit merits too here, not just the mandated one). Also caught: `classify_sentiment()` only degraded gracefully on a malformed model response, not a transient API failure (rate limit, timeout) — the latter would have crashed a whole classification batch, contradicting this project's established "one bad item must not take down a batch" rule used throughout every adapter. Fixed, with a new test. `topic_tagging.py` originally returned `[]` silently for a missing API key too (the two sibling modules disagreed on this — flagged by both parallel agents' own final reports); reconciled to raise the same `ClassifierNotConfiguredError` as `classification.py`, since every remaining item in a batch would fail identically if the key is missing.

79 new/changed tests across this phase and Phase 7 combined (296 total passing), ruff clean, migration verified against real Postgres (`sentiment_confidence`/`classified_at`/`alert_category` columns added ahead of both this phase and the API layer specifically to avoid a cross-agent schema dependency).

- [x] **6.1 · Sentiment classifier, with the precision/recall bar decided first**
  The PRD's open question: *"what precision/recall bar is acceptable before it's trusted to drive the alert workflow unsupervised?"* Answer it before building, not after.
  **Effort:** L·risky · **Requirement:** P0-4 · **Skip risk:** Either the alert list is noise nobody reads, or it silently misses negatives — both destroy the core v1 metric · HIGH

  > **YOUR CALL — how is sentiment classified?**
  > **Hosted LLM per item.** Best quality on Taglish, code-switching, and the sarcasm that wrecks lexicon models — which matters a lot for PH social content. Costs per-item money and adds a latency and availability dependency to ingestion.
  > **Off-the-shelf multilingual sentiment model, self-hosted.** Free per item, predictable, offline-capable. Noticeably worse on mixed Tagalog/English, and on star-rating-free text like Reddit comments.
  > **Whatever the vendor provides.** Free if the vendor decision lands on Awario or Brand24, and one less thing to build. You inherit their definition of "negative" with no ability to tune it against your precision/recall bar — which is the exact thing the PRD says must be tuned.
  > **The lean:** hosted LLM, batched, with the raw text and the label both stored so you can re-score later. The volume here is one clinic group — a few hundred items a week per the mockup's own numbers — so per-item cost is genuinely small, and Taglish handling is where the accuracy actually lives.
  > **What would change my mind:** if the vendor decision lands somewhere with tunable sentiment *and* an exportable confidence score, take it — inheriting a tunable classifier beats building one.

- [x] **6.2 · Reconcile the two conflicting definitions of `sentiment`**
  `normalize_reviews()` derives it purely from stars (`>=4` Positive, `<=2` Negative). The mockup's feed items carry a text-derived sentiment. Same field name, two incompatible meanings, and they will be mixed in one feed.
  **Effort:** M · **Requirement:** P0-1, P0-2 · **Skip risk:** A single feed where "Negative" means two different things, sorted and filtered as if it means one · MEDIUM

- [x] **6.3 · Implement the Crisis Alert vs Daily Digest routing rules**
  The rules are already written and stakeholder-visible in the mockup's classification modal (`openAlertRulesModal()`, citing spec §9.2) — five crisis conditions, five digest conditions. This is a spec that already exists; implement it rather than reinventing it.
  **Effort:** L · **Requirement:** P0-4 · **Skip risk:** Every item routes the same way and the crisis path means nothing · HIGH

- [x] **6.4 · Assignment roster with an owner**
  PRD open question. The mockup hardcodes Gian, Paul, Boom, Mixi in `handleAssign()`.
  **Effort:** S · **Requirement:** P0-4 · **Skip risk:** Items get assigned to people who have left, or cannot be assigned at all · MEDIUM

- [x] **6.5 · Topic clustering**
  The mockup shows five themes with per-topic sentiment splits.
  **Effort:** L·risky · **Requirement:** P0-8 · **Skip risk:** Topics tab has no content · MEDIUM

---

## Phase 7 — API and the data-driven UI refactor

**Status (2026-09-04): 7.1, 7.2, 7.3, 7.5, 7.6, 7.7 closed; 7.4 partially done, left open.** Both the backend and the mockup refactor were built by separate parallel agents against one shared, hand-written contract (`docs/api-contract.md`) so they'd match without seeing each other's code — reconciled afterward: CORS was missing entirely (the mockup's `apiFetch()` would have been blocked by the browser against a real server; added `CORSMiddleware`, verified live with a real preflight + authenticated request against a running `uvicorn` process, not just the in-process `TestClient`). `app/api/` (7.1) implements every `GET` in the contract as a real, runnable FastAPI app — `uvicorn app.api.main:app`. `remedy-pulse-mockup.html`'s refactor (7.2/7.3/7.5/7.6/7.7) extracts every tab's hardcoded data into one module shaped to match the contract, renders everything via `createElement`/`textContent` (never `innerHTML` from concatenated data), computes relative time and the sync pill from real `GET /api/status`/`publishedAt` data, adds a shared loading/empty/error panel to all six tabs, and gates the Demo badge/simulate-mention/AI-summary-regenerate behind one `isDemoMode` flag — falling back to the original sample data (preserving the mockup's zero-install, zero-backend demo value) whenever no real API response is available.

**7.4 is genuinely partial, not fully closed:** `assign`/`resolve` are implemented on both the API (`POST /api/mentions/{id}/assign|resolve`) and the mockup (wired end-to-end, with an optimistic local update + honest failure toast on a save error) — reconciled in after the mockup agent's own report flagged that its instructions covered the read/fetch layer but not exhaustively wiring every write action. `reply` is implemented on the API (`POST /api/reviews/{mention_id}/reply`) but **not** wired in the mockup, and that's a real, deliberate gap: the mockup's reply flow operates on a whole branch-level listing (`pendingReplies`, a count), while the API's endpoint needs one specific review's `mention_id` — there is no clean mapping between "reply to the next pending review at this branch" and "reply to review #123" without either the UI listing individual pending reviews to pick from, or the API adding a "reply to any one pending review at this venue" endpoint. That's a product/API design decision, not a wiring task, and is documented at the point in the mockup's fetch layer where it was found rather than papered over with an incorrect implementation.

- [x] **7.1 · Read API per tab**
  **Effort:** L · **Requirement:** P0-1 … P0-11 · **Skip risk:** No path from store to screen · HIGH

- [x] **7.2 · Refactor the mockup from hardcoded markup to render-from-data**
  This is the *"Piece 2"* refactor `fetch_owned_reviews.py:1-15` explicitly defers — *"this script doesn't do that refactor itself, it just produces the input for it."* Six feed items, six EMV rows, four alerts, four review rows, and five topic cards are all hand-written markup today.
  **Effort:** L · **Requirement:** all P0 · **Skip risk:** The validated UI cannot show real data · HIGH

  > **YOUR CALL — refactor the mockup in place, or rebuild the UI in a framework?**
  > **Refactor in place.** Keeps the exact design stakeholders already validated, keeps the zero-build-step simplicity, and every hour goes into behaviour rather than setup. Costs you: 774 lines of vanilla JS with global functions wired through inline `onclick` attributes, which gets unpleasant as state grows — and it already has real state (filters, assignment, resolution, reply status).
  > **Rebuild in a framework.** Proper state management, component reuse, and an ecosystem for the table/chart work. Costs you a build pipeline, a re-validation risk on pixel differences, and days you may not have.
  > **The lean:** refactor in place, but extract the six tabs' data into one module and render from it — the halfway point that fixes the actual problem (markup is the data) without buying a toolchain. With 27 days to target, a framework migration competes directly with the P0 acceptance criteria in Phase 8.
  > **What would change my mind:** if the P2 multi-brand plan gets pulled forward, or if more than one person will be in this UI simultaneously, the framework's structure starts paying for itself and the in-place refactor becomes a second migration you'll pay for twice.

- [x] **7.3 · Escape all rendered content (implements 0.11)**
  `textContent` over `innerHTML`; no HTML built from mention text by concatenation; no `onclick` attributes carrying data.
  **Effort:** M · **Requirement:** P0-1 · **Skip risk:** Stored XSS · HIGH

- [x] **7.4 · Write API: assign, resolve, send reply — with timestamps for Phase 3**
  **Effort:** M · **Requirement:** P0-4, P0-5, P0-6 · **Skip risk:** Actions still reset on refresh, exactly as the demo guide says they do today · HIGH
  **Status (2026-09-11):** Done. Assign/resolve were already wired (Phase 7). This pass closed the one remaining gap this item's own note named — reply — by adding `POST /api/reviews/by-venue/{venue}/reply` (see 8.5's status note for the full design and why a venue-level endpoint, not a mention-id one, closes the actual UI/API mismatch) and wiring the mockup's `sendReply()` to it in live mode, with the same optimistic-update-then-reconcile idiom `handleAssign()`/`resolveAlertItem()` already established in Phase 8. Demo mode is unchanged (local-only, as before).

- [x] **7.5 · Real relative-time rendering and a PHT-correct sync pill (implements 0.12, 0.16)**
  **Effort:** S · **Requirement:** P0-1, P0-11 · **Skip risk:** Freshness and recency both misreported · MEDIUM

- [x] **7.6 · Empty states and error states on every tab**
  Both are named PRD edge cases: *"if there are zero new mentions in a period, I want a clear empty state rather than an ambiguous blank screen."* Only the alerts panel and the EMV table have one today.
  **Effort:** M · **Requirement:** P0-1, P0-11 · **Skip risk:** Blank screen reads as breakage; a failed source reads as quiet · MEDIUM

- [x] **7.7 · Gate or remove every demo affordance before it faces real data**
  The "Demo" badge, "+ Simulate mention" (see 0.13), `simulateSync()`, and the three canned AI summaries. Each needs an environment gate or removal — decided deliberately, not discovered at launch.
  **Effort:** S · **Requirement:** P1-3 · **Skip risk:** Simulated data indistinguishable from real data in a tool whose only job is being trusted · HIGH

---

## Phase 8 — The six tabs against real data

Each item closes a specific PRD acceptance criterion. This is the phase where "it works" becomes checkable rather than arguable.

- [x] **8.1 · Overview: Clarity Index, volume trend, attention summary, default landing (implements 0.10)**
  **Effort:** L · **Requirement:** P0-7 · **Skip risk:** No landing view · HIGH
  **Status:** Done. `GET /api/overview/trend` (+ `get_overview_trend()` in `app/repository.py`) closes the one real gap left after Phase 7 — the Sentiment Trend chart had no backing endpoint at all. Bucketed by `COALESCE(published_at, ingested_at)::date`, sentiment counts null-safe, capped at 90 days per 9.2. `remedy-pulse-mockup.html`'s chart is redrawn from real data (`renderSentimentTrendChart()`), with a matching 14-day `SAMPLE_DATA.overviewTrend` for demo mode. Verified against a real running server + real Postgres, not just `TestClient` (see `docs/runbook-backup-restore.md`'s container for how). Tests: `test_overview_trend_*` in `test_api_overview_mentions.py`.

- [x] **8.2 · Mentions: add the sentiment filter (from 0.19a)**
  **Effort:** S · **Requirement:** P0-2 · **Skip risk:** A stated P0 acceptance criterion fails · MEDIUM
  **Status:** Done (Phase 7) — re-verified this pass. The `Sentiment: All` filter chip and `applyMentionFilters()` were already correct; `POST /api/exports/mentions_csv`'s new query params (8.3) now also cover it server-side for exports specifically.

- [x] **8.3 · CSV export from the server-side filtered set**
  P0-3's acceptance criterion is *"exactly the filtered rows."* Today's client-side `style.display !== 'none'` check works only while every row is in the DOM — which stops being true the moment the feed is paginated.
  **Effort:** M · **Requirement:** P0-3 · **Skip risk:** Exports silently contain only the current page · MEDIUM
  **Status:** Done. `POST /api/exports/{type}` already existed server-side (Phase 7); this pass wired the mockup's three "Export CSV" chips to call it in live mode with the tab's current filter-chip state as query params (`currentMentionsExportParams()`/`currentEmvExportParams()`), downloading the real response body. Demo mode keeps the pre-existing DOM-scrape, since sample data has no API behind it. Verified against a real server (all three export types, plus a filtered mentions export). Test: `test_p0_3_csv_export_contains_exactly_the_filtered_rows` (9.1) + existing `test_api_exports_status.py` coverage.

- [x] **8.4 · Alerts and assignment, end to end, with real timestamps**
  **Effort:** M · **Requirement:** P0-4, P0-5 · **Skip risk:** The core v1 workflow, and the core v1 metric, do not exist · HIGH
  **Status:** Done. Assign/resolve were already wired (Phase 7); this pass fixed `handleAssign()`/`resolveAlertItem()`/`resolveAllAlerts()` in the mockup to overwrite `assignedAt`/`assignedTo`/`resolvedAt` from the API's own response body on success, instead of trusting only the client's `new Date()` guess — verified the real endpoints return real server timestamps. Also found and fixed a real bug while writing 9.1's tests: `get_overview_stats()`'s `activeAlerts` count had no `kind` restriction, so a classified-negative Google review (kind=review) would inflate the KPI forever with no way to see or resolve it, since the only alerts-list UI (`GET /api/mentions`) defaults to kind=mention. Fixed in `app/repository.py`; regression test `test_overview_active_alerts_excludes_review_kind_rows`.

- [x] **8.5 · Reviews: per-branch table, trend column, and the reply flow (implements 0.4, 0.14)**
  **Effort:** L · **Requirement:** P0-6 · **Skip risk:** Reviews tab cannot be populated · HIGH

  > **HEADS-UP — replying to a Google review is a different API surface from reading one, and "pending reply" is easy to read but hard to write.**
  > The reply endpoint (`accounts/*/locations/*/reviews/*/reply`) sits behind the same gated Business Profile access as review reads, and posting a reply is an irreversible public action taken in the clinic's name. Decide explicitly whether "Send reply" posts to Google or deep-links the user out to Business Profile to post it themselves. The mockup implies the former; the second is dramatically cheaper and lower-risk for v1, and the PRD's Non-Goals already establish the principle — *"the tool surfaces and routes items for a human to act on."* Worth recording in `docs/decisions/` either way.

  **Status (2026-09-11):** Done. The table/trend-column read side and the per-mention `POST /api/reviews/{id}/reply` write endpoint were already built and tested (Phase 7). This pass closed the remaining wiring gap, per the already-ratified decision in `docs/decisions/13-review-reply-flow.md` (deep-link out, never post reply text via API): added `POST /api/reviews/by-venue/{venue}/reply` — resolving "the oldest not-yet-replied review at this venue" server-side, since the mockup's reply flow is branch-level (`pendingReplies`, a count) and never had a specific review id to send the existing by-id endpoint. Wired the mockup's reply modal to it: a "Open in Business Profile ↗" link (from `GET /api/reviews`'s new `businessProfileUrl` field) for a human to post the actual reply, and a "Mark as replied" button (renamed from "Send reply" — more honest about what it actually does) that calls the new endpoint and reconciles `STATE.reviews` from the server's response, same optimistic-then-reconcile idiom as `handleAssign()`. **What's still honestly not done, and can't be without external input:** `businessProfileUrl` is `null` for all four branches — `config.py`'s `OWNED_LISTINGS` only has `REPLACE_ME_*` location-id placeholders (Phase 1 item 1.1's still-pending access request is what would supply real ones), and no plausible URL-construction scheme was fabricated to fill the gap. The UI falls back to Business Profile's generic top-level URL in the meantime, with an honest "(no direct link configured yet for this branch)" note. Verified: 319 backend tests pass (up from 313; includes a real-Postgres test of the new endpoint's `NULLS LAST` ordering, not just SQLite), ruff clean, and a real-browser (Playwright via the system's installed Edge, since no network access to fetch Playwright's own Chromium build) pass of the full modal → mark-as-replied → count-decrements flow with zero console errors.

- [x] **8.6 · Topics: drill-down to constituent mentions (from 0.19b)**
  P0-8's acceptance criterion is exactly this drill-down. Topic cards have no click handlers today.
  **Effort:** M · **Requirement:** P0-8 · **Skip risk:** A P0 acceptance criterion fails; a volume spike cannot be traced to its cause, which is the tab's whole purpose · MEDIUM
  **Status:** Done (Phase 7) — re-verified this pass. `buildTopicCardEl()`'s click handler opens `openTopicModal()`, which calls the real `GET /api/topics/{key}/mentions` in live mode with a demo-mode sample fallback.

- [ ] **8.7 · EMV engine, gated on formula sign-off**
  The roadmap is right that this should slip rather than ship unapproved. Two specifics from the mockup to resolve: the PeopleAsia row's detail text flags that it *"replaces the §6.4 worked example, which was tuned to the old illustrative Tier 2 base"* and asks someone to confirm — that is an open question sitting inside the UI. And Net Favorable (₱2,809,000) exceeds Gross (₱2,366,000) because the positive multiplier is ×1.2; confirm that is intended before leadership asks.
  **Effort:** L · **Requirement:** P0-9 · **Skip risk:** Peso figures in a leadership deck that finance has not approved · HIGH
  **Status:** Deliberately not built. `grossEmv`/`netEmv` are `null` on every article the API returns, by design (see `docs/api-contract.md`'s EMV section) — a real formula needs editorial-judgment inputs no connector or classifier can supply, and requires the sign-off this item names. Fabricating one is exactly what this checklist's own standing rule (don't invent decisions that need a human) forbids. `test_acceptance_p0.py::test_p0_9_...` is a deliberately-skipped test recording this, cross-referenced.

- [x] **8.8 · Competitors: share of voice **and** sentiment, plus alias matching (from 0.19c)**
  Includes the keyword-variant matching P0-10 requires — the mockup encodes brand aliases as HTML `title` tooltips ("Also matches: Remedy Skin Solutions, Remedy Skin, Remedy BGC…") and the Category Watch cards all read *"Boolean query pending."* Those tooltips are the requirement, stored in the only place they could be at mockup stage. Move them into config. See the 4.2 HEADS-UP before deciding where competitor sentiment comes from.
  **Effort:** L·risky · **Requirement:** P0-10 · **Skip risk:** The most vendor-dependent P0, and the roadmap's nominated first cut · MEDIUM
  **Status (2026-09-11):** Done. Share-of-voice + sentiment (the P0-10 acceptance criterion itself) were done and tested in Phase 7. This pass closed the remaining gap: `GET /api/competitors`'s `shareOfVoice` entries and `GET /api/reviews`'s `listings` entries now both carry an `aliases` field (always an array, `[]` when none are documented), layered in at the route level from `config.BRAND_ALIASES`/`config.OWNED_LISTING_ALIASES` — the same pattern `reviews.py` already used for `OWNED_LISTINGS`, so `config.py` stays the single source of truth rather than the alias strings living twice. The mockup's Competitors-tab Share of Voice legend and the Reviews-tab branch table both render the recovered "Also matches: …" tooltip from that field now, in both live and demo mode (demo-mode `SAMPLE_DATA` updated to match). Fixed one real regression this surfaced: `POST /api/exports/{type}`'s CSV writer used `csv.DictWriter`'s default `extrasaction="raise"`, which broke `reviews_csv` the moment its row dicts gained a field not in the export's column list — switched to `extrasaction="ignore"`, since an export's fieldnames list is a deliberate column allowlist, not an assertion about a row dict's exact shape. 314 tests pass (up from 313; 4 new/changed), ruff clean. **Still explicitly out of scope, unchanged from before:** `get_competitors_data()` still doesn't do alias-based *text matching* during ingestion (a stray "just tried Aivee Skin Spa" mention still isn't attributed to Aivee's share of voice) — that remains a real adapter-behavior change, not a display fix, per that function's own docstring. Category Watch's "Boolean query pending" cards are unchanged (still explicitly not promoted into real tracking, per `config.CATEGORY_WATCH_HAIR_PENDING`'s own comment — no decision was made to change that in this pass).

- [x] **8.9 · "Last synced" on every tab, read from the 2.4 run ledger**
  **Effort:** S · **Requirement:** P0-11 · **Skip risk:** Stale data indistinguishable from current · HIGH
  **Status:** Done (Phase 7) — re-verified this pass. `#syncPill` lives in `<header>`, outside every per-tab `<section class="view">`, so it's visible regardless of the active tab; `renderSyncPill()`/`mostRecentSuccessAt()` read the real 2.4 run ledger via `GET /api/status`.

---

## Phase 9 — QA, launch, and cutover

- [x] **9.1 · One acceptance test per PRD acceptance criterion**
  The PRD writes them in Given/When/Then already — eleven P0 criteria, ready to transcribe.
  **Effort:** L · **Requirement:** all P0 · **Skip risk:** "Done" is a matter of opinion at the sign-off meeting · HIGH
  **Status:** Done. `backend/tests/test_acceptance_p0.py` — one test per P0-N wherever the criterion is API-testable (10 of 11; P0-9 is a deliberately-skipped test, blocked on 8.7's still-open sign-off, not a testability gap). `docs/qa-manual-checklist.md` covers the three genuinely browser-only slivers this session has no browser to exercise (P0-2's live-as-you-type, P0-6's reply box opening, P0-7's fresh-load landing view) — each cross-referenced back to its automated counterpart so every one of the 11 criteria lands in exactly one authoritative place. Writing these tests also surfaced and fixed a real bug — see 8.4's status note.

- [x] **9.2 · Decide and implement the backfill window**
  PRD Non-Goals cap it at 90 days and start tracking from launch forward. Confirm that still holds — a dashboard that is empty on day one is a bad first impression for the leadership audience.
  **Effort:** M · **Requirement:** — · **Skip risk:** Launch day shows an empty product · MEDIUM
  **Status:** Done. `config.BACKFILL_WINDOW_DAYS = 90` (matching the PRD cap as-is), enforced via `app.repository.is_within_backfill_window()`, applied per-item in `news_job.py`/`reddit_job.py`/`meta_job.py` before an item counts as ingested. Deliberately NOT applied to owned Google reviews or Places competitor ratings (current-state snapshots, not discovery streams — see the exception's own comment in `google_reviews_job.py`) — filtering those by date would understate a branch's real all-time rating for no cost/volume benefit.

- [ ] **9.3 · Stakeholder demo and sign-off against the real thing**
  **Effort:** M · **Requirement:** — · **Skip risk:** Gaps found after the old tool is already switched off · HIGH
  **Status:** Not started. Genuinely a human/business step (scheduling a real demo with real stakeholders) — not something to fabricate a record of.

- [x] **9.4 · Runbook: what to do when a source fails**
  Who notices, how, and what they do. Directly serves the PRD's stale-data edge case.
  **Effort:** M · **Requirement:** P0-11 · **Skip risk:** A silent outage runs for days on a two-person team · HIGH
  **Status:** Done. `docs/runbook-source-failures.md` — verified against real code (the 8 exact `SOURCE_NAME`/ledger-source strings, the actual default sync cadence, the PRD's stale-data language quoted verbatim rather than paraphrased).

- [x] **9.5 · Backups with a *documented, executed* restore**
  A backup with no tested restore is a hope, not a backup.
  **Effort:** M · **Requirement:** — · **Skip risk:** Unrecoverable loss of assignment/resolution history — the only record of the metric v1 is judged on · CRITICAL
  **Status:** Done. `docs/runbook-backup-restore.md` documents a restore that was actually executed against the real local Postgres container — seeded identifiable data, `pg_dump`, a real `DROP DATABASE`, restore, and verification that every row, `alembic current`/`alembic check`, and the full Postgres-backed test suite all matched afterward. Explicitly scopes what this does and doesn't prove (production scheduling/retention and point-in-time recovery are named as open, not silently assumed).

- [ ] **9.6 · Run Remedy Pulse in parallel with Media Meter/MediaWatch for 30 days**
  The PRD's own success metric gates decommissioning on 30 days of stable use.
  **Effort:** M · **Requirement:** — · **Skip risk:** Old tool switched off before the new one is trusted, with no fallback · HIGH
  **Status:** Not started. Inherently time-gated (30 real calendar days of parallel use) — cannot be done or simulated in a single working session.

- [x] **9.7 · Formal MediaWatch decommission decision**
  **Effort:** S · **Requirement:** — · **Skip risk:** Paying for both indefinitely; the stated goal never lands · MEDIUM
  **Status:** Done. `docs/decisions/12-mediawatch-decommission.md` recommends a two-part gate — 9.6's 30-day parallel run completing AND a named sign-off — before decommissioning, rather than a bare calendar date.

---

## Sequencing

| # | Work | Why it sits here |
|---|---|---|
| 1 | **Phase 1** access requests | Pure calendar time you do not control. Every day of delay is a day the whole plan loses. Starts today, runs behind everything else. |
| 2 | **Phase 0** gap closure | Cheap, mostly small, and every item is a false claim someone is currently building on. Do it while Phase 1 is in the post. |
| 3 | **Phase 2** foundations | Nothing downstream can start without the schema and the store. 2.1 was never blocked on the vendor decision and still isn't. |
| 4 | **Phase 3** instrumentation | Before the features it measures, or there is no baseline at the 30-day review. Also constrains the Phase 2 schema, so it cannot come later. |
| 5 | **Phase 4** ingestion | Gated per-source on Phase 1 outcomes. Google first — it is the one source not blocked on the vendor decision. |
| 6 | **Phase 5** compliance | Immediately behind ingestion, never behind launch. See the hard constraint below. |
| 7 | **Phase 6** classification | Needs real ingested text to tune the precision/recall bar against. |
| 8 | **Phase 7** API + refactor | Needs the schema and something real to render. |
| 9 | **Phase 8** the six tabs | Where the P0 acceptance criteria actually close. |
| 10 | **Phase 9** QA and cutover | Last, and the parallel-run period genuinely takes 30 days — count backwards from when MediaWatch's contract ends. |

### Hard constraints that override the order

1. **No production ingestion against real third-party data until Phase 5 completes** — specifically 5.1 (Reddit deletion propagation), 5.3 (PII minimization across sources), and 5.5 (authentication). Not "before launch" — before the *first real ingestion run*. Once the data is in your store, the obligations are already live.
2. **8.7 (EMV) does not ship without written formula sign-off.** The roadmap already says slip rather than ship; the mockup itself contains an unresolved question about the PeopleAsia figure.
3. **6.1 (the precision/recall bar) is decided before 6.3 (alert routing) is built.** Routing tuned against an undefined accuracy target cannot be evaluated.
4. **3.3 (baseline) is captured before launch, not after.** It is the only item here that becomes permanently impossible if skipped.

### If the timeline forces a cut

The roadmap nominates Competitors first, and the evidence here supports that: it is the most vendor-dependent P0 (8.8), its only current data source has a documented 5-review sampling cap that makes competitor sentiment trends unreliable (4.2 HEADS-UP), it needs a design round that has not happened (0.19c), and it is the least connected to the stated core metric.

A defensible cut order, from first to last: **Competitors (P0-10) → Topics (P0-8) → EMV (P0-9) → the AI weekly summary and command palette (P1)**. What must not be cut is the chain that produces the core v1 metric: ingestion → classification → alerts → assignment → resolution timestamps. That chain *is* v1. Everything else is what makes v1 pleasant.

---

## Recommended: `docs/decisions/`

Create it, and record each YOUR CALL there once decided. Five are open in this document:

| Decision | Item |
|---|---|
| Fallback trigger dates for each access request | 1.6 |
| Persistence engine | 2.2 |
| Sentiment classification approach | 6.1 |
| Refactor the mockup in place vs. rebuild | 7.2 |
| Post review replies via API vs. deep-link out | 8.5 HEADS-UP |

For each: the decision, the options considered, the reasoning — and **what would change your mind.** That last line is the one that makes the document worth re-reading in a month, when the vendor decision lands or an access request gets rejected and half of these need revisiting.
