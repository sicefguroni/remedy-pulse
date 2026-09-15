"""Tests for the three key-free ingestion jobs — google_news_rss_job,
bing_news_rss_job and reddit_public_rss_job.

Each job's fetch_feed is monkeypatched in that job's own namespace (they
each do `from fetch_rss import ... fetch_feed`), the same approach
test_jobs_news.py uses — nothing here makes a real network call.

The behaviours pinned below are the ones that were actually wrong at some
point during this work, not a generic smoke test: the "SUCCESS with
items_seen=0" reporting gap, the oversized external_id that failed a real
insert, the subreddit rows that would have rendered as human mentions,
and the off-topic post that came back as a top brand mention.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.jobs.bing_news_rss_job as bing_job
import app.jobs.google_news_rss_job as gnews_job
import app.jobs.reddit_public_rss_job as reddit_job
from app.models import IngestionRun, Mention, RunStatus
from fetch_rss import FeedParseError
from http_utils import RetryExhaustedError

# Relative, never a hardcoded date: the 90-day backfill window
# (is_within_backfill_window) filters on real wall-clock time, so a fixed
# fixture date silently ages out of the window — the exact regression
# test_jobs_meta.py already had to fix once.
RECENT = datetime.now(timezone.utc) - timedelta(days=5)
TOO_OLD = datetime.now(timezone.utc) - timedelta(days=200)


def _article(*, guid="g1", link="https://news.google.com/rss/articles/abc?oc=5",
             title="Belo Medical Group wins award", outlet="Rappler", published_at=RECENT):
    return {
        "title": title,
        "link": link,
        "summary": f"{title} summary",
        "published_at": published_at,
        "author": None,
        "outlet": outlet,
        "guid": guid,
    }


def _reddit_item(*, guid="t3_abc123", title="Dermatologist reco in BGC please",
                 summary="Please help me find a good clinic.", published_at=RECENT,
                 author="/u/someone"):
    return {
        "title": title,
        "link": f"https://www.reddit.com/r/skincare_ph/comments/{guid}/x/",
        "summary": summary,
        "published_at": published_at,
        "author": author,
        "outlet": None,
        "guid": guid,
    }


@pytest.fixture(autouse=True)
def _no_polite_delay(monkeypatch):
    """reddit_public_rss_job sleeps SECONDS_BETWEEN_QUERIES between
    queries to stay under Reddit's unauthenticated rate limit. That is
    correct in production and pure dead time here - seven queries per run
    across several tests added minutes to the suite. Zeroed for tests
    only; the real value stays in the module where it belongs."""
    monkeypatch.setattr(reddit_job, "SECONDS_BETWEEN_QUERIES", 0)


def _latest_run(session, source):
    return session.execute(
        select(IngestionRun)
        .where(IngestionRun.source == source)
        .order_by(IngestionRun.started_at.desc())
    ).scalars().first()


# --- google_news_rss_job ---


def test_google_news_ingests_articles_and_maps_the_outlet_tier(sqlite_session, monkeypatch):
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(outlet="Rappler")])

    gnews_job.run(sqlite_session)

    mention = sqlite_session.execute(
        select(Mention).where(Mention.source == "news_google_rss")
    ).scalars().one()
    assert mention.kind == "article"
    assert mention.venue == "Rappler"
    # Rappler is in config.OUTLET_TIER_MAP; the tier must come from there,
    # never be guessed, or the EMV tab silently misprices a placement.
    assert mention.tier == "National News"
    # The classifier owns sentiment. A connector that invents one puts a
    # value into alert routing that no model ever produced.
    assert mention.sentiment is None


def test_google_news_leaves_tier_none_for_an_unmapped_outlet(sqlite_session, monkeypatch):
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(outlet="Some Blog")])

    gnews_job.run(sqlite_session)

    mention = sqlite_session.execute(select(Mention)).scalars().one()
    assert mention.venue == "Some Blog"
    assert mention.tier is None


def test_google_news_hashes_an_oversized_guid_into_external_id(sqlite_session, monkeypatch):
    """The bug the first live run hit: a real article carried an 850-char
    base64 guid and the INSERT failed with StringDataRightTruncation."""
    long_guid = "CBMi" + "A" * 900
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(guid=long_guid)])

    gnews_job.run(sqlite_session)

    mention = sqlite_session.execute(select(Mention)).scalars().one()
    assert mention.external_id.startswith("sha256:")
    assert len(mention.external_id) <= 512
    # The full guid is still recoverable - it is kept verbatim in the raw
    # payload, which is the whole reason hashing the key is acceptable.
    assert mention.raw_payload["guid"] == long_guid


def test_google_news_dedupes_the_same_article_across_queries(sqlite_session, monkeypatch):
    """Every query in FREE_NEWS_QUERIES returns the same item - a real
    pattern, since the competitor and category queries overlap."""
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(guid="same")])

    gnews_job.run(sqlite_session)

    assert len(sqlite_session.execute(select(Mention)).scalars().all()) == 1


def test_google_news_rerun_does_not_double_insert(sqlite_session, monkeypatch):
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(guid="stable")])

    gnews_job.run(sqlite_session)
    gnews_job.run(sqlite_session)

    assert len(sqlite_session.execute(select(Mention)).scalars().all()) == 1


def test_google_news_excludes_articles_outside_the_backfill_window(sqlite_session, monkeypatch):
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(published_at=TOO_OLD)])

    gnews_job.run(sqlite_session)

    assert sqlite_session.execute(select(Mention)).scalars().all() == []
    # The policy working as intended is not a data-quality problem: the
    # item is not counted as seen either.
    assert _latest_run(sqlite_session, "news_google_rss").items_seen == 0


def test_google_news_records_error_not_success_when_every_query_fails(sqlite_session, monkeypatch):
    """The precise failure this project was caught reporting: a source
    recording SUCCESS having ingested nothing."""

    def always_fails(url):
        raise FeedParseError("feed returned HTTP 503")

    monkeypatch.setattr(gnews_job, "fetch_feed", always_fails)

    gnews_job.run(sqlite_session)

    run = _latest_run(sqlite_session, "news_google_rss")
    assert run.status == RunStatus.ERROR
    assert "503" in run.error


def test_google_news_records_partial_when_only_some_queries_fail(sqlite_session, monkeypatch):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryExhaustedError("failed after 4 retries: last status 429")
        return [_article(guid=f"g{calls['n']}")]

    monkeypatch.setattr(gnews_job, "fetch_feed", flaky)

    gnews_job.run(sqlite_session)

    run = _latest_run(sqlite_session, "news_google_rss")
    assert run.status == RunStatus.PARTIAL
    assert "429" in run.error
    # The successful queries still stored their rows.
    assert run.items_ingested > 0


def test_google_news_feed_url_pins_the_philippine_market():
    url = gnews_job.feed_url('"Belo Medical Group"')
    assert "hl=en-PH" in url and "gl=PH" in url and "ceid=PH%3Aen" in url


# --- bing_news_rss_job ---


def test_bing_news_keys_on_the_publisher_url_not_a_redirector(sqlite_session, monkeypatch):
    """Bing's <link> is the publisher's own URL and it emits no <guid>, so
    the link is both url and external_id - the one real difference from
    the Google News job."""
    monkeypatch.setattr(
        bing_job,
        "fetch_feed",
        lambda url: [_article(guid=None, link="https://www.manilatimes.net/x", outlet="Rappler")],
    )

    bing_job.run(sqlite_session)

    mention = sqlite_session.execute(select(Mention)).scalars().one()
    assert mention.external_id == "https://www.manilatimes.net/x"
    assert mention.url == "https://www.manilatimes.net/x"


def test_bing_news_feed_url_requests_rss_not_html():
    """Without format=RSS this endpoint returns a web page, and every
    fetch would raise FeedParseError."""
    assert "format=RSS" in bing_job.feed_url("skin clinic Philippines")


def test_bing_and_google_store_the_same_article_separately(sqlite_session, monkeypatch):
    """Uniqueness is (source, external_id), so an article both feeds carry
    is one row per source rather than the two jobs racing to own one."""
    shared = "https://www.manilatimes.net/shared-story"
    monkeypatch.setattr(bing_job, "fetch_feed", lambda url: [_article(guid=None, link=shared)])
    monkeypatch.setattr(gnews_job, "fetch_feed", lambda url: [_article(guid=shared, link=shared)])

    bing_job.run(sqlite_session)
    gnews_job.run(sqlite_session)

    sources = {m.source for m in sqlite_session.execute(select(Mention)).scalars().all()}
    assert sources == {"news_bing_rss", "news_google_rss"}


# --- reddit_public_rss_job ---


def test_reddit_public_ingests_a_real_thread(sqlite_session, monkeypatch):
    monkeypatch.setattr(reddit_job, "fetch_feed", lambda url: [_reddit_item()])

    reddit_job.run(sqlite_session)

    mention = sqlite_session.execute(select(Mention)).scalars().one()
    assert mention.kind == "mention"
    assert mention.external_id == "t3_abc123"
    # The "/u/" prefix is stripped so this matches the author format the
    # praw adapter already stores, rather than two spellings of one thing.
    assert mention.author == "someone"
    # Title and body are joined - the title frequently carries the whole
    # point, and a link post has no body at all.
    assert "Dermatologist reco in BGC please" in mention.text
    assert "Please help me find a good clinic." in mention.text


def test_reddit_public_skips_subreddit_results(sqlite_session, monkeypatch):
    """search.rss returns subreddits (t5_) alongside posts - r/MANILA came
    back for "skin clinic manila". Ingesting one would put a sidebar blurb
    in the Mentions feed as something a person said."""
    monkeypatch.setattr(
        reddit_job,
        "fetch_feed",
        lambda url: [
            _reddit_item(guid="t5_2qphr", title="Manila", summary="Manila / Philippines"),
            _reddit_item(guid="t3_real"),
        ],
    )

    reddit_job.run(sqlite_session)

    stored = sqlite_session.execute(select(Mention)).scalars().all()
    assert [m.external_id for m in stored] == ["t3_real"]


def test_reddit_public_rejects_an_off_topic_post(sqlite_session, monkeypatch):
    """The [M4A] post that came back as a top brand mention on the first
    live run: an off-topic body that happened to contain "dermatologist"
    once, hundreds of words in."""
    monkeypatch.setattr(
        reddit_job,
        "fetch_feed",
        lambda url: [
            _reddit_item(
                guid="t3_offtopic",
                title="26 [M4A] Looking for someone interesting to hang out with",
                summary="I am a freelance writer. " * 40 + "I saw a dermatologist once.",
            )
        ],
    )

    reddit_job.run(sqlite_session)

    assert sqlite_session.execute(select(Mention)).scalars().all() == []
    run = _latest_run(sqlite_session, "reddit_public")
    # Seen but not ingested, so the ledger shows the filter working rather
    # than silently shrinking the result.
    assert run.items_seen == 1
    assert run.items_ingested == 0


def test_reddit_public_keeps_a_competitor_named_in_the_body(sqlite_session, monkeypatch):
    """A named entity counts anywhere in the post: a cashback-promos
    roundup listing Aivee Clinic genuinely is competitor coverage."""
    monkeypatch.setattr(
        reddit_job,
        "fetch_feed",
        lambda url: [
            _reddit_item(
                guid="t3_promos",
                title="Credit Card Cashback Promos Compilation (August 2026)",
                summary="BDO: 10% off at Aivee Clinic until month end.",
            )
        ],
    )

    reddit_job.run(sqlite_session)

    assert len(sqlite_session.execute(select(Mention)).scalars().all()) == 1


def test_reddit_public_feed_url_sorts_by_new():
    """Relevance ordering would re-surface the same year-old thread every
    run and bury the one posted an hour ago, which makes a cadence
    pointless."""
    assert "sort=new" in reddit_job.feed_url('"skin clinic" manila')


def test_reddit_public_is_relevant_scopes_the_two_term_lists_differently():
    # Named entity: anywhere.
    assert reddit_job.is_relevant("Random thread title", "someone mentioned Belo Medical Group")
    # Category word: title only.
    assert reddit_job.is_relevant("Dermatologist reco in BGC please", "")
    assert not reddit_job.is_relevant("Random thread title", "I saw a dermatologist once")
    assert not reddit_job.is_relevant(None, None)


# --- Bing feed-format specifics (both found by reading a live response) ---


def test_bing_unwraps_the_apiclick_redirector_to_the_publisher_url():
    """A real link from a live Bing response. The redirector carries
    per-request tracking parameters (tid, c), so storing it as the
    external_id would make the same article look new on every pass and
    re-insert it forever."""
    link = (
        "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=6aa7819368c6"
        "&url=https%3a%2f%2fwww.pep.ph%2fnews%2f44776%2fruffa%2f1%2f&c=91217&mkt=en-ph"
    )
    assert bing_job.resolve_publisher_url(link) == "https://www.pep.ph/news/44776/ruffa/1/"


def test_bing_passes_through_a_link_that_is_not_a_redirector():
    """An unrecognized shape is passed through, never dropped."""
    assert bing_job.resolve_publisher_url("https://www.manilatimes.net/x") == (
        "https://www.manilatimes.net/x"
    )
    assert bing_job.resolve_publisher_url(None) is None


def test_bing_dedupes_a_rerun_despite_changing_tracking_parameters(sqlite_session, monkeypatch):
    """The reason unwrapping happens before the dedupe key is taken: Bing
    hands back a different tid/c on every request for the same article."""
    target = "https%3a%2f%2fwww.pep.ph%2fnews%2f44776%2fx%2f"

    def feed_with_tracking(tid):
        link = f"http://www.bing.com/news/apiclick.aspx?tid={tid}&url={target}&c={tid}"
        return lambda url: [_article(guid=None, link=link)]

    monkeypatch.setattr(bing_job, "fetch_feed", feed_with_tracking("AAA"))
    bing_job.run(sqlite_session)

    monkeypatch.setattr(bing_job, "fetch_feed", feed_with_tracking("BBB"))
    bing_job.run(sqlite_session)

    stored = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(stored) == 1
    assert stored[0].url == "https://www.pep.ph/news/44776/x/"


def test_parse_feed_reads_the_outlet_from_a_namespaced_source_element():
    """Bing puts the outlet in <News:Source> and declares that prefix
    against a namespace URI that is the query URL itself — so it differs
    on every request and cannot be registered as a fixed prefix. A null
    outlet means a null EMV tier, i.e. an unpriced article."""
    from fetch_rss import parse_feed

    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0" xmlns:News="https://www.bing.com/news/search?q=%22x%22&amp;format=RSS">'
        b"<channel><item>"
        b"<title>Belo Medical Group denounce fake posts</title>"
        b"<link>https://www.pep.ph/news/44776/x/</link>"
        b"<pubDate>Mon, 08 Sep 2026 23:05:00 GMT</pubDate>"
        b"<News:Source>PEP</News:Source>"
        b"</item></channel></rss>"
    )
    [item] = parse_feed(xml)
    assert item["outlet"] == "PEP"
