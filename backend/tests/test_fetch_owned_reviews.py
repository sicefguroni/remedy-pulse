import pytest

import fetch_owned_reviews as f


def test_star_rating_to_int_known_values():
    assert f.star_rating_to_int("ONE") == 1
    assert f.star_rating_to_int("TWO") == 2
    assert f.star_rating_to_int("THREE") == 3
    assert f.star_rating_to_int("FOUR") == 4
    assert f.star_rating_to_int("FIVE") == 5


def test_star_rating_to_int_unknown_returns_none():
    assert f.star_rating_to_int("UNKNOWN") is None
    assert f.star_rating_to_int(None) is None


def test_mask_reviewer_name_full_name():
    assert f.mask_reviewer_name("Maria Santos") == "Maria S."


def test_mask_reviewer_name_multi_part_name_uses_first_and_last():
    assert f.mask_reviewer_name("Maria Luisa Santos") == "Maria S."


def test_mask_reviewer_name_single_name_unchanged():
    assert f.mask_reviewer_name("Cardo") == "Cardo"


def test_mask_reviewer_name_empty_or_none_falls_back():
    assert f.mask_reviewer_name("") == "Google patron"
    assert f.mask_reviewer_name(None) == "Google patron"


def test_normalize_reviews_maps_fields_and_marks_positive_with_reply():
    raw = [{
        "reviewer": {"displayName": "Juan Dela Cruz"},
        "starRating": "FIVE",
        "comment": "Great service!",
        "createTime": "2026-08-01T10:00:00Z",
        "reviewReply": {"comment": "Thank you!"},
    }]
    normalized = f.normalize_reviews("Remedy — BGC", raw)
    assert len(normalized) == 1
    row = normalized[0]
    assert row["platform"] == "Google"
    assert row["listing"] == "Remedy — BGC"
    assert row["author"] == "Juan C."
    assert row["rating"] == 5
    assert row["text"] == "Great service!"
    assert row["date"] == "2026-08-01"
    assert row["hasReply"] is True
    assert row["sentiment"] == "Positive"
    assert row["sourceUrl"] is None


def test_normalize_reviews_negative_sentiment_and_no_reply():
    raw = [{
        "reviewer": {"displayName": "Ana Reyes"},
        "starRating": "ONE",
        "comment": "Not happy",
        "createTime": "2026-08-01T10:00:00Z",
    }]
    row = f.normalize_reviews("Remedy — BGC", raw)[0]
    assert row["rating"] == 1
    assert row["hasReply"] is False
    assert row["sentiment"] == "Negative"


def test_normalize_reviews_neutral_sentiment_for_mid_rating():
    raw = [{
        "reviewer": {"displayName": "Cardo"},
        "starRating": "THREE",
        "comment": "It was fine",
        "createTime": "2026-08-01T10:00:00Z",
    }]
    row = f.normalize_reviews("Remedy — BGC", raw)[0]
    assert row["sentiment"] == "Neutral"


def _fake_normalized(rating, has_reply):
    return {"rating": rating, "hasReply": has_reply}


def test_build_aggregate_ok_status_computes_rate_and_pending():
    normalized = [
        _fake_normalized(5, True),
        _fake_normalized(3, False),
    ]
    agg = f.build_aggregate("Remedy — BGC", normalized)
    assert agg["status"] == "ok"
    assert agg["listing"] == "Remedy — BGC"
    assert agg["reviewCount"] == 2
    assert agg["rating"] == 4.0
    # responseRate is a fraction, not a percentage.
    assert agg["responseRate"] == 0.5
    assert agg["pendingReplies"] == 1


def test_build_aggregate_no_reviews_status_for_genuinely_empty_branch():
    agg = f.build_aggregate("Remedy — Vertis North", [])
    assert agg["status"] == "no_reviews"
    assert agg["reviewCount"] == 0
    assert agg["rating"] is None
    assert agg["responseRate"] is None
    assert agg["pendingReplies"] == 0


def test_build_aggregate_access_denied_status_never_looks_like_ok_zero():
    denied = f.build_aggregate(
        "Skin Bar by Remedy — Greenhills Mall", [], status="access_denied"
    )
    assert denied["status"] == "access_denied"
    # Must be distinguishable from a genuinely empty branch: nulls, not 0.
    assert denied["reviewCount"] is None
    assert denied["rating"] is None
    assert denied["responseRate"] is None
    assert denied["pendingReplies"] is None

    genuinely_empty = f.build_aggregate("Skin Bar by Remedy — Greenhills Mall", [])
    assert denied != genuinely_empty
    assert not (denied["reviewCount"] == 0 and denied["status"] == "ok")


