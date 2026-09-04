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
