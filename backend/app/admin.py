"""app/admin.py — the operator CLI. Creating a login, listing accounts,
resetting a password, and printing what the last ingestion pass actually
did.

Why this exists
----------------
app/auth.py has had create_user() since Phase 5 and app/api/routes/auth.py
has had POST /api/auth/login since Phase 7. Between them there was no way
to call the first one: no CLI, no seed, no signup endpoint, no fixture
outside the test suite. A reviewer handed the finished project could
start the database, run the migrations, start the API, open the UI — and
then had no account to log in with and no documented way to make one.
Every feature behind the login was unreachable, which is how a system
that is "done" reviews as a system that does nothing.

That is the whole reason this module is a CLI and not, say, a signup
endpoint. This is an internal tool for one marketing team; accounts are
provisioned by whoever runs the deployment, and a public self-registration
route on a brand-monitoring dashboard would be a security hole, not a
feature. A command an operator runs once at setup is the right shape.

`check` is here for a related reason. The same review found the project
reporting sources as working when they had ingested nothing — a run
recorded SUCCESS with items_seen=0 and nothing downstream said otherwise.
`check` is the one command that answers "is this actually working" with
counts from the database rather than from anyone's summary, which is the
only form of that answer worth having.

Usage:
    python -m app.admin create-user --email you@example.com --name "Your Name"
    python -m app.admin list-users
    python -m app.admin reset-password --email you@example.com
    python -m app.admin check
    python -m app.admin reddit-compliance
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.auth import DuplicateEmailError, create_user, hash_password
from app.db import session_scope
from app.models import IngestionRun, Mention, User

# Minimum password length accepted by create-user / reset-password.
# Deliberately a length floor and nothing else — no character-class rules,
# which are well-established to push people toward "Password1!" and away
# from length, the property that actually matters against an offline
# attack on a bcrypt hash.
MIN_PASSWORD_LENGTH = 12


def _prompt_password(confirm: bool = True) -> str:
    """Read a password from the terminal without echoing it.

    getpass, not an --password flag, and not input(). A password typed as
    a command-line argument is visible to every other process on the
    machine via the process list and is written verbatim into the shell
    history file — which is the same class of mistake as the .env that
    shipped inside a distribution zip, just through a different channel.
    A --password flag is deliberately not offered even as a convenience,
    because the convenient path is the one people actually use.

    For non-interactive use (CI, a provisioning script), pipe the password
    on stdin: `echo "$PW" | python -m app.admin create-user --email ...`.
    getpass falls back to reading stdin when there is no tty."""
    if not sys.stdin.isatty():
        piped = sys.stdin.readline().rstrip("\n")
        if not piped:
            raise SystemExit("No password on stdin, and no terminal to prompt on.")
        return piped

    password = getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if confirm and getpass.getpass("Confirm password: ") != password:
        raise SystemExit("Passwords did not match. Nothing was changed.")
    return password


def cmd_create_user(args: argparse.Namespace) -> int:
    """Create a login. This is the command that makes the product
    reachable at all — see the module docstring."""
    password = _prompt_password()
    with session_scope() as session:
        try:
            user = create_user(
                session, email=args.email, password=password, display_name=args.name
            )
        except DuplicateEmailError as exc:
            # Not a traceback: this is a normal thing for an operator to
            # do twice, and the useful response names the fix.
            print(f"{exc}\nUse `reset-password` to change that account's password.")
            return 1
        print(f"Created user #{user.id}: {user.email} ({user.display_name})")
    print("\nLog in at index.html, or:")
    print(
        '  curl -X POST http://localhost:8000/api/auth/login '
        f'-H "Content-Type: application/json" -d \'{{"email":"{args.email}","password":"..."}}\''
    )
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    """Set a new password on an existing account.

    Every outstanding session token for that user stays valid until it
    expires — tokens are stateless signed blobs (see app/auth.py's "Why a
    signed stdlib token" note), so there is nothing server-side to
    invalidate. That is a real limitation worth stating at the moment
    someone resets a password precisely BECAUSE they might be resetting
    it in response to a compromise, where "the old password no longer
    works" is not the same as "whoever had it is logged out." Rotating
    SESSION_SECRET_KEY is what actually signs everyone out."""
    with session_scope() as session:
        user = session.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if user is None:
            print(f"No user with email {args.email!r}. `list-users` shows what exists.")
            return 1
        user.password_hash = hash_password(_prompt_password())
        print(f"Password updated for {user.email}.")
    print(
        "Note: existing session tokens for this account remain valid until they\n"
        "expire (12h). To invalidate every outstanding token immediately, rotate\n"
        "SESSION_SECRET_KEY and restart the API."
    )
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    with session_scope() as session:
        users = session.execute(select(User).order_by(User.id)).scalars().all()
        if not users:
            print("No users exist. Nobody can log in.")
            print("Create one:  python -m app.admin create-user --email you@example.com --name \"Your Name\"")
            return 1
        print(f"{'id':>4}  {'email':<40} {'name':<24} active  last login")
        for user in users:
            last = user.last_login_at.strftime("%Y-%m-%d %H:%M") if user.last_login_at else "never"
            print(
                f"{user.id:>4}  {user.email:<40} {(user.display_name or ''):<24} "
                f"{'yes' if user.is_active else 'no':<6}  {last}"
            )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Print what is actually in the database: rows per source, how much of
    it the classifier has processed, and the most recent ledger row per
    source with its real status and error.

    Reads the database directly rather than the API, deliberately — the
    question this answers is "did the pipeline do anything", and routing
    it through the layer whose own status output was previously wrong
    would defeat the purpose."""
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    with session_scope() as session:
        total = session.execute(select(func.count(Mention.id))).scalar_one()
        classified = session.execute(
            select(func.count(Mention.id)).where(Mention.classified_at.isnot(None))
        ).scalar_one()

        print(f"Mentions: {total} total, {classified} classified, {total - classified} pending\n")

        print("By source:")
        rows = session.execute(
            select(Mention.source, func.count(Mention.id), func.count(Mention.classified_at))
            .group_by(Mention.source)
            .order_by(func.count(Mention.id).desc())
        ).all()
        if not rows:
            print("  (nothing ingested — no source has produced a single row)")
        for source, count, done in rows:
            print(f"  {source:<28} {count:>5} rows  {done:>5} classified")

        print("\nSentiment (classified rows only):")
        sentiment_rows = session.execute(
            select(Mention.sentiment, func.count(Mention.id))
            .where(Mention.classified_at.isnot(None))
            .group_by(Mention.sentiment)
        ).all()
        if not sentiment_rows:
            print("  (none — the classifier has not successfully processed anything)")
        for sentiment, count in sentiment_rows:
            print(f"  {str(sentiment):<28} {count:>5}")

        print(f"\nLast run per source (within {args.hours}h):")
        # The ledger's own last-row-per-source. A source that has never
        # run at all simply does not appear, which is itself the answer.
        latest = session.execute(
            select(IngestionRun)
            .where(IngestionRun.started_at >= since)
            .order_by(IngestionRun.source, IngestionRun.started_at.desc())
        ).scalars().all()
        seen: set[str] = set()
        if not latest:
            print("  (no runs recorded — has the scheduler been run?)")
        for run in latest:
            if run.source in seen:
                continue
            seen.add(run.source)
            status = run.status.value if hasattr(run.status, "value") else str(run.status)
            detail = f"  {run.error}" if run.error else ""
            print(
                f"  {run.source:<28} {status:<14} "
                f"seen={run.items_seen:<4} ingested={run.items_ingested:<4}{detail}"
            )
    return 0


def cmd_reddit_compliance(args: argparse.Namespace) -> int:
    """Answer the one question the signed Reddit application commits us
    to: is every stored Reddit row being re-checked for upstream deletion
    inside 48 hours?

    Deliberately NOT the same question as "did the deletion job succeed."
    A pass can report SUCCESS having verified three rows while forty sit
    untouched for a week — the ledger would look healthy and the
    commitment would be broken. This reads the rows themselves, which is
    the only way to get an answer that means anything.

    Exits non-zero when any row is overdue, so this can be wired into a
    monitor later without being rewritten."""
    from app.jobs.reddit_public_deletion_job import (
        COMMITMENT_WINDOW_HOURS,
        TARGET_SOURCE,
        rows_overdue_for_verification,
    )

    now = datetime.now(timezone.utc)

    with session_scope() as session:
        held = session.execute(
            select(func.count(Mention.id)).where(
                Mention.source == TARGET_SOURCE, Mention.deleted_at.is_(None)
            )
        ).scalar_one()
        scrubbed = session.execute(
            select(func.count(Mention.id)).where(
                Mention.source == TARGET_SOURCE, Mention.deleted_at.isnot(None)
            )
        ).scalar_one()

        print(
            f"Commitment: remove deleted Reddit content within "
            f"{COMMITMENT_WINDOW_HOURS}h."
        )
        print("Source: docs/Remedy Pulse_Reddit Data Access_Use Case.pdf\n")
        print(f"Rows held:                             {held}")
        print(f"Rows scrubbed after upstream deletion: {scrubbed}")

        if held == 0:
            print("\nNothing held, so nothing to verify.")
            return 0

        overdue = rows_overdue_for_verification(session, now=now)
        if not overdue:
            print(
                f"\nOK — every held row was verified within the last "
                f"{COMMITMENT_WINDOW_HOURS}h."
            )
            return 0

        print(
            f"\nOVERDUE — {len(overdue)} row(s) not verified in "
            f"{COMMITMENT_WINDOW_HOURS}h:"
        )
        for row in overdue[:10]:
            updated = row.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (now - updated).total_seconds() / 3600 if updated else float("inf")
            print(f"  #{row.id:<6} {row.external_id:<16} last verified {age:.0f}h ago")
        if len(overdue) > 10:
            print(f"  ... and {len(overdue) - 10} more")
        print(
            "\nRun `python -m app.scheduler` to work the queue. If this stays\n"
            "overdue, the deletion job is being rate-limited faster than it can\n"
            "keep up — its ledger row carries the real reason."
        )
    return 1


def cmd_generate_secret(args: argparse.Namespace) -> int:
    """Print a value suitable for SESSION_SECRET_KEY.

    Here because the alternative is an operator inventing one by hand,
    and because .env.example cannot contain a real one — a placeholder
    that looks like a key is exactly how a weak key ends up in
    production."""
    print(secrets.token_urlsafe(48))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Create a login (prompts for the password)")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="Display name shown in the UI")
    create.set_defaults(func=cmd_create_user)

    reset = sub.add_parser("reset-password", help="Set a new password on an existing account")
    reset.add_argument("--email", required=True)
    reset.set_defaults(func=cmd_reset_password)

    listing = sub.add_parser("list-users", help="Show every account")
    listing.set_defaults(func=cmd_list_users)

    check = sub.add_parser("check", help="What the pipeline has actually ingested and classified")
    check.add_argument("--hours", type=float, default=24.0, help="Ledger window (default: 24)")
    check.set_defaults(func=cmd_check)

    compliance = sub.add_parser(
        "reddit-compliance",
        help="Is the 48-hour Reddit deletion commitment actually being kept?",
    )
    compliance.set_defaults(func=cmd_reddit_compliance)

    secret = sub.add_parser("generate-secret", help="Print a value for SESSION_SECRET_KEY")
    secret.set_defaults(func=cmd_generate_secret)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
