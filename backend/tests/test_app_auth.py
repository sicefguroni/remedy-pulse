"""Tests for the 5.5 authentication primitives (app/auth.py): password
hashing, create_user()/authenticate(), and the session-token concept.
Runs against in-memory SQLite (see conftest.sqlite_session) — a real
Postgres round trip for create_user()/authenticate() lives in
test_app_auth_postgres.py, same split as the Phase 3 event-log suite."""

import time

import pytest
from sqlalchemy import select

from app.auth import (
    DuplicateEmailError,
    authenticate,
    create_session_token,
    create_user,
    hash_password,
    verify_password,
    verify_session_token,
)
from app.models import Event, EventType, User

# --- hash_password / verify_password ---


def test_hash_password_never_returns_the_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert "correct horse battery staple" not in hashed


def test_hash_password_produces_a_bcrypt_hash():
    hashed = hash_password("hunter2")
    # bcrypt's own format prefix - proves a real password-hashing
    # algorithm is in play, not a bare fast hash.
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")


def test_verify_password_succeeds_for_the_correct_password():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_verify_password_fails_for_the_wrong_password():
    hashed = hash_password("hunter2")
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_fails_cleanly_for_a_malformed_hash():
    # A corrupted/foreign value should read as "doesn't match," not crash.
    assert verify_password("hunter2", "not-a-real-bcrypt-hash") is False


# --- create_user ---


def test_create_user_hashes_the_password_before_storing(sqlite_session):
    user = create_user(
        sqlite_session, email="gian@remedy.example", password="hunter2", display_name="Gian"
    )
    sqlite_session.commit()

    row = sqlite_session.execute(select(User)).scalar_one()
    assert row.id == user.id
    assert row.email == "gian@remedy.example"
    assert row.display_name == "Gian"
    assert row.password_hash != "hunter2"
    assert verify_password("hunter2", row.password_hash) is True


def test_create_user_defaults_is_active_true_and_last_login_at_none(sqlite_session):
    user = create_user(
        sqlite_session, email="paul@remedy.example", password="hunter2", display_name="Paul"
    )
    sqlite_session.commit()

    row = sqlite_session.get(User, user.id)
    assert row.is_active is True
    assert row.last_login_at is None
    assert row.created_at is not None


def test_create_user_raises_a_clear_error_on_duplicate_email(sqlite_session):
    create_user(sqlite_session, email="boom@remedy.example", password="pw1", display_name="Boom")
    sqlite_session.commit()

    with pytest.raises(DuplicateEmailError, match="boom@remedy.example"):
        create_user(sqlite_session, email="boom@remedy.example", password="pw2", display_name="Boom Again")


# --- authenticate ---


def test_authenticate_succeeds_with_correct_credentials(sqlite_session):
    create_user(sqlite_session, email="mixi@remedy.example", password="hunter2", display_name="Mixi")
    sqlite_session.commit()

    user = authenticate(sqlite_session, email="mixi@remedy.example", password="hunter2")
    sqlite_session.commit()

    assert user is not None
    assert user.email == "mixi@remedy.example"


def test_authenticate_fails_with_wrong_password_and_returns_none_not_an_exception(sqlite_session):
    create_user(sqlite_session, email="gian@remedy.example", password="hunter2", display_name="Gian")
    sqlite_session.commit()

    result = authenticate(sqlite_session, email="gian@remedy.example", password="wrong-password")

    assert result is None


def test_authenticate_fails_with_unknown_email_and_returns_none(sqlite_session):
    result = authenticate(sqlite_session, email="nobody@remedy.example", password="whatever")
    assert result is None


def test_authenticate_fails_for_an_inactive_account(sqlite_session):
    user = create_user(sqlite_session, email="paul@remedy.example", password="hunter2", display_name="Paul")
    user.is_active = False
    sqlite_session.commit()

    result = authenticate(sqlite_session, email="paul@remedy.example", password="hunter2")
    assert result is None


def test_authenticate_updates_last_login_at_on_success(sqlite_session):
    create_user(sqlite_session, email="boom@remedy.example", password="hunter2", display_name="Boom")
    sqlite_session.commit()

    user = authenticate(sqlite_session, email="boom@remedy.example", password="hunter2")
    sqlite_session.commit()

    row = sqlite_session.get(User, user.id)
    assert row.last_login_at is not None


def test_authenticate_does_not_update_last_login_at_on_failure(sqlite_session):
    create_user(sqlite_session, email="mixi@remedy.example", password="hunter2", display_name="Mixi")
    sqlite_session.commit()

    authenticate(sqlite_session, email="mixi@remedy.example", password="wrong-password")
    sqlite_session.commit()

    row = sqlite_session.execute(select(User)).scalar_one()
    assert row.last_login_at is None


def test_authenticate_logs_a_login_event_on_success(sqlite_session):
    """The whole point of wiring 3.1's log_login() in for the first time —
    assert an Event row actually lands, not just that the User row
    updated."""
    create_user(sqlite_session, email="gian@remedy.example", password="hunter2", display_name="Gian")
    sqlite_session.commit()

    authenticate(sqlite_session, email="gian@remedy.example", password="hunter2")
    sqlite_session.commit()

    events = sqlite_session.execute(select(Event).where(Event.event_type == EventType.LOGIN)).scalars().all()
    assert len(events) == 1
    assert events[0].actor == "gian@remedy.example"


def test_authenticate_does_not_log_a_login_event_on_failure(sqlite_session):
    create_user(sqlite_session, email="paul@remedy.example", password="hunter2", display_name="Paul")
    sqlite_session.commit()

    authenticate(sqlite_session, email="paul@remedy.example", password="wrong-password")
    authenticate(sqlite_session, email="nobody@remedy.example", password="whatever")
    sqlite_session.commit()

    events = sqlite_session.execute(select(Event).where(Event.event_type == EventType.LOGIN)).scalars().all()
    assert len(events) == 0


# --- session tokens ---


def test_session_token_roundtrips_correctly():
    token = create_session_token(42)
    assert verify_session_token(token) == 42


def test_session_token_is_not_a_bare_user_id_and_carries_a_separate_signature():
    token = create_session_token(42)
    assert token != "42"
    # Format is "<payload>.<hex hmac signature>" - two parts, and the
    # signature half is what makes tampering with the payload detectable
    # (see test_tampered_session_token_fails_verification below). It is
    # NOT meant to hide the user_id from whoever holds the token (that's
    # not this token's job - only tamper-evidence and expiry are), so
    # this doesn't assert anything about the signature's content.
    payload_part, signature_part = token.split(".", 1)
    assert payload_part and signature_part


def test_tampered_session_token_fails_verification():
    token = create_session_token(42)
    payload_part, signature_part = token.split(".", 1)
    # Flip a character in the signature - simulates tampering.
    tampered_signature = ("0" if signature_part[0] != "0" else "1") + signature_part[1:]
    tampered_token = f"{payload_part}.{tampered_signature}"

    assert verify_session_token(tampered_token) is None


def test_expired_session_token_fails_verification():
    token = create_session_token(42, ttl_seconds=0)
    time.sleep(0.01)  # ensure the clock has moved past the expiry second
    assert verify_session_token(token) is None


def test_verify_session_token_rejects_garbage_input():
    assert verify_session_token("not-a-real-token") is None
    assert verify_session_token("") is None
    assert verify_session_token("nodothere") is None
