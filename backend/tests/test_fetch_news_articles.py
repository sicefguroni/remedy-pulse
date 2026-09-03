import fetch_news_articles as n


def _raw_article(outlet="Rappler", url="https://rappler.com/a1"):
    return {
        "title": "Remedy BGC clinic review",
        "description": "A look at the new Rejuran treatment.",
        "url": url,
        "publishedAt": "2026-06-29T09:03:00Z",
        "source": {"name": outlet, "url": "https://rappler.com"},
    }


def test_normalize_maps_known_outlet_to_tier_and_ok_status():
    row = n.normalize(_raw_article(outlet="Rappler"))
    assert row["outlet"] == "Rappler"
    assert row["tier"] == "National News"
    assert row["status"] == "ok"
    assert row["headline"] == "Remedy BGC clinic review"
    assert row["publishedAt"] == "2026-06-29T09:03:00Z"


def test_normalize_all_six_seeded_outlets_resolve_to_their_documented_tier():
    expected = {
        "Rappler": "National News",
        "Philippine Star": "National News",
        "Manila Bulletin": "National News",
        "PeopleAsia": "Lifestyle Mag",
        "When In Manila": "Lifestyle Mag",
        "ANC": "Broadcast TV",
    }
    for outlet, tier in expected.items():
        row = n.normalize(_raw_article(outlet=outlet))
        assert row["tier"] == tier, outlet
        assert row["status"] == "ok"


def test_normalize_unmapped_outlet_gets_null_tier_not_a_guess():
    row = n.normalize(_raw_article(outlet="Some Random Blog"))
    assert row["tier"] is None
    assert row["status"] == "unmapped_outlet"


def test_normalize_sentiment_is_always_none():
    # Sentiment classification is explicitly out of scope for this
    # connector (Phase 6's job) - pin that so a future edit doesn't
    # accidentally start inventing a sentiment value here.
    row = n.normalize(_raw_article())
    assert row["sentiment"] is None


def test_normalize_missing_source_name_handled_without_crashing():
    raw = _raw_article()
    raw["source"] = {}
    row = n.normalize(raw)
    assert row["outlet"] is None
    assert row["tier"] is None
    assert row["status"] == "unmapped_outlet"


def test_dedupe_by_url_removes_repeats_keeps_first_occurrence():
    a1 = _raw_article(url="https://rappler.com/a1")
    a1["title"] = "first"
    a1_dup = _raw_article(url="https://rappler.com/a1")
    a1_dup["title"] = "duplicate"
    a2 = _raw_article(url="https://rappler.com/a2")
    deduped = n.dedupe_by_url([a1, a1_dup, a2])
    assert len(deduped) == 2
    assert deduped[0]["title"] == "first"
    assert {a["url"] for a in deduped} == {"https://rappler.com/a1", "https://rappler.com/a2"}


def test_dedupe_by_url_keeps_articles_with_no_url():
    a_no_url = {"title": "no url", "url": None, "source": {"name": "X"}}
    deduped = n.dedupe_by_url([a_no_url, a_no_url])
    assert len(deduped) == 2
