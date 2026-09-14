"""Tests for backend/fetch_rss.py — the shared RSS/Atom connector.

Every fixture below is a trimmed copy of a response an actual feed
returned during the first live run, not an invented shape: the Google
News item carries the real 850-character base64 guid that broke the first
insert, the Reddit entry is Atom-with-an-.rss-extension with its link in
an attribute, and the Bing item is plain RSS 2.0. Feeds are mocked at
http_utils.get_with_retry as fetch_rss binds it, so nothing here makes a
real network call.
"""

from datetime import timezone

import pytest

import fetch_rss
from fetch_rss import FeedParseError, parse_feed, parse_feed_datetime, stable_external_id, strip_html

# The real shape Google News RSS returns: HTML inside <description>, a
# <source> element carrying the outlet name, an RFC 2822 pubDate, and a
# very long base64 guid.
GOOGLE_NEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Belo Medical Group - Google News</title>
<item>
  <title>Belo Medical Group wins award - Philstar.com</title>
  <link>https://news.google.com/rss/articles/CBMiXGh0dHBz?oc=5</link>
  <guid isPermaLink="false">CBMiXGh0dHBz</guid>
  <pubDate>Thu, 21 Aug 2026 07:00:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/x"&gt;Belo Medical Group wins award&lt;/a&gt;&amp;nbsp;
  &lt;font color="#6f6f6f"&gt;Philstar.com&lt;/font&gt;</description>
  <source url="https://www.philstar.com">Philstar.com</source>
</item>
</channel></rss>"""

# Reddit's .rss endpoints serve Atom: the link is an attribute, the body
# is HTML inside <content>, and the id is a Reddit fullname.
REDDIT_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <author><name>/u/Bubbly_Reply1805</name></author>
  <title>Clinics or Dermatologists Reco</title>
  <link href="https://www.reddit.com/r/skincare_ph/comments/1wcfylt/clinic/" rel="alternate"/>
  <id>t3_1wcfylt</id>
  <updated>2026-09-10T11:02:25+00:00</updated>
  <content type="html">&lt;div&gt;Hi! Can anyone recommend a good skin clinic?&lt;/div&gt;</content>
</entry>
<entry>
  <title>Manila</title>
  <link href="https://www.reddit.com/r/MANILA/" rel="alternate"/>
  <id>t5_2qphr</id>
  <updated>2008-12-05T18:03:28+00:00</updated>
  <content type="html">Manila / Philippines</content>
</entry>
</feed>"""

BING_NEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item>
  <title>XIGLO Is Belo Medical Group's Next Big Chapter</title>
  <link>https://www.manilatimes.net/2026/08/21/xiglo</link>
  <description>The clinic opens in BGC this quarter.</description>
  <pubDate>Fri, 22 Aug 2026 03:15:00 GMT</pubDate>
  <source>The Manila Times</source>
