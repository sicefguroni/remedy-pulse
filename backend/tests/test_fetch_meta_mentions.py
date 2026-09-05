"""Tests for fetch_meta_mentions.py. No credentials exist in this
environment and there is no Meta App Review access yet (checklist 1.3) —
every HTTP call here is mocked; nothing hits a real Graph API endpoint.

Mocking convention matches test_http_utils.py: a small FakeResponse
stand-in for requests.Response, monkeypatched in as this module's own
get_with_retry so no real network call is possible, routed by a URL
substring so nested per-media/per-post loops can each get their own
canned response without depending on call order.
"""

import fetch_meta_mentions as fmm
import fetch_owned_reviews as owned_reviews


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def _router(routes):
    """routes: an ordered list of (url_suffix, FakeResponse) pairs.
    Returns a fake get_with_retry(url, params=None) matched by the URL's
    trailing edge name (e.g. ".../media", ".../comments") — robust to
    nested loops calling the same edge for several different parent IDs
    in a single test, and deliberately NOT a plain substring match: a
    fake media/post id containing the edge name itself (e.g. an id like
    "media-1" inside ".../media-1/comments") would otherwise false-match
    the wrong route."""

    def fake(url, params=None, **kwargs):
        for suffix, resp in routes:
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"Unexpected URL in test: {url}")

    return fake


def _permission_denied_response():
    return FakeResponse(
        status_code=400,
        json_data={"error": {"type": "OAuthException", "code": 200, "message": "permission denied"}},
    )


# --- mask_instagram_handle ---


def test_mask_instagram_handle_consistent_same_handle_same_value():
    assert fmm.mask_instagram_handle("glowwithsab") == fmm.mask_instagram_handle("glowwithsab")


def test_mask_instagram_handle_different_handles_differ():
    assert fmm.mask_instagram_handle("glowwithsab") != fmm.mask_instagram_handle("skinseeker_mnl")


def test_mask_instagram_handle_normalizes_at_sign_and_case():
    assert fmm.mask_instagram_handle("@GlowWithSab") == fmm.mask_instagram_handle("glowwithsab")


def test_mask_instagram_handle_empty_or_none_falls_back():
    assert fmm.mask_instagram_handle(None) == "ig_unknown"
    assert fmm.mask_instagram_handle("") == "ig_unknown"


def test_mask_instagram_handle_is_not_a_truncation_like_google_style():
    # mask_reviewer_name() (the Google case) deliberately keeps a
    # human-readable fragment of the original (first name). An Instagram
    # handle has no such "safe partial" to keep, so this must not just
    # slice the string the way the Google function does - prove the
    # output contains none of the original handle.
    masked = fmm.mask_instagram_handle("glowwithsab")
    assert "glowwithsab" not in masked
    assert "glow" not in masked
    assert masked.startswith("ig_user_")


# --- Facebook masking reuses mask_reviewer_name() (not reimplemented) ---


def test_facebook_masking_reuses_mask_reviewer_name_not_reimplemented():
    assert fmm.mask_reviewer_name is owned_reviews.mask_reviewer_name


# --- fetch_instagram_comments ---


def test_fetch_instagram_comments_not_configured_without_ig_id(monkeypatch):
    monkeypatch.setattr(
        fmm, "get_with_retry", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP call expected"))
    )
    result = fmm.fetch_instagram_comments("token", None)
    assert result == {"capability": "instagram_comments", "status": "not_configured", "items": [], "error": None}


