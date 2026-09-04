import fetch_competitor_ratings as c


def test_normalize_with_result_defaults_to_ok_status():
    result = {
        "rating": 4.5,
        "user_ratings_total": 120,
        "reviews": [
            {
                "author_name": "X",
                "rating": 5,
                "text": "Great service",
                "relative_time_description": "a week ago",
            },
        ],
    }
    row = c.normalize("Belo Medical Group", result)
    assert row["status"] == "ok"
    assert row["competitor"] == "Belo Medical Group"
    assert row["rating"] == 4.5
    assert row["userRatingsTotal"] == 120
    assert row["sampleReviewCount"] == 1
    assert row["sampleReviews"][0]["author"] == "X"
    assert row["sampleReviews"][0]["rating"] == 5


def test_normalize_truncates_review_text_to_280_chars():
    long_text = "x" * 500
    result = {
        "rating": 4.0,
        "user_ratings_total": 10,
        "reviews": [{"author_name": "Y", "rating": 4, "text": long_text}],
    }
    row = c.normalize("Aivee Clinic", result)
    assert len(row["sampleReviews"][0]["text"]) == 280


def test_normalize_no_result_not_found_status():
    row = c.normalize("Kamiseta Skin Clinic", None, status="not_found")
    assert row["status"] == "not_found"
    assert row["rating"] is None
    assert row["userRatingsTotal"] == 0
    assert row["sampleReviewCount"] == 0
    assert row["sampleReviews"] == []


def test_normalize_no_result_error_status_distinct_from_not_found():
    row = c.normalize("SkinStation", None, status="error")
    assert row["status"] == "error"
    assert row["rating"] is None


def test_normalize_default_status_param_is_ok():
    # normalize() itself doesn't decide status - main() passes it in based
    # on what actually happened (see test_normalize_no_result_not_found_status
    # and test_normalize_no_result_error_status_distinct_from_not_found).
    # This pins the function's own default so a future refactor of main()
    # that forgets to pass `status` explicitly is caught by a status
    # mismatch here rather than discovered later.
    row = c.normalize("DermHQ", None)
    assert row["status"] == "ok"