def test_build_aggregate_error_status_also_uses_nulls():
    agg = f.build_aggregate("Club Remedy — BGC", [], status="error")
    assert agg["status"] == "error"
    assert agg["reviewCount"] is None
    assert agg["rating"] is None


# --- load_credentials() / main() error handling ---
#
# load_credentials() must raise a plain RuntimeError, not SystemExit, when
# token.json is missing - it's shared with app/jobs/google_reviews_job.py
# (which needs an ordinary exception for app.repository.start_run()'s
# `except Exception` to catch), while main() (this script's own CLI
# entrypoint) converts that back to SystemExit so running the file
# directly still exits cleanly with a one-line message, no traceback -
# see both functions' own docstrings/comments for the full reasoning.


def test_load_credentials_missing_token_file_raises_runtime_error_not_system_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "TOKEN_FILE", str(tmp_path / "does-not-exist.json"))
    with pytest.raises(RuntimeError, match="No token file"):
        f.load_credentials()


def test_main_converts_missing_token_file_to_clean_system_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "TOKEN_FILE", str(tmp_path / "does-not-exist.json"))
    with pytest.raises(SystemExit, match="No token file"):
        f.main()


# --- GOOGLE_TOKEN_JSON (checklist 5.6 / docs/decisions/08-secrets-at-rest.md) ---


class _FakeCreds:
    """Stands in for google.oauth2.credentials.Credentials - controllable
    .expired/.refresh_token without needing a real, validly-signed OAuth
    token blob just to exercise load_credentials()'s branching."""

    def __init__(self, expired=False, refresh_token="a-refresh-token"):
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False

    def refresh(self, request):
        self.refreshed = True

    def to_json(self):
        return '{"token": "refreshed-token"}'


def test_load_credentials_prefers_env_var_over_file(monkeypatch, tmp_path):
    token_file = tmp_path / "should-not-be-touched.json"
    monkeypatch.setattr(f, "TOKEN_FILE", str(token_file))
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", '{"token": "abc", "refresh_token": "rt"}')

    fake = _FakeCreds(expired=False)
    captured = {}

    def fake_from_info(info, scopes):
        captured["info"] = info
        return fake

    monkeypatch.setattr(f.Credentials, "from_authorized_user_info", fake_from_info)

    creds = f.load_credentials()

    assert creds is fake
    assert captured["info"] == {"token": "abc", "refresh_token": "rt"}
    assert not token_file.exists()  # never read, never written


def test_load_credentials_falls_back_to_file_when_env_var_unset(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "from-file"}')
    monkeypatch.setattr(f, "TOKEN_FILE", str(token_file))
    monkeypatch.delenv("GOOGLE_TOKEN_JSON", raising=False)

    fake = _FakeCreds(expired=False)
    captured = {}

    def fake_from_file(path, scopes):
        captured["path"] = path
        return fake

    monkeypatch.setattr(f.Credentials, "from_authorized_user_file", fake_from_file)

    creds = f.load_credentials()

    assert creds is fake
    assert captured["path"] == str(token_file)


def test_load_credentials_refresh_from_env_var_prints_new_token_not_write_to_file(monkeypatch, tmp_path, capsys):
    token_file = tmp_path / "should-not-be-written.json"
    monkeypatch.setattr(f, "TOKEN_FILE", str(token_file))
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", '{"token": "expired", "refresh_token": "rt"}')

    fake = _FakeCreds(expired=True, refresh_token="rt")
    monkeypatch.setattr(f.Credentials, "from_authorized_user_info", lambda info, scopes: fake)

    creds = f.load_credentials()

    assert creds.refreshed is True
    assert not token_file.exists()  # can't rewrite an env var from the running process
    out = capsys.readouterr().out
    assert "GOOGLE_TOKEN_JSON" in out
    assert '"token": "refreshed-token"' in out


def test_load_credentials_refresh_from_file_writes_new_token_to_file(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "expired"}')
    monkeypatch.setattr(f, "TOKEN_FILE", str(token_file))
    monkeypatch.delenv("GOOGLE_TOKEN_JSON", raising=False)

    fake = _FakeCreds(expired=True, refresh_token="rt")
    monkeypatch.setattr(f.Credentials, "from_authorized_user_file", lambda path, scopes: fake)

    creds = f.load_credentials()

    assert creds.refreshed is True
    assert token_file.read_text() == '{"token": "refreshed-token"}'