def test_fetch_instagram_comments_ok_path_normalizes_and_masks(monkeypatch):
    media_resp = FakeResponse(json_data={"data": [
        {"id": "media-1", "permalink": "https://instagram.com/p/abc", "timestamp": "2026-06-01T00:00:00+0000"},
    ]})
    comments_resp = FakeResponse(json_data={"data": [
        {"id": "c1", "text": "Great service!", "username": "@GlowWithSab", "timestamp": "2026-06-02T00:00:00+0000"},
    ]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/media", media_resp),
        ("/comments", comments_resp),
    ]))

    result = fmm.fetch_instagram_comments("token", "ig-123")

    assert result["status"] == "ok"
    assert result["error"] is None
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["platform"] == "instagram"
    assert item["author"] == fmm.mask_instagram_handle("@GlowWithSab")
    assert item["author"] != "@GlowWithSab"
    assert item["text"] == "Great service!"
    assert item["sentiment"] is None
    assert item["date"] == "2026-06-02T00:00:00+0000"
    assert item["sourceUrl"] == "https://instagram.com/p/abc"
    assert item["raw"] == {"comment_id": "c1", "media_id": "media-1"}


def test_fetch_instagram_comments_access_denied_is_independent_status(monkeypatch):
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/media", _permission_denied_response()),
    ]))

    result = fmm.fetch_instagram_comments("token", "ig-123")

    assert result["status"] == "access_denied"
    assert result["items"] == []
    assert result["error"] is not None


def test_fetch_instagram_comments_error_on_retry_exhaustion(monkeypatch):
    def always_raise(*a, **k):
        raise fmm.RetryExhaustedError("boom")

    monkeypatch.setattr(fmm, "get_with_retry", always_raise)

    result = fmm.fetch_instagram_comments("token", "ig-123")

    assert result["status"] == "error"
    assert result["items"] == []
    assert "boom" in result["error"]


# --- fetch_instagram_mentions ---


def test_fetch_instagram_mentions_not_configured_without_ig_id(monkeypatch):
    monkeypatch.setattr(
        fmm, "get_with_retry", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP call expected"))
    )
    result = fmm.fetch_instagram_mentions("token", None)
    assert result == {"capability": "instagram_mentions", "status": "not_configured", "items": [], "error": None}


def test_fetch_instagram_mentions_ok_path_uses_tags_edge_not_comments(monkeypatch):
    tags_resp = FakeResponse(json_data={"data": [
        {
            "id": "media-9",
            "caption": "Loved my visit to @remedy!",
            "permalink": "https://instagram.com/p/xyz",
            "timestamp": "2026-06-03T00:00:00+0000",
            "username": "skinseeker_mnl",
        },
    ]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([("/tags", tags_resp)]))

    result = fmm.fetch_instagram_mentions("token", "ig-123")

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["venue"] == "tagged_mention"
    assert item["text"] == "Loved my visit to @remedy!"
    assert item["author"] == fmm.mask_instagram_handle("skinseeker_mnl")
    # No comment_id for this capability - a tag is a property of the
    # whole media object, not a distinct comment (see _external_id in
    # app/jobs/meta_job.py, which depends on this distinction).
    assert item["raw"] == {"media_id": "media-9"}


def test_fetch_instagram_mentions_access_denied_independent_of_comments(monkeypatch):
    monkeypatch.setattr(fmm, "get_with_retry", _router([("/tags", _permission_denied_response())]))

    result = fmm.fetch_instagram_mentions("token", "ig-123")

    assert result["status"] == "access_denied"
    assert result["items"] == []


# --- fetch_facebook_comments ---


def test_fetch_facebook_comments_not_configured_without_page_id(monkeypatch):
    monkeypatch.setattr(
        fmm, "get_with_retry", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP call expected"))
    )
    result = fmm.fetch_facebook_comments("token", None)
    assert result == {"capability": "facebook_comments", "status": "not_configured", "items": [], "error": None}


def test_fetch_facebook_comments_ok_path_uses_mask_reviewer_name(monkeypatch):
    posts_resp = FakeResponse(json_data={"data": [
        {"id": "post-1", "message": "New Rejuran offer!", "permalink_url": "https://fb.com/post1",
         "created_time": "2026-06-01T00:00:00+0000"},
    ]})
    comments_resp = FakeResponse(json_data={"data": [
        {"id": "fc1", "message": "How much?", "from": {"name": "Maria Santos", "id": "fbid1"},
         "created_time": "2026-06-02T00:00:00+0000", "permalink_url": "https://fb.com/post1?comment_id=fc1"},
    ]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/posts", posts_resp),
        ("/comments", comments_resp),
    ]))

    result = fmm.fetch_facebook_comments("token", "page-123")

    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["platform"] == "facebook"
    # Reuses mask_reviewer_name()'s exact first-name-plus-initial output -
    # not a hashed token like the Instagram case.
    assert item["author"] == owned_reviews.mask_reviewer_name("Maria Santos")
    assert item["author"] == "Maria S."
    assert item["text"] == "How much?"
    assert item["sentiment"] is None
    assert item["raw"] == {"comment_id": "fc1", "post_id": "post-1"}


