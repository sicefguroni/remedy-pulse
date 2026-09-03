# Remedy Pulse — Demo Guide

## What is this?

This is a **preview** of Remedy Pulse, the tool we're building to replace Media Meter/MediaWatch and give us a real-time view of what people are saying about Remedy online.

Think of it as a **model of the house before construction starts** — you can walk through every room, flip the light switches, and open the cabinets, but the plumbing and electricity aren't connected to the city grid yet. That comes next, once everyone agrees this is the right layout.

## How to open it

Double-click the file `remedy-pulse-mockup.html`. It opens in your web browser (Chrome, Safari, etc.) — no installation and no login required. It also works fully offline: the page falls back to built-in system fonts if it can't reach the internet, so it stays readable and functional, just with slightly different typography than intended. When you are online, it additionally pulls its intended Google Fonts (Space Grotesk, Inter, IBM Plex Mono) for the polished look shown in this guide.

## What's real vs. what's sample

Everything you see — the numbers, the reviews, the alerts, the ₱ figures — is **sample data**, not live information pulled from Google, Instagram, Reddit, etc. It's built using real examples from our actual press coverage (like the real PeopleAsia and Rappler articles) so the numbers feel grounded and honest, not made up from nothing.

You'll see a small **"Demo"** badge in the top right corner as a constant reminder of this.

## Six sections to explore

| Tab | What it's for |
|---|---|
| **Overview** | The morning check-in — health score, mention volume, and what needs attention today |
| **Competitors** | Remedy vs. Belo, Aivee, and others — something our old tool couldn't do at all |
| **Mentions** | A single feed of everything being said about us, across every platform |
| **Reviews** | Star ratings and reply status, per clinic branch |
| **Topics** | What people are actually talking about — grouped into themes with sentiment |
| **EMV** | Puts a peso figure on our press coverage, with the math shown for every article |

*(Note: the Competitors tab reflects a later build phase, not day one — it's shown here so we can agree on what it should look like before that work starts.)*

## Fun things to try (the "wow" tour)

This version does more than just look real — a lot of it actually behaves like a real product:

- **Press ⌘K (or Ctrl+K)** anywhere to open a quick command bar — jump to any tab, resolve alerts, export data, or simulate a new mention, all from one search box. Or click the **⌘K** button in the top right.
- **Click the bell icon** in the header to see a dropdown of everything currently needing attention.
- **"This Week, Summarized"** on the Overview page is a taste of an AI-written executive briefing — click **Regenerate** to see it rewrite itself.
- **Click "+ Simulate mention"** on the Mentions page to watch a brand-new mention drop into the feed in real time, exactly like it would once the tool is live. (Try switching the "Live" toggle to "Paused" first — the simulation politely refuses, just like the real thing would.)
- **Search box on Mentions** actually filters the feed as you type — that part is fully real, not simulated.
- **"⭳ Export CSV"** buttons on the Mentions, Reviews, and EMV pages genuinely download a real spreadsheet file you can open in Excel — try it.
- **Click any row in the EMV table** to expand the exact formula behind that peso number, so nothing is ever a black box.
- **Assign / View / Resolve** on any alert in Overview — assigning lets you pick a real teammate's name, and resolving it updates the alert count live.
- **Click "1 pending reply"** on the Reviews page to open a reply box — sending it updates the status instantly.
- **Click the "Last synced" pill** in the header to simulate a data refresh.

None of these actions save anywhere permanently or send anything to a real person — if you refresh the page, everything resets to how it started (except downloaded CSV files, which are real files that stay on your computer). That's expected at this stage.

## What's not built yet (and why that's OK right now)

- Nothing is connected to real Google, Instagram, X, Reddit, or news data yet.
- "Assign," "Resolve," and "Send reply" don't notify anyone or post anywhere in real life.
- The AI summary cycles through three pre-written examples rather than generating something new each time.
- Some features (like Competitors) are planned for a later phase, not day one.

The point of this stage is to agree on **what the tool should look, feel, and behave like** before the harder engineering work — connecting to Google, Meta, Reddit, and X — begins. If something feels off, confusing, or missing, now is the cheapest and easiest time to say so.

## Questions this demo is meant to help answer

- Does this layout and these interactions match how you'd actually use the tool day to day?
- Is the AI weekly summary something leadership would actually want, and does the tone feel right?
- Does the EMV number-crunching make sense, or does it need adjusting?
- Is anything missing that you'd expect to see on day one? Anything that feels over-built for a first version?

## Who to ask

If something doesn't make sense or you'd like to see it work differently, flag it directly — nothing in this document or the demo is locked in.
