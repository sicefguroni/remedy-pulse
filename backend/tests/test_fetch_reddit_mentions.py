"""Tests for fetch_reddit_mentions.py (checklist 4.3 / 5.2 / 5.3).

No real PRAW/network calls anywhere here — praw itself may not even be
installed in this environment (see the module's own "no PRAW call/import
without credentials" design). Where a real praw.Reddit-shaped object
would normally sit, tests use plain objects (SimpleNamespace / a small
fake class) that only expose the attributes fetch_reddit_mentions.py
actually reads, and get_reddit_client()'s own `import praw` boundary is
exercised by injecting a fake module into sys.modules rather than
requiring the real package.
"""

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import fetch_reddit_mentions as r

# --- mask_reddit_username() ---


def test_mask_reddit_username_is_deterministic_for_same_input():
    first = r.mask_reddit_username("skinseeker_mnl")
    second = r.mask_reddit_username("skinseeker_mnl")
    assert first == second


def test_mask_reddit_username_differs_across_usernames():
    a = r.mask_reddit_username("skinseeker_mnl")
    b = r.mask_reddit_username("glow_ph_2024")
    assert a != b


def test_mask_reddit_username_does_not_contain_raw_handle_verbatim():
    masked = r.mask_reddit_username("skinseeker_mnl")
    assert "skinseeker_mnl" not in masked
    # Only a short recognizable prefix of the original may appear, not the
    # whole handle.
    assert masked.startswith("skin")


def test_mask_reddit_username_not_reversible_to_plaintext_via_bare_hash():
    # A bare sha256 of the raw username (no pepper) must NOT equal the
    # hash suffix used - otherwise the pepper/design adds nothing and the
    # output would be a plain, well-known, reversible-via-rainbow-table
    # hash of the handle.
    import hashlib

    masked = r.mask_reddit_username("skinseeker_mnl")
    suffix = masked.split("_")[-1]
    bare_hash = hashlib.sha256(b"skinseeker_mnl").hexdigest()[: len(suffix)]
    assert suffix != bare_hash


def test_mask_reddit_username_short_username_handled():
    masked = r.mask_reddit_username("ab")
    assert masked != "ab"
    assert masked.startswith("ab")


def test_mask_reddit_username_none_or_empty_maps_to_fixed_string():
    assert r.mask_reddit_username(None) == "Reddit user"
    assert r.mask_reddit_username("") == "Reddit user"


def test_mask_reddit_username_deleted_placeholder_maps_to_fixed_string():
    # Reddit's own placeholders, distinct from this function's own output
    # - must not be hashed as if they were real handles.
    assert r.mask_reddit_username("[deleted]") == "Reddit user"
    assert r.mask_reddit_username("[removed]") == "Reddit user"


def test_mask_reddit_username_different_users_never_collide_in_a_batch():
    usernames = ["skinseeker_mnl", "glow_ph_2024", "user123", "ab", "manila_derma_fan"]
    masked = {r.mask_reddit_username(u) for u in usernames}
    assert len(masked) == len(usernames)


# --- fullname_kind() ---


def test_fullname_kind_submission():
    assert r.fullname_kind("t3_abc123") == "submission"


def test_fullname_kind_comment():
    assert r.fullname_kind("t1_xyz789") == "comment"


def test_fullname_kind_unknown_prefix_returns_none():
    assert r.fullname_kind("t5_whatever") is None
    assert r.fullname_kind("") is None
    assert r.fullname_kind(None) is None


# --- missing_credentials() / get_reddit_client() ---


def test_missing_credentials_lists_every_unset_var(monkeypatch):
    monkeypatch.setattr(r, "CLIENT_ID", None)
    monkeypatch.setattr(r, "CLIENT_SECRET", None)
    monkeypatch.setattr(r, "REDDIT_USERNAME", None)
    monkeypatch.setattr(r, "REDDIT_PASSWORD", None)
    missing = r.missing_credentials()
    assert set(missing) == {
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
    }


