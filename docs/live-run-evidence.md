# Live run evidence — 2026-09-14

What this system does when you run it, on real data, verified by running
it rather than by reading the code. Every number below was read out of
the database after the run that produced it. Commands are included so
this is reproducible, not just reported.

**Standard this document exists to meet:** nothing is done until it has
been run on real data and shown. A passing test suite is not that — the
suite was green throughout the period when no source returned data and
nobody could log in.

---

## The run

Starting from a **completely empty database** (`truncate mentions,
ingestion_runs, events restart identity cascade`), one command:

```
$ python -m app.scheduler
scheduler: google_reviews failed this pass: No token file at './token.json'. ...
Ran: news_google_rss, news_bing_rss, reddit_public, google_places_competitor,
     news_gnews, reddit, reddit_deletion_check, instagram_comments,
     instagram_mentions, facebook_comments, classification, topic_tagging
```

Start 05:16:35Z, end 05:22:41Z — **6 minutes 6 seconds**, unattended.

## What landed

```
$ python -m app.admin check

Mentions: 39 total, 39 classified, 0 pending

By source:
  news_google_rss                 19 rows     19 classified
  news_bing_rss                   13 rows     13 classified
  reddit_public                    7 rows      7 classified

Sentiment (classified rows only):
  Positive                        28
  Neutral                         10
  Negative                         1
```

39 real items, ingested, sentiment-classified, alert-routed and
topic-tagged, with nothing left pending. Topic tagging covered all 39;
30 came back `[]`, which is the correct answer — a corporate clinic-launch
announcement genuinely matches none of the service-experience topics in
the taxonomy, and `[]` ("tagged, found nothing") is stored distinctly from
`NULL` ("never tagged").

Real items from that run, as stored:

| Source | Item | Sentiment | Confidence |
| --- | --- | --- | --- |
| news_google_rss | "Remedy Skin Solutions Launches A More Personalized Approach…" | Positive | 0.96 |
| news_bing_rss | "Skin boosters offer 'snatched' looks with little downtime" | Negative | 0.90 |
| reddit_public | "Dermatologist reco in BGC please" | Neutral | 0.96 |
| reddit_public | "Any recos for best dermas in Metro Manila" | Neutral | 0.86 |

That first row is worth noting: it is genuine Remedy brand coverage,
found by a source that needs no key, on the same day GNews returned
`totalArticles: 0` for every configured Remedy brand term.

## Login and the API

```
$ python -m app.admin create-user --email demo@remedy.local --name "Demo Reviewer"
Password: ********
Created user #7: demo@remedy.local (Demo Reviewer)

$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/overview
401                                   # unauthenticated is refused

$ curl -X POST http://localhost:8000/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"demo@remedy.local","password":"..."}'
{"token":"NzoxNzg5NDA0MTg1.28c16dfc...","expires_at":"2026-09-14T16:43:05+00:00",
 "user":{"id":7,"email":"demo@remedy.local","display_name":"Demo Reviewer"}}

$ curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/overview
{"clarityIndex":{"score":27,"deltaVsLastWeek":-13},
 "totalMentions":{"value":3,"deltaPct":-62,"priorPeriodValue":8},
 "netSentiment":{"value":67,"deltaPts":29},
 "avgGoogleRating":{"value":0.0,"reviewCount":0},
 "activeAlerts":{"total":0,"crisis":0,"digest":0},
 "lastSyncedAt":"2026-09-14T05:19:34.684777+00:00"}
```

A wrong password returns 401 with the same body as an unknown email, so
the endpoint cannot be used to enumerate accounts. `/api/topics`,
`/api/mentions` and `/api/status` were exercised the same way and serve
the real rows above.

`avgGoogleRating` is honestly `0.0 / 0 reviews`: no Google source is live
yet. It is not a placeholder standing in for a number we do not have.

## Reproducing this

```bash
cd backend
docker compose up -d
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
python -m app.admin generate-secret     # -> SESSION_SECRET_KEY in .env
alembic upgrade head
python -m app.admin create-user --email you@example.com --name "You"
python -m app.scheduler
python -m app.admin check
```

The three sources that produced all 39 rows need **no key and no
account**. `GROQ_API_KEY` is the only credential required to reproduce
the classification and topic-tagging half; signup is self-serve and free.

One caveat on that, raised under "Unresolved" below: those jobs send every
mention's text to Groq, and that now includes Reddit content.

Run everything through the project venv. Invoking the scheduler with a
system Python that lacks the dependencies records an `ERROR` ledger row
reading "The `groq` package is not installed" — which looks like a broken
classifier but is a wrong interpreter. That exact confusion is in this
database's own history.

---

## What is honestly still not working

Reported as the ledger reports it, not smoothed over.

### `reddit_public` — PARTIAL, not SUCCESS

```
reddit_public  partial  seen=11  ingested=7
  "Skin Bar by Remedy": GET https://www.reddit.com/search.rss?... failed after
  4 retries: last status 429; "skin clinic" manila: ... last status 429
```

Two of seven queries were rate-limited. A 6-second inter-query delay took
this from 4 ingested to 7; it is better, not solved. The delay probably
needs to go to ~10s, or the query list needs trimming. **PARTIAL is the
correct status and the job reports it** — the point is that it says so
rather than recording SUCCESS.

`seen=11, ingested=7` is also the relevance filter working: 4 items were
fetched and deliberately rejected as off-topic.

### Sources returning nothing, and why

