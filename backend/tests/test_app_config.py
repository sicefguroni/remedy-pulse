import pytest
from pydantic import ValidationError

from app.config import Settings

# The full-suite pytest run imports fetch_owned_reviews.py / fetch_competitor_
# ratings.py / fetch_news_articles.py during collection, each of which calls
# load_dotenv() at module level - that mutates the REAL process os.environ
# for the rest of the interpreter, not just those modules' own view of it.
# Settings(_env_file=None) correctly still reads os.environ (that's the
# intended behavior for a real deployment that exports vars rather than
# using a .env file) - so these tests must explicitly clear the specific
# keys they're asserting the absence of, rather than assume a clean
# environment that only holds when this file happens to run in isolation.
_ENV_KEYS = [
    "DATABASE_URL",
    "GOOGLE_PLACES_API_KEY",
    "GOOGLE_CLIENT_SECRETS_FILE",
    "GOOGLE_TOKEN_FILE",
    "GNEWS_API_KEY",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_settings_requires_database_url():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)


def test_settings_rejects_blank_database_url():
    with pytest.raises(ValidationError, match="blank"):
        Settings(_env_file=None, DATABASE_URL="   ")


def test_settings_accepts_valid_database_url():
    s = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@localhost:5432/db"
    # Optional fields default to None/a sane default rather than raising,
    # since most consumers of Settings (the ledger, the repository layer)
    # never touch a third-party API at all.
    assert s.google_places_api_key is None
    assert s.gnews_api_key is None
    assert s.google_token_file == "./token.json"


def test_settings_picks_up_optional_fields_when_present():
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
        GOOGLE_PLACES_API_KEY="places-key",
        GNEWS_API_KEY="gnews-key",
    )
    assert s.google_places_api_key == "places-key"
    assert s.gnews_api_key == "gnews-key"
