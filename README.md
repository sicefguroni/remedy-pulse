# Remedy Pulse

A real-time reputation monitoring dashboard for Remedy, replacing Media Meter/MediaWatch with a single view of what people are saying online — reviews, mentions, competitor benchmarks, topic sentiment, and earned media value (EMV).

## Status

This is a **demo/mockup stage** project. The dashboard UI is built and interactive, but running on sample data — nothing is connected to live Google, Instagram, X, or Reddit feeds yet. The backend connector for pulling real Google review data exists but requires Google API access approval before it can go live.

## Repo layout

```
index.html   The dashboard frontend — sample data if you're not logged in, real data once you are
backend/                   Google Business Profile / Places API connector for pulling real review data
docs/                      Demo guide and supporting reference docs
```

`index.html` was renamed from `remedy-pulse-mockup.html` (checklist 0.26) once it started serving real, logged-in sessions too, not just the demo — a "mockup" filename in a real user's address bar was the wrong signal to send.

## Getting started

**To view the demo:** open [`index.html`](index.html) in any browser. See [`docs/README-Remedy-Pulse-Demo.md`](docs/README-Remedy-Pulse-Demo.md) for a full walkthrough of what's real vs. sample, and things to try.

**To set up the review data connector:** see [`backend/README.md`](backend/README.md) for the Google Cloud setup steps, API access requirements, and known limitations.

**To run the dashboard against a real local backend (not sample data):** see [`docs/local-dev-setup.md`](docs/local-dev-setup.md).

**To deploy a real, reachable instance on free-tier cloud services:** see [`docs/runbook-deploy-free-tier.md`](docs/runbook-deploy-free-tier.md) (also published as an interactive artifact, linked at the top of that file).

## What's next

Connecting the dashboard to live data sources (Google, Meta, Reddit, X) is the next phase of work, gated on Business Profile API access approval from Google. See `backend/README.md` for details on that blocker.
