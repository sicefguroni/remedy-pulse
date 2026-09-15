"""Tests for app/admin.py — the operator CLI.

The behaviour worth pinning here is narrow but load-bearing: before this
module existed, app.auth.create_user() had no caller outside the test
suite, so a reviewer handed the finished project had no account to log in
with and no documented way to make one. Every feature behind the login
was unreachable.

session_scope is monkeypatched to hand back the sqlite_session fixture so
these exercise the real command functions against a real session without
touching a live database.
"""

import contextlib

import pytest
from sqlalchemy import select

import app.admin as admin
from app.auth import authenticate, create_user
from app.models import User


@pytest.fixture
def cli(sqlite_session, monkeypatch):
    """Point the CLI's session_scope at the test session."""

    @contextlib.contextmanager
    def fake_scope():
        yield sqlite_session
        sqlite_session.flush()

    monkeypatch.setattr(admin, "session_scope", fake_scope)
    return sqlite_session


def _answer_password(monkeypatch, password="a-sufficiently-long-password"):
    monkeypatch.setattr(admin, "_prompt_password", lambda confirm=True: password)


def test_create_user_creates_an_account_that_can_actually_log_in(cli, monkeypatch, capsys):
    """The whole point of the module: the account it makes must satisfy
    the same authenticate() the login endpoint calls."""
    _answer_password(monkeypatch, "correct-horse-battery")

    exit_code = admin.main(["create-user", "--email", "ops@remedy.local", "--name", "Ops"])

    assert exit_code == 0
    assert authenticate(cli, email="ops@remedy.local", password="correct-horse-battery") is not None
    assert "Created user" in capsys.readouterr().out


def test_create_user_stores_a_hash_never_the_plaintext(cli, monkeypatch):
    _answer_password(monkeypatch, "correct-horse-battery")

    admin.main(["create-user", "--email", "ops@remedy.local", "--name", "Ops"])

    user = cli.execute(select(User).where(User.email == "ops@remedy.local")).scalars().one()
    assert user.password_hash != "correct-horse-battery"
    assert user.password_hash.startswith("$2")  # bcrypt


def test_create_user_on_a_duplicate_email_explains_the_fix(cli, monkeypatch, capsys):
    """A normal thing for an operator to do twice. The useful response
    names the command that does what they meant, not a traceback."""
    create_user(cli, email="ops@remedy.local", password="whatever-long-enough", display_name="Ops")
    _answer_password(monkeypatch)

    exit_code = admin.main(["create-user", "--email", "ops@remedy.local", "--name", "Ops"])

    assert exit_code == 1
    assert "reset-password" in capsys.readouterr().out


def test_reset_password_changes_the_password(cli, monkeypatch):
    create_user(cli, email="ops@remedy.local", password="old-password-here", display_name="Ops")
    _answer_password(monkeypatch, "new-password-here")

    assert admin.main(["reset-password", "--email", "ops@remedy.local"]) == 0

    assert authenticate(cli, email="ops@remedy.local", password="old-password-here") is None
    assert authenticate(cli, email="ops@remedy.local", password="new-password-here") is not None


def test_reset_password_on_an_unknown_email_fails_without_creating_one(cli, monkeypatch, capsys):
    _answer_password(monkeypatch)

    exit_code = admin.main(["reset-password", "--email", "nobody@remedy.local"])

    assert exit_code == 1
    assert cli.execute(select(User)).scalars().all() == []
    assert "list-users" in capsys.readouterr().out


def test_list_users_on_an_empty_database_says_nobody_can_log_in(cli, capsys):
    """The exact state a reviewer found the shipped project in. Saying so
    plainly, and printing the command that fixes it, is the difference
    between a dead end and a next step."""
    exit_code = admin.main(["list-users"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Nobody can log in" in out
    assert "create-user" in out


def test_list_users_shows_existing_accounts(cli, capsys):
    create_user(cli, email="ops@remedy.local", password="long-enough-password", display_name="Ops")

    assert admin.main(["list-users"]) == 0
    assert "ops@remedy.local" in capsys.readouterr().out


def test_check_reports_an_empty_pipeline_as_empty(cli, capsys):
    """`check` exists because a source once reported SUCCESS having
    ingested nothing. It must never phrase "no data" as anything but."""
    admin.main(["check"])

    out = capsys.readouterr().out
    assert "0 total" in out
    assert "nothing ingested" in out


def test_generate_secret_prints_a_long_random_value(capsys):
    admin.main(["generate-secret"])

    first = capsys.readouterr().out.strip()
    admin.main(["generate-secret"])
    second = capsys.readouterr().out.strip()

    assert len(first) >= 32
    assert first != second
