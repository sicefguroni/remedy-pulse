# Remedy Pulse

A real-time reputation monitoring dashboard for Remedy, replacing Media Meter/MediaWatch with a single view of what people are saying online — reviews, mentions, competitor benchmarks, topic sentiment, and earned media value (EMV).

## Status

This is a **demo/mockup stage** project. The dashboard UI is built and interactive, but running on sample data — nothing is connected to live Google, Instagram, X, or Reddit feeds yet. The backend connector for pulling real Google review data exists but requires Google API access approval before it can go live.

## Repo layout

```
remedy-pulse-mockup.html   The interactive dashboard demo (open directly in a browser, no install needed)
backend/                   Google Business Profile / Places API connector for pulling real review data
docs/                      Demo guide and supporting reference docs
```

## Getting started

**To view the demo:** open [`remedy-pulse-mockup.html`](remedy-pulse-mockup.html) in any browser. See [`docs/README-Remedy-Pulse-Demo.md`](docs/README-Remedy-Pulse-Demo.md) for a full walkthrough of what's real vs. sample, and things to try.

**To set up the review data connector:** see [`backend/README.md`](backend/README.md) for the Google Cloud setup steps, API access requirements, and known limitations.

**To run the dashboard against a real local backend (not sample data):** see [`docs/local-dev-setup.md`](docs/local-dev-setup.md).

## What's next

Connecting the dashboard to live data sources (Google, Meta, Reddit, X) is the next phase of work, gated on Business Profile API access approval from Google. See `backend/README.md` for details on that blocker.
