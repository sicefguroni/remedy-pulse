"""fetch_rss.py — shared RSS/Atom connector for the key-free, approval-free
news and social sources (see docs/decisions/15-free-sources-first.md).

Why this module exists
-----------------------
Every source this project had wired before it needed something that had
not actually been obtained: Google Business Profile needed an OAuth grant
and an approved API access request, Places needed billing enabled on the
Cloud project, Reddit's API needed a script-app credential, Meta needed
three separate App Review outcomes, and GNews needed a key whose free
tier both rate-limits and delays results by 12 hours. The result was a
system that ran clean and ingested nothing — a review found every
configured source either erroring on a missing credential or reporting
SUCCESS with items_seen=0.

RSS is the answer to that, and it is deliberately the FIRST thing tried
now rather than the fallback: Google News, Bing News and Reddit all
publish full-text search feeds over plain HTTP with no key, no account,
no quota application and no approval queue. A source built on one of them
works the moment the code is written, which is the only property that
actually matters for proving the pipeline end to end.

What this module is, and is not
--------------------------------
It is a parser and a fetcher, nothing else. It does NOT know about
Mention rows, the ingestion ledger, or any particular feed — each
per-source job module (app/jobs/google_news_rss_job.py,
bing_news_rss_job.py, reddit_public_rss_job.py) owns its own feed URLs,
its own normalization into this project's Mention fields, and its own
ledger reporting, exactly the way fetch_news_articles.py relates to
app/jobs/news_job.py. That split is what lets one source's feed format
quirk stay in that source's job rather than leaking in here.

No new third-party dependency: RSS 2.0 and Atom are both parsed with
stdlib xml.etree.ElementTree, and dates with stdlib email.utils /
datetime. `feedparser` would be the obvious library reach here and is
deliberately not taken — the subset of these two formats that these three
feeds actually use is small enough to read in one screen, and a new pin
to audit (5.7's pip-audit gate) is a real cost for parsing this
mechanical.

Transport
----------
Requests go through http_utils.get_with_retry, so 429/5xx backoff,
Retry-After handling and RetryExhaustedError all behave identically to
every other connector in this project. A descriptive User-Agent is sent
on every request and is NOT optional: Reddit serves 429 to requests
carrying the python-requests default UA regardless of rate, and both
Google News and Bing are materially more tolerant of an identifiable
client. See USER_AGENT below.
"""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from http_utils import get_with_retry

# Mention.external_id is VARCHAR(512). Google News RSS <guid> values run
# past that — a real item during the first live run carried an 850-char
# base64 guid and the insert failed outright with
# StringDataRightTruncation. See stable_external_id() below.
EXTERNAL_ID_MAX_LENGTH = 512

# Sent on every request this module makes. Identifies the client and
# gives an operator a way to reach whoever is running it — the thing a
# feed host actually wants before it decides whether to serve you. Not
# cosmetic: Reddit returns 429 to the python-requests default UA on the
# very first request, which is what a hand-check of
# reddit.com/r/<sub>/new.rss ran into before this was set.
USER_AGENT = "RemedyPulse/1.0 (brand monitoring; +https://github.com/sicefguroni/remedy-pulse)"

# XML namespaces these feeds actually use. Atom for Reddit (its .rss
# endpoints serve Atom despite the extension), Dublin Core for the
# creator element Bing emits, content:encoded for full-text bodies.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}