def test_fetch_facebook_comments_missing_from_field_handled_without_crashing(monkeypatch):
    posts_resp = FakeResponse(json_data={"data": [{"id": "post-1"}]})
    comments_resp = FakeResponse(json_data={"data": [{"id": "fc1", "message": "anon comment"}]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/posts", posts_resp),
        ("/comments", comments_resp),
    ]))

    result = fmm.fetch_facebook_comments("token", "page-123")

    assert result["status"] == "ok"
    assert result["items"][0]["author"] == owned_reviews.mask_reviewer_name(None)


def test_fetch_facebook_comments_error_on_retry_exhaustion(monkeypatch):
    def always_raise(*a, **k):
        raise fmm.RetryExhaustedError("network blip")

    monkeypatch.setattr(fmm, "get_with_retry", always_raise)

    result = fmm.fetch_facebook_comments("token", "page-123")

    assert result["status"] == "error"
    assert "network blip" in result["error"]


# --- the three capabilities fail independently (no cross-contamination) ---


def test_all_three_capabilities_can_have_different_statuses_at_once(monkeypatch):
    routes = [
        ("/media", FakeResponse(json_data={"data": []})),
        ("/tags", _permission_denied_response()),
    ]
    monkeypatch.setattr(fmm, "get_with_retry", _router(routes))

    ig_comments = fmm.fetch_instagram_comments("token", "ig-123")
    ig_mentions = fmm.fetch_instagram_mentions("token", "ig-123")
    fb_comments = fmm.fetch_facebook_comments("token", None)

    assert ig_comments["status"] == "ok"
    assert ig_mentions["status"] == "access_denied"
    assert fb_comments["status"] == "not_configured"


# --- pagination ---


def test_paginate_follows_paging_next_until_no_next_page(monkeypatch):
    page1 = FakeResponse(json_data={"data": [{"id": "a"}], "paging": {"next": "https://graph.facebook.com/next"}})
    page2 = FakeResponse(json_data={"data": [{"id": "b"}]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/next", page2),
        ("/media", page1),
    ]))

    items = fmm._paginate(f"{fmm.GRAPH_BASE}/ig-123/media", {"access_token": "t"}, max_items=10)

    assert [i["id"] for i in items] == ["a", "b"]


def test_paginate_stops_at_max_items(monkeypatch):
    page1 = FakeResponse(json_data={
        "data": [{"id": "a"}, {"id": "b"}],
        "paging": {"next": "https://graph.facebook.com/next"},
    })
    page2 = FakeResponse(json_data={"data": [{"id": "c"}]})
    monkeypatch.setattr(fmm, "get_with_retry", _router([
        ("/next", page2),
        ("/media", page1),
    ]))

    items = fmm._paginate(f"{fmm.GRAPH_BASE}/ig-123/media", {"access_token": "t"}, max_items=1)

    assert len(items) == 1


# --- main() ---


def test_main_exits_when_access_token_entirely_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        fmm.main()
        raised = False
    except SystemExit:
        raised = True
    assert raised
    assert not (tmp_path / "meta_mentions.json").exists()
