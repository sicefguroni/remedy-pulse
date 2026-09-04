"""config.py — typed, validated service configuration (2.3).

The existing fetch_*.py scripts each do their own module-level
`load_dotenv()` + `os.getenv(...)` + `raise SystemExit(...)` on a missing
value. That is fine for a script someone runs by hand and reads the
stderr of. It stops being fine the moment configuration is read by a
scheduled job (Phase 4.6) or an API process (Phase 7) with no human
watching stdout — a typo in an env var name should fail at startup with a
clear message naming every problem at once, not partway through a run,
and not as a bare KeyError three call frames deep.

`Settings` is that startup-time validation. It does NOT replace the
existing scripts' own env handling (deliberately — see backend/app/__init__.py
for why); it's what Phase 4 adopts when those scripts become long-running
jobs instead of one-shot CLI runs.

Usage:
    from app.config import get_settings
    settings = get_settings()  # raises pydantic.ValidationError, once,
                                # naming every missing/invalid field, if
                                # required configuration is absent.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required: where the mentions/reviews/articles/ingestion-runs tables
    # live. No default — an empty or missing DATABASE_URL should fail
    # startup loudly, not silently fall back to some assumed local default
    # that happens to work on one engineer's machine and nowhere else.
    database_url: str = Field(alias="DATABASE_URL")

    # Optional: the existing per-connector credentials (Phase 0/1). Not
    # required here — most of Settings' consumers (the ledger, the
    # repository layer) never touch a third-party API at all — but typed
    # and validated in one place for whichever Phase 4 job does need them,
    # instead of each job re-deriving its own os.getenv() story.
    google_places_api_key: str | None = Field(default=None, alias="GOOGLE_PLACES_API_KEY")
    google_client_secrets_file: str | None = Field(default=None, alias="GOOGLE_CLIENT_SECRETS_FILE")
    google_token_file: str = Field(default="./token.json", alias="GOOGLE_TOKEN_FILE")
    gnews_api_key: str | None = Field(default=None, alias="GNEWS_API_KEY")

    @field_validator("database_url")
    @classmethod
    def _database_url_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "DATABASE_URL is set but blank. Expected a SQLAlchemy URL, "
                "e.g. postgresql+psycopg://user:pass@localhost:5432/remedy_pulse"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached Settings singleton — validated once per process, not once
    per call site. Tests that need a different configuration should
    construct Settings(...) directly rather than going through this cache."""
    return Settings()