# Root elements that mean "this really is a feed". RSS 2.0 uses <rss>,
# Atom uses <feed>, and RSS 1.0 uses RDF's <RDF> - see parse_feed() for
# why the root is checked at all rather than just parsed.
_FEED_ROOT_TAGS = {"rss", "feed", "rdf"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class FeedParseError(Exception):
    """Raised when a response body is not usable as RSS or Atom.

    Distinct from RetryExhaustedError (a transport failure worth
    retrying) — a feed that returns 200 with an HTML error page, or a
    host that refuses with a non-retryable 403/404, is a content problem,
    and the calling job records it as that feed's failure without
    retrying into the same wall."""


def strip_html(value: str | None) -> str | None:
    """Feed descriptions are HTML, not text — Google News wraps every
    description in an <a> plus a <font> byline, Reddit's Atom content is
    a full HTML div. Mention.text is read by the classifier (which pays
    per token) and rendered directly in the UI, so markup is stripped
    once here rather than at each of those two places.

    Deliberately a tag-strip and an entity-unescape, not a sanitizer —
    nothing downstream renders this as HTML, so the problem being solved
    is "unreadable text", not injection."""
    if value is None:
        return None
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def parse_feed_datetime(value: str | None) -> datetime | None:
    """Parse a feed's publication timestamp into an aware UTC datetime.

    Handles both formats these feeds emit — RFC 2822 ("Mon, 08 Sep 2026
    09:03:00 GMT", RSS 2.0's pubDate) and ISO-8601 ("2026-09-08T09:03:00
    +00:00", Atom's published/updated) — by trying each in turn.

    Returns None rather than raising on anything unparseable, matching
    app/jobs/news_job._parse_published_at()'s existing contract: one item
    with a malformed date must not abort the other forty in the same
    feed. A value that parses but carries no offset is treated as UTC,
    the same assumption app.scheduler._as_aware_utc() documents."""
    if not value:
        return None
    raw = value.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def stable_external_id(value: str, *, prefix: str = "sha256") -> str:
    """`value` if it fits in Mention.external_id, otherwise a digest of it.

    Found by running the first live pass, not by reading the schema:
    Google News RSS guids are base64 blobs that routinely exceed
    VARCHAR(512), and the insert failed with StringDataRightTruncation on
    a real article.

    Hashing rather than truncating, because external_id is the dedupe key
    — (source, external_id) is a UNIQUE constraint, and two different
    Google News guids share long common prefixes, so truncation would
    collapse distinct articles into one row and silently drop coverage.
    A digest of the whole value collides only if SHA-256 does.

    Hashing rather than widening the column, because the value being
    shortened is already opaque (a base64 redirector token, not something
    anyone reads) and the original is kept verbatim in
    Mention.raw_payload's guid field either way. Widening would migrate
    every row of a shared column to accommodate one source's format.

    Short values pass through unchanged, so Bing's plain article URLs and
    Reddit's "t3_1wcfylt" fullnames stay readable in the database."""
    if len(value) <= EXTERNAL_ID_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _first(item: ET.Element, *paths: str) -> str | None:
    """First non-empty text among `paths`, tried in order.

    The whole reason this helper exists: RSS and Atom disagree on the
    name of every field, and these feeds each pick a different subset.
    Rather than branch on "is this RSS or Atom" (a distinction the
    callers genuinely do not care about), each field is looked up as an
    ordered list of the names it goes by, and the first one present
    wins."""
    for path in paths:
        value = _text(item.find(path, _NS))
        if value is not None:
            return value
    return None


def _find_by_local_name(item: ET.Element, local_name: str) -> ET.Element | None:
    """First direct child whose tag is `local_name`, ignoring any XML
    namespace on it.

    Needed because Bing News puts the outlet in <News:Source>, and
    declares that `News` prefix as a namespace URI which is *the query
    URL itself* — so it differs on every single request:

        xmlns:News="https://www.bing.com/news/search?q=%22Belo...%22&format=RSS"

    There is no fixed URI to register in _NS, so the prefix cannot be
    matched the normal way. Matching on local name is what actually
    works here. Found by reading a live response after every Bing article
    came back with a null outlet — and a null outlet means a null EMV
    tier, so the whole EMV tab would have been unpriced for that source.
    """
    for child in item:
        if child.tag.split("}")[-1] == local_name:
            return child
    return None


def _link(item: ET.Element) -> str | None:
    """The item URL. RSS puts it in <link>'s text; Atom puts it in a
    <link href="..."> attribute with an empty body, so _first() alone
    would return None for every Reddit item."""
    direct = _first(item, "link")
    if direct:
        return direct
    for link in item.findall("atom:link", _NS) + item.findall("link"):
        rel = link.get("rel")
        href = link.get("href")
        if href and rel in (None, "alternate"):
            return href
    return None


def parse_feed(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom bytes into a list of plain dicts.

    Each dict carries the union of the fields the callers need — title,
    link, summary, published_at (already an aware datetime), author,
    outlet, guid — with None for anything this particular feed does not
    provide. Callers map these onto Mention fields themselves; nothing
    here assumes what an item "is" (a review, a mention, an article).

    `outlet` is the publication name Google News and Bing put in each
    item's <source> element. It is the field config.OUTLET_TIER_MAP is
    keyed on for EMV pricing, which is why it is extracted here rather
    than left in the raw payload for each job to dig out separately."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedParseError(f"response body is not valid XML: {exc}") from exc

    # The root element must actually be a feed. Parsing alone is not
    # enough of a check: an XHTML error page served with a 200 is
    # well-formed XML, so ET.fromstring() accepts it and findall() then
    # finds no items — and the caller reads that as "this search matched
    # nothing" rather than "this feed is broken".
    #
    # That is the precise failure this project was already caught
    # reporting: a source recording SUCCESS with items_seen=0 while
    # actually being unable to fetch anything. A wrong root tag is
    # therefore an error, not an empty result.
    tag = root.tag.split("}")[-1].lower()
    if tag not in _FEED_ROOT_TAGS:
        raise FeedParseError(
            f"response is well-formed XML but not a feed (root element <{tag}>) — "
            "this is usually an error page served with a 200"
        )

    items = root.findall(".//item") or root.findall(".//atom:entry", _NS)
    parsed: list[dict[str, Any]] = []
    for item in items:
        parsed.append(
            {
                "title": strip_html(_first(item, "title", "atom:title")),
                "link": _link(item),
                "summary": strip_html(
                    _first(item, "description", "content:encoded", "atom:content", "atom:summary")
                ),
                "published_at": parse_feed_datetime(
                    _first(item, "pubDate", "atom:published", "atom:updated", "dc:date")
                ),
                "author": _first(item, "dc:creator", "author", "atom:author/atom:name"),
                # Google News uses <source>, Bing uses <News:Source> with
                # a per-request namespace URI - see _find_by_local_name.
                "outlet": _text(_find_by_local_name(item, "source"))
                or _text(_find_by_local_name(item, "Source")),
                "guid": _first(item, "guid", "atom:id"),
            }
        )
    return parsed


def fetch_feed(url: str, *, timeout: int = 30) -> list[dict[str, Any]]:
    """GET `url` and parse it. Raises RetryExhaustedError (transport, from
    http_utils) or FeedParseError (content) — both of which every calling
    job catches per-feed, so one dead feed never blanks out the others in
    the same run."""
    response = get_with_retry(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if response.status_code != 200:
        raise FeedParseError(f"feed returned HTTP {response.status_code}")
    return parse_feed(response.content)