| Source | Status | Real reason |
| --- | --- | --- |
| `google_places_competitor` | success, 0 rows | Every `place_id` in `config.py` is still `REPLACE_ME`. Separately, the API key is valid but the Cloud project has **no billing enabled** — a direct call returns `REQUEST_DENIED: You must enable Billing`. Both ours to fix. |
| `news_gnews` | success, 0 rows | The key works. Every configured Remedy brand term returns `totalArticles: 0` — the brand has almost no press coverage. Not a defect. |
| `google_reviews` | error | `No token file at './token.json'` — needs the Business Profile API access request Google has not granted. |
| `reddit` (praw) | error | `Missing required Reddit credential(s)`. **Not obtainable by us.** Reddit closed self-serve registration: app creation is now gated behind the Responsible Builder Policy and every OAuth token needs manual approval. The free path asks you to declare developer / researcher / moderator — and we have already signed a commercial Data Access Request stating "Commercial developer / enterprise partner... not personal or academic use." Taking the free path would contradict that in writing, which the policy explicitly prohibits. The commercial request is pending and unanswered. |
| `reddit_deletion_check` | error | Same credential. **This matters beyond data volume:** it is the 48-hour deletion-propagation commitment made in writing in the Reddit Data Access application. It has never run. |
| Instagram / Facebook ×3 | not_configured | Meta App Review, three separate scopes. |

**One** of these is ours and costs nothing: Google Places billing. It was
described as two in an earlier draft of this document — the Reddit script
app was self-serve when the connector was written and is not any more.
Corrected rather than left standing.

### Open items found during this run

1. **`tier` is `NULL` on every article.** `config.OUTLET_TIER_MAP` holds
   six outlets under exact names ("Rappler", "Philippine Star"). The live
   feeds return "PhilStar Global on MSN", "GMA Network", "sugbo",
   "BusinessMirror", "Tatler Asia". Unmapped outlets are deliberately left
   unpriced rather than guessed, so **the EMV tab has nothing to price
   until Marketing extends that map.** It is a business-judgment call, not
   an engineering one.
2. **Reddit content now reaches an LLM, against a signed commitment.**
   The submitted Reddit Data Access Request states that Reddit data "is not
   resold, redistributed, or used to train any model"
   (`docs/decisions/07-reddit-c4-no-resale-control.md`). The `reddit_public`
   source added in this change stores Reddit text, and the classification
   and topic-tagging jobs send every mention to Groq — so 7 Reddit threads
   have already been sent. Inference is not training, which is a defensible
   reading, but decision record 07 flags the inference case as arguable and
   predicted this exact situation before it happened. **This needs a ruling
   from whoever signed the request (Angelo Mojica), not an engineer's
   judgement.** Until then the volume is small and the fix is cheap: record
   07 already specifies a source tag checked before any LLM call.
3. **Deletion propagation does not cover `reddit_public` rows.**
   `reddit_deletion_job` re-checks via praw, the credential this source
   exists to not need. An unauthenticated re-check path is not written.
   Stated here rather than assumed to work.
4. **Search and relevance terms are an engineer's first pass.** The
   Mentions feed is only as good as `config.py`'s query lists; Marketing
   should review them.
5. **No alerts fired.** `activeAlerts: 0` is correct — one Negative item
   was routed `digest`, not `crisis`. The crisis path has not been
   exercised on real data because no genuine crisis-grade mention has
   appeared yet.

---

## Bugs this run found

All four were invisible to a green test suite and to reading the code.

**1. Enrichment erased on every re-ingest.** Every ingestion adapter
passes `sentiment=None` (correctly — a connector must not invent one).
`upsert_mention`'s last-write-wins rule applied that `NULL` to the stored
value, so each 12-hourly re-fetch **wiped the classifier's verdict**. It
did not self-heal: `classified_at` is not a field adapters pass, so it
survived, and `classify_unclassified_batch()` selects on `classified_at
IS NULL` — the row stayed permanently blank while every status surface
called it classified. Fixed via `repository.ENRICHMENT_FIELDS`; four
regression tests. **Only a second pass over the same item produces this**,
which is exactly why one clean run and a green suite both missed it.

**2. Topic tagging was never wired to run.** `app/topic_tagging.py` was
complete and tested with no caller — the identical defect the classifier
had. Every mention sat at `topics = NULL`, so the Topics tab rendered
empty against a full database. Fixed by `app/jobs/topic_tagging_job.py`.

**3. Oversized `external_id`.** Google News guids are base64 blobs
routinely over `VARCHAR(512)`; a real article killed the insert with
`StringDataRightTruncation`. Hashed, not truncated — these guids share
long prefixes, and `(source, external_id)` is UNIQUE, so truncation would
have collapsed distinct articles into one row and silently dropped
coverage.

**4. Bing outlet always `NULL`.** Bing puts the outlet in `<News:Source>`
and declares that prefix against a namespace URI that *is the query URL*,
so it differs per request and cannot be matched as a fixed prefix. A null
outlet means a null EMV tier. Matched on local name instead. The same
read of a live response caught Bing's `apiclick.aspx` redirector, whose
per-request tracking parameters would have re-inserted every article on
every run.

---

## Credentials

`backend/.env` was included in a review archive with live keys; those were
rotated. Root cause was the packaging step, not the ignore rules — nothing
sensitive was ever committed, verified against full history. Archives are
now built by `scripts/package_release.py` from git-tracked files only,
scanned, and CI fails on a committed secret. Full write-up:
[`docs/security/2026-09-14-env-in-distributed-archive.md`](security/2026-09-14-env-in-distributed-archive.md).

## Test suite

409 passed, 1 skipped (was 345). The additions cover the three new
sources, the RSS layer, the topic-tagging job, the admin CLI, and the four
bugs above. Stated last, deliberately: the suite was green the whole time
this system was not working, and this document — not the test count — is
the status of record.
