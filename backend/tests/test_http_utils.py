import pytest

import http_utils as hu


class FakeResponse:
    def __init__(self, status_code, headers=None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    # Every test in this file exercises backoff/retry logic; none of them
    # should actually wait in real time.
    monkeypatch.setattr(hu.time, "sleep", lambda seconds: None)


def test_get_with_retry_returns_response_on_first_success(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(hu.requests, "get", fake_get)
    resp = hu.get_with_retry("http://example.com/ok")
    assert resp.status_code == 200
    assert len(calls) == 1


def test_get_with_retry_passes_through_non_retryable_status_immediately(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return FakeResponse(403)

    monkeypatch.setattr(hu.requests, "get", fake_get)
    resp = hu.get_with_retry("http://example.com/denied")
    assert resp.status_code == 403
    assert len(calls) == 1


def test_get_with_retry_retries_on_429_then_succeeds(monkeypatch):
    responses = [FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200)]

    def fake_get(url, headers=None, params=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(hu.requests, "get", fake_get)
    resp = hu.get_with_retry("http://example.com/rate-limited", max_retries=3)
    assert resp.status_code == 200
    assert responses == []


def test_get_with_retry_retries_on_transient_5xx_then_succeeds(monkeypatch):
    responses = [FakeResponse(503), FakeResponse(502), FakeResponse(200)]

    def fake_get(url, headers=None, params=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(hu.requests, "get", fake_get)
    resp = hu.get_with_retry("http://example.com/flaky", max_retries=3)
    assert resp.status_code == 200


def test_get_with_retry_raises_after_exhausting_all_retries(monkeypatch):
    call_count = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        call_count["n"] += 1
        return FakeResponse(503)

    monkeypatch.setattr(hu.requests, "get", fake_get)
    with pytest.raises(hu.RetryExhaustedError):
        hu.get_with_retry("http://example.com/always-503", max_retries=2)
    # 1 initial attempt + 2 retries = 3 total calls.
    assert call_count["n"] == 3


def test_get_with_retry_raises_on_network_exception_after_exhaustion(monkeypatch):
    import requests as real_requests

    def fake_get(url, headers=None, params=None, timeout=None):
        raise real_requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(hu.requests, "get", fake_get)
    with pytest.raises(hu.RetryExhaustedError):
        hu.get_with_retry("http://example.com/unreachable", max_retries=1)
