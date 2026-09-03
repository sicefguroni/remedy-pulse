# Decision: Clarity Index formula

**Status:** Decided (v0.1, mockup stage) · **Requirement:** P0-7 · **Item:** 0.10

## Decision

The Clarity Index is a weighted composite of four inputs, each normalized to a 0–100 scale,
implemented as `computeClarityIndex(inputs)` in `remedy-pulse-mockup.html`:

```
ClarityIndex = round(
    0.15 * ratingScore        +   // Avg. Google Rating, normalized: (rating / 5) * 100
    0.40 * sentimentScore     +   // Net Sentiment KPI, clamped to 0-100 (already a -100..100 scale)
    0.20 * responseRateScore  +   // Average review response rate across branches (already 0-100)
    0.25 * volumeTrendScore       // Mention-volume trend, transformed around a 50 baseline:
                                   //   clamp(50 + weekOverWeekMentionChangePct, 0, 100)
)
```

With the sample data already in the mockup (Avg. Google Rating 5.0, Net Sentiment +62, branch
response rates 100/88/95/80, mentions 342 vs. 291 last week → +18%), this computes to **75**,
matching the demo narrative ("Clarity Index climbed to 75, up 6 points this week").

## Options considered

1. **Equal weighting across all four inputs (25% each).** Simplest to explain, but treats a
   single-channel, high-intent signal (Google rating) as equally important as a cross-platform
   sentiment read — and with this sample data it overweights the (very high) rating and pushes
   the score to the low-to-mid 80s, further from what the narrative copy already says.
2. **Rating-led score (rating as the majority weight).** Rejected — Google reviews are only one
   of six tracked channels (Google, Reddit, Instagram, Facebook, News, TikTok per the Source
   Breakdown chart), and letting one channel dominate defeats the point of a cross-source index.
3. **Sentiment-and-response-only, no volume term.** Considered, since volume trend is the
   noisiest of the four. Rejected because a sudden spike in mention volume (positive or negative)
   changes how much the sentiment reading matters that week, which is worth capturing — just at
   a modest weight, not zero.
4. **The weighting actually chosen** (15 / 40 / 20 / 25 — rating / sentiment / response rate /
   volume trend). Selected.

## Reasoning — why each weight

- **Sentiment mix, 40% (heaviest).** The only input that spans every tracked platform, not just
  Google — the closest single number to "what people are actually saying," which is the thing
  the index exists to summarize. It uses the page's own Net Sentiment KPI value, clamped to
  0–100 (a raw -100..100 net score, so a deeply negative week can pull below the midpoint).
- **Mention-volume trend, 25%.** A change in how much people are talking shifts how much the
  sentiment reading matters this week, so it earns real weight — but it's a volume signal, not a
  quality signal, so it stays below sentiment and response rate. The `50 +` transform means a
  flat week (0% change) scores a neutral 50, not 0, so the metric only rewards or penalizes
  relative to a flat baseline rather than treating "no growth" as a failure.
- **Review response rate, 20%.** The one input entirely inside the clinic's own control —
  rewards actually closing the loop on reviews, which is the core workflow this tool is built to
  drive (P0-6, the Reviews tab's reply flow).
- **Avg. Google Rating, 15% (lightest).** High-intent and public-facing, but the narrowest
  channel — Google only, and only from patients who chose to leave a rating — so it is not
  allowed to dominate the composite the way it would under equal weighting.

## What would change your mind

- **If a real precision/recall-tested sentiment classifier lands (Phase 6, item 6.1)** with a
  documented confidence score, sentiment's weight should probably go *up* further, since it
  would stop being a single blended KPI and become a directly auditable per-mention signal.
- **If review volume becomes large enough that a handful of ratings no longer represent the
  whole patient base**, the rating input should shift from "current average" to something
  trend-aware (e.g., rolling 30-day average vs. prior 30 days) rather than a snapshot — a
  snapshot is fine at ~230 total reviews across branches, less fine at scale.
- **If stakeholders push back that the index feels disconnected from the alerts they're acting
  on**, that's a sign volume trend or response rate is over- or under-weighted relative to what
  actually predicts a bad week — re-derive weights from a few months of real `alert → resolution`
  data instead of judgment calls, the way EMV's rate card was sourced from Media Meter's actual
  card rather than invented.