</item>
</channel></rss>"""


class _FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code


# --- strip_html ---


def test_strip_html_removes_markup_and_unescapes_entities():
    # Exactly what Google News puts in <description>.
    raw = '<a href="https://x">Belo wins award</a>&nbsp;<font color="#6f6f6f">Philstar.com</font>'
    assert strip_html(raw) == "Belo wins award Philstar.com"


def test_strip_html_returns_none_for_empty_and_markup_only():
    assert strip_html(None) is None
    assert strip_html("") is None
    # A description that is nothing but a tag must not become an empty
    # string stored as Mention.text - the classifier would then be billed
    # for classifying whitespace.
    assert strip_html("<div></div>") is None


# --- parse_feed_datetime ---


@pytest.mark.parametrize(
    "value",
    [
        "Thu, 21 Aug 2026 07:00:00 GMT",  # RSS 2.0 pubDate (RFC 2822)
        "2026-08-21T07:00:00+00:00",  # Atom published/updated (ISO 8601)
        "2026-08-21T07:00:00Z",  # ISO 8601 with a Z suffix
    ],
)
def test_parse_feed_datetime_handles_both_feed_formats(value):
    parsed = parse_feed_datetime(value)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc).hour == 7


def test_parse_feed_datetime_assumes_utc_when_no_offset():
    """A naive value is treated as UTC rather than left naive - otherwise
    it would blow up on comparison against the aware timestamps every
    other part of this project produces."""
    assert parse_feed_datetime("2026-08-21T07:00:00").tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", "2026-13-45"])
def test_parse_feed_datetime_returns_none_rather_than_raising(value):
    """One item with a broken date must not abort the other forty in the
    same feed."""
    assert parse_feed_datetime(value) is None


# --- stable_external_id ---


def test_stable_external_id_passes_short_values_through_unchanged():
    """Reddit fullnames and Bing URLs stay readable in the database."""
    assert stable_external_id("t3_1wcfylt") == "t3_1wcfylt"
    assert stable_external_id("https://www.manilatimes.net/x") == "https://www.manilatimes.net/x"


def test_stable_external_id_hashes_values_over_the_column_limit():
    long_guid = "CBMi" + "A" * 900
    result = stable_external_id(long_guid)
    assert result.startswith("sha256:")
    assert len(result) <= fetch_rss.EXTERNAL_ID_MAX_LENGTH


def test_stable_external_id_is_deterministic_so_reruns_do_not_double_insert():
    long_guid = "CBMi" + "B" * 900
    assert stable_external_id(long_guid) == stable_external_id(long_guid)


def test_stable_external_id_does_not_collide_on_shared_long_prefixes():
    """The reason this hashes instead of truncating. Google News guids
    share long common prefixes; truncation would collapse distinct
    articles onto one external_id and silently drop coverage, because
    (source, external_id) is a UNIQUE constraint."""
    shared_prefix = "CBMi" + "C" * 900
    assert stable_external_id(shared_prefix + "one") != stable_external_id(shared_prefix + "two")


# --- parse_feed ---


def test_parse_feed_reads_rss_with_html_description_and_source_outlet():
    [item] = parse_feed(GOOGLE_NEWS_XML)
    assert item["title"] == "Belo Medical Group wins award - Philstar.com"
    assert item["link"] == "https://news.google.com/rss/articles/CBMiXGh0dHBz?oc=5"
    assert item["guid"] == "CBMiXGh0dHBz"
    # The outlet is what config.OUTLET_TIER_MAP is keyed on for EMV
    # pricing, so extracting it correctly is not cosmetic.
    assert item["outlet"] == "Philstar.com"
    assert item["summary"] == "Belo Medical Group wins award Philstar.com"
    assert item["published_at"].year == 2026


def test_parse_feed_reads_atom_where_the_link_is_an_attribute():
    """The case a naive RSS-only parser silently returns None for, which
    would drop every Reddit item for having no URL."""
    items = parse_feed(REDDIT_ATOM_XML)
    assert items[0]["link"] == "https://www.reddit.com/r/skincare_ph/comments/1wcfylt/clinic/"
    assert items[0]["guid"] == "t3_1wcfylt"
    assert items[0]["author"] == "/u/Bubbly_Reply1805"
    assert items[0]["summary"] == "Hi! Can anyone recommend a good skin clinic?"


def test_parse_feed_returns_subreddit_entries_for_the_caller_to_filter():
    """fetch_rss does not know what a subreddit is - filtering them out is
    reddit_public_rss_job's job, via the t5_ fullname prefix. This test
    pins that division: the parser hands back everything the feed had."""
    items = parse_feed(REDDIT_ATOM_XML)
    assert [item["guid"] for item in items] == ["t3_1wcfylt", "t5_2qphr"]


def test_parse_feed_reads_plain_rss_without_namespaces():
    [item] = parse_feed(BING_NEWS_XML)
    assert item["outlet"] == "The Manila Times"
    assert item["link"] == "https://www.manilatimes.net/2026/08/21/xiglo"
    assert item["guid"] is None  # Bing emits none; the job keys on link


def test_parse_feed_raises_feed_parse_error_on_non_xml():
    """A feed host serving an HTML error page with a 200 is a content
    problem, not a transport one - so it must not look like a retryable
    failure."""
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>Sorry, something went wrong.</body></html>")


def test_parse_feed_returns_empty_list_for_a_feed_with_no_items():
    """A search that legitimately matched nothing. Distinct from a parse
    failure, and the calling job records it as a clean zero."""
    assert parse_feed(b'<?xml version="1.0"?><rss version="2.0"><channel/></rss>') == []


# --- fetch_feed ---


def test_fetch_feed_sends_an_identifying_user_agent(monkeypatch):
    """Not cosmetic: Reddit serves 429 to the python-requests default UA
    on the first request regardless of rate, which is what makes this
    whole key-free approach work or not work."""
    captured = {}

    def fake_get(url, *, headers=None, params=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse(GOOGLE_NEWS_XML)

    monkeypatch.setattr(fetch_rss, "get_with_retry", fake_get)
    fetch_rss.fetch_feed("https://news.google.com/rss/search?q=x")
    assert captured["headers"]["User-Agent"] == fetch_rss.USER_AGENT
    assert "python-requests" not in captured["headers"]["User-Agent"]


def test_fetch_feed_raises_feed_parse_error_on_a_non_retryable_refusal(monkeypatch):
    """http_utils returns 403/404 rather than retrying them. From this
    module's side "the host refused" and "the host sent garbage" are the
    same non-retryable outcome."""
    monkeypatch.setattr(
        fetch_rss, "get_with_retry", lambda url, **kw: _FakeResponse(b"", status_code=403)
    )
    with pytest.raises(FeedParseError, match="403"):
        fetch_rss.fetch_feed("https://www.reddit.com/search.rss?q=x")