def test_missing_credentials_empty_when_all_set(monkeypatch):
    monkeypatch.setattr(r, "CLIENT_ID", "id")
    monkeypatch.setattr(r, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(r, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(r, "REDDIT_PASSWORD", "pass")
    assert r.missing_credentials() == []


def test_get_reddit_client_raises_systemexit_when_credentials_missing(monkeypatch):
    monkeypatch.setattr(r, "CLIENT_ID", None)
    monkeypatch.setattr(r, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(r, "REDDIT_USERNAME", None)
    monkeypatch.setattr(r, "REDDIT_PASSWORD", None)
    with pytest.raises(SystemExit, match="REDDIT_CLIENT_ID"):
        r.get_reddit_client()


def test_get_reddit_client_never_imports_praw_when_credentials_missing(monkeypatch):
    # Guarantees "no PRAW call should be attempted without them" holds
    # even at the import level - if praw were imported, sys.modules would
    # gain an entry for it (or, if a fake was pre-seeded, it would be
    # touched); neither should happen when credentials are missing.
    monkeypatch.setattr(r, "CLIENT_ID", None)
    monkeypatch.setattr(r, "CLIENT_SECRET", None)
    monkeypatch.setattr(r, "REDDIT_USERNAME", None)
    monkeypatch.setattr(r, "REDDIT_PASSWORD", None)
    monkeypatch.delitem(sys.modules, "praw", raising=False)
    with pytest.raises(SystemExit):
        r.get_reddit_client()
    assert "praw" not in sys.modules


def test_get_reddit_client_builds_praw_reddit_with_expected_kwargs(monkeypatch):
    monkeypatch.setattr(r, "CLIENT_ID", "cid")
    monkeypatch.setattr(r, "CLIENT_SECRET", "csecret")
    monkeypatch.setattr(r, "REDDIT_USERNAME", "remedy_bot")
    monkeypatch.setattr(r, "REDDIT_PASSWORD", "pw")

    captured = {}

    def fake_reddit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    fake_praw_module = types.ModuleType("praw")
    fake_praw_module.Reddit = fake_reddit
    monkeypatch.setitem(sys.modules, "praw", fake_praw_module)

    client = r.get_reddit_client()

    assert captured == {
        "client_id": "cid",
        "client_secret": "csecret",
        "username": "remedy_bot",
        "password": "pw",
        "user_agent": r.USER_AGENT,
    }
    assert client.user_agent == r.USER_AGENT


def test_user_agent_matches_required_format_and_has_a_version():
    # Reddit's documented format: "<platform>:<app ID>:<version> (by
    # /u/<username>)". Pin the shape so a future edit can't accidentally
    # drop a segment.
    assert r.USER_AGENT.count(":") == 2
    assert "(by /u/" in r.USER_AGENT
    platform, app_id, rest = r.USER_AGENT.split(":", 2)
    assert platform == "python"
    assert app_id == "remedy-pulse-monitor"
    assert rest.split(" ", 1)[0].startswith("v")


# --- normalize_submission() ---


def _fake_submission(
    fullname="t3_abc123",
    author_name="skinseeker_mnl",
    title="Anyone tried Remedy BGC?",
    selftext="Went last week, results were great.",
    created_utc=1751190000.0,
    permalink="/r/PhilippinesSkincare/comments/abc123/anyone_tried/",
):
    author = None if author_name is None else SimpleNamespace(name=author_name)
    return SimpleNamespace(
        fullname=fullname,
        author=author,
        title=title,
        selftext=selftext,
        created_utc=created_utc,
        permalink=permalink,
    )


def test_normalize_submission_maps_fields_and_masks_author():
    row = r.normalize_submission(_fake_submission(), "PhilippinesSkincare", "Remedy BGC")
    assert row["platform"] == "Reddit"
    assert row["source"] == "reddit"
    assert row["redditKind"] == "submission"
    assert row["fullname"] == "t3_abc123"
    assert row["subreddit"] == "PhilippinesSkincare"
    assert row["matchedTerm"] == "Remedy BGC"
    assert row["author"] == r.mask_reddit_username("skinseeker_mnl")
    assert "skinseeker_mnl" not in row["author"]
    assert row["text"] == "Went last week, results were great."
    assert row["sourceUrl"] == "https://www.reddit.com/r/PhilippinesSkincare/comments/abc123/anyone_tried/"
    assert row["sentiment"] is None
    assert row["status"] == "ok"
    expected_published_at = datetime.fromtimestamp(1751190000.0, tz=timezone.utc).isoformat()
    assert row["publishedAt"] == expected_published_at


def test_normalize_submission_falls_back_to_title_when_no_selftext():
    submission = _fake_submission(selftext="")
    row = r.normalize_submission(submission, "PhilippinesSkincare", "Remedy BGC")
    assert row["text"] == "Anyone tried Remedy BGC?"


def test_normalize_submission_deleted_account_masks_to_reddit_user():
    submission = _fake_submission(author_name=None)
    row = r.normalize_submission(submission, "PhilippinesSkincare", "Remedy BGC")
    assert row["author"] == "Reddit user"


def test_normalize_submission_removed_body_marks_status_and_clears_text():
    submission = _fake_submission(selftext="[removed]")
    row = r.normalize_submission(submission, "PhilippinesSkincare", "Remedy BGC")
    assert row["status"] == "removed_at_fetch"
    assert row["text"] is None


def test_normalize_submission_no_permalink_yields_no_source_url():
    submission = _fake_submission(permalink=None)
    row = r.normalize_submission(submission, "PhilippinesSkincare", "Remedy BGC")
    assert row["sourceUrl"] is None


def test_normalize_submission_missing_created_utc_yields_no_published_at():
    submission = _fake_submission(created_utc=None)
    row = r.normalize_submission(submission, "PhilippinesSkincare", "Remedy BGC")
    assert row["publishedAt"] is None


# --- fetch_all_mentions() ---


class _FakeSubreddit:
    def __init__(self, results_by_term):
        self._results_by_term = results_by_term

    def search(self, term, limit):
        result = self._results_by_term.get(term, [])
        if isinstance(result, Exception):
            raise result
        return result


class _FakeReddit:
    def __init__(self, results_by_subreddit):
        self._results_by_subreddit = results_by_subreddit

    def subreddit(self, name):
        return self._results_by_subreddit[name]


def test_fetch_all_mentions_dedupes_same_submission_across_terms(monkeypatch):
    monkeypatch.setattr(r, "REDDIT_SUBREDDITS", ["PhilippinesSkincare"])
    monkeypatch.setattr(r, "REDDIT_SEARCH_TERMS", ["Remedy BGC", "Remedy Skin Clinic"])

    same_submission = _fake_submission(fullname="t3_dup")
    fake_subreddit = _FakeSubreddit(
        {
            "Remedy BGC": [same_submission],
            "Remedy Skin Clinic": [same_submission],
        }
    )
    reddit = _FakeReddit({"PhilippinesSkincare": fake_subreddit})

    mentions = r.fetch_all_mentions(reddit)
    assert len(mentions) == 1
    assert mentions[0]["fullname"] == "t3_dup"


def test_fetch_all_mentions_one_failing_query_does_not_abort_others(monkeypatch):
    monkeypatch.setattr(r, "REDDIT_SUBREDDITS", ["PhilippinesSkincare"])
    monkeypatch.setattr(r, "REDDIT_SEARCH_TERMS", ["Remedy BGC", "Remedy Skin Clinic"])

    ok_submission = _fake_submission(fullname="t3_ok")
    fake_subreddit = _FakeSubreddit(
        {
            "Remedy BGC": RuntimeError("boom"),
            "Remedy Skin Clinic": [ok_submission],
        }
    )
    reddit = _FakeReddit({"PhilippinesSkincare": fake_subreddit})

    mentions = r.fetch_all_mentions(reddit)
    assert len(mentions) == 1
    assert mentions[0]["fullname"] == "t3_ok"


def test_fetch_all_mentions_empty_results_yields_empty_list(monkeypatch):
    monkeypatch.setattr(r, "REDDIT_SUBREDDITS", ["PhilippinesSkincare"])
    monkeypatch.setattr(r, "REDDIT_SEARCH_TERMS", ["Remedy BGC"])
    fake_subreddit = _FakeSubreddit({"Remedy BGC": []})
    reddit = _FakeReddit({"PhilippinesSkincare": fake_subreddit})
    assert r.fetch_all_mentions(reddit) == []


# --- main() ---


def test_main_missing_credentials_raises_systemexit_and_writes_no_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(r, "CLIENT_ID", None)
    monkeypatch.setattr(r, "CLIENT_SECRET", None)
    monkeypatch.setattr(r, "REDDIT_USERNAME", None)
    monkeypatch.setattr(r, "REDDIT_PASSWORD", None)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        r.main()

    assert not (tmp_path / "reddit_mentions.json").exists()


def test_main_writes_fetched_at_and_mentions(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "CLIENT_ID", "id")
    monkeypatch.setattr(r, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(r, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(r, "REDDIT_PASSWORD", "pass")
    monkeypatch.chdir(tmp_path)

    fake_reddit_client = object()
    monkeypatch.setattr(r, "get_reddit_client", lambda: fake_reddit_client)
    monkeypatch.setattr(
        r,
        "fetch_all_mentions",
        lambda reddit: [r.normalize_submission(_fake_submission(), "PhilippinesSkincare", "Remedy BGC")],
    )

    r.main()

    import json

    with open(tmp_path / "reddit_mentions.json") as f:
        data = json.load(f)
    assert "fetchedAt" in data
    assert len(data["mentions"]) == 1
    assert data["mentions"][0]["fullname"] == "t3_abc123"
