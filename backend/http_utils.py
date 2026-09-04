"""http_utils.py — shared retry/backoff helper for the fetch_* connectors.

No new third-party dependency: this hand-rolls exponential backoff with
jitter around `requests.get`, retrying on 429 and transient 5xx responses
(500, 502, 503, 504). On a 429 it respects a `Retry-After` header when the
server sends one, instead of guessing.

Non-retryable statuses (e.g. 401, 403, 404) are returned immediately as a
normal `requests.Response` — callers still call `.raise_for_status()` (or
inspect `.status_code` themselves, as `fetch_owned_reviews.get_reviews`
does for 403) exactly as before. Only on final exhaustion of the retryable
statuses does this raise — and it raises clearly, `RetryExhaustedError`,
rather than swallowing the failure, so a caller can record the listing or
competitor as failed instead of pretending the run succeeded.
"""

import random
import time

import requests

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30


class RetryExhaustedError(Exception):
    """Raised when a GET request never succeeds after all retry attempts."""


def _sleep_seconds(attempt, resp, backoff_base):
    """Exponential backoff with jitter, honoring Retry-After on a 429."""
    if resp is not None and resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass  # fall through to exponential backoff below
    base_sleep = backoff_base * (2 ** (attempt - 1))
    jitter = random.uniform(0, base_sleep * 0.25)
    return base_sleep + jitter


def get_with_retry(
    url,
    *,
    headers=None,
    params=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    max_retries=DEFAULT_MAX_RETRIES,
    backoff_base=DEFAULT_BACKOFF_BASE_SECONDS,
):
    """GET `url` with exponential backoff + jitter on 429/5xx.

    Returns the `requests.Response` as soon as it comes back with a status
    that isn't in RETRYABLE_STATUS_CODES (this includes normal 2xx success
    as well as non-retryable errors like 403/404, which the caller is
    expected to handle itself). Raises `RetryExhaustedError` if every
    attempt is exhausted on a retryable status or a network-level
    exception (timeout, connection error, etc).
    """
    last_response = None
    last_exception = None

    for attempt in range(1, max_retries + 2):  # +1 initial try, +1 for range()
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            last_response = None
        else:
            if resp.status_code not in RETRYABLE_STATUS_CODES:
                return resp
            last_response = resp
            last_exception = None

        if attempt > max_retries:
            break
        time.sleep(_sleep_seconds(attempt, last_response, backoff_base))

    if last_response is not None:
        raise RetryExhaustedError(
            f"GET {url} failed after {max_retries} retries: "
            f"last status {last_response.status_code}"
        )
    raise RetryExhaustedError(
        f"GET {url} failed after {max_retries} retries: {last_exception}"
    ) from last_exception
