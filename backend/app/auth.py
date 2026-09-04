"""auth.py — authentication primitives for the dashboard (5.5).

There is no HTTP API or web framework anywhere in this repo yet — Phase 7
("API and the data-driven UI refactor") builds that, and comes after
Phase 5 in the checklist's own sequencing. So this module is NOT routes
or middleware; it's the schema-adjacent logic Phase 7's API layer will
call into the moment it exists, matching this project's established
"schema/logic readiness ahead of the feature that consumes it" pattern
(see models.Mention.deleted_at, models.EventType.LOGIN — both built in
earlier phases with nothing calling them yet). `repository.log_login()`
(3.1) is exactly that: it has existed since Phase 3 with no caller. This
module is that caller, for the first time.

Three things live here:

1. Password hashing — hash_password() / verify_password(), backed by
   bcrypt (see the "Why bcrypt" note below). Never store or return a
   plaintext password, not even transiently longer than necessary.
2. create_user() / authenticate() — the two operations Phase 7's login
   endpoint will call. authenticate() never lets a caller distinguish
   "unknown email" from "wrong password" via its return value or a
   timing difference — see its docstring.
3. A minimal session-token concept — create_session_token() /
   verify_session_token() — so Phase 7 has something to build on without
   redoing this design. See "Why a signed stdlib token, not a sessions
   table" below.

Why bcrypt, not passlib:
    `passlib[bcrypt]` is a popular wrapper, but passlib's last release
    predates bcrypt's own 4.x release and its bcrypt backend has had
    version-detection breakage against modern `bcrypt` packages (it reads
    an internal `__about__.__version__` attribute recent bcrypt versions
    dropped). Depending on `bcrypt` directly avoids that footgun, needs
    only one new dependency instead of passlib-plus-bcrypt, and bcrypt is
    itself a purpose-built, actively maintained password-hashing library
    (not a general crypto library repurposed for this) — exactly what
    this project's "use a real password-hashing library" requirement
    asks for. Never plain SHA/MD5/etc.: those are fast-to-compute general
    hashes, which is precisely the property you don't want for password
    storage (it makes brute-forcing cheap).

Why a signed stdlib token, not a Session/AuthToken table:
    Two designs were considered:
    (a) A `Session`/`AuthToken` table — a row per login, revocable by
        deleting the row, queryable ("who's logged in right now").
    (b) A signed opaque token (HMAC over user_id + expiry, using only the
        stdlib's `hmac`/`hashlib`/`secrets`) — stateless, nothing to
        clean up, verifiable without a DB round trip.
    (b) is what's built here. This is a single-team internal tool with no
    stated requirement for server-side revocation, "who's logged in
    right now" visibility, or refresh-token rotation — building a table
    (and the expiry-sweeping job it would eventually need) is exactly the
    kind of over-building this project's repeated "don't build past
    what's actually asked" ethos warns against (see the 4.6 checklist
    item's explicit instruction as the clearest example of that ethos in
    this codebase). A stateless signed token is also strictly less new
    surface area: no new table, no new migration beyond `users` itself,
    and the stdlib already provides everything needed (`hmac.compare_digest`
    for constant-time comparison, `secrets` for the signing key fallback).
    The tradeoff this accepts: a token can't be individually revoked
    before it expires (e.g. on logout-everywhere or a compromised
    account) short of rotating the signing key, which invalidates every
    outstanding token at once. For this tool's stated scope that's an
    acceptable trade; Phase 7 should revisit it only if a real revocation
    requirement shows up (e.g. from 5.6/5.8's security posture, or a
    stated need for "sign this one user out remotely").

    The signing key: read from the SESSION_SECRET_KEY environment
    variable (not added to app.config.Settings — this module intentionally
    doesn't touch config.py; see the batch's file-ownership note). If
    unset, a random key is generated once at import time so tests and
    ad-hoc local use still work, WITH THE CAVEAT that every previously
    issued token becomes unverifiable the moment the process restarts.
    That caveat is a non-issue today (nothing issues tokens yet outside
    tests), but Phase 7 must set SESSION_SECRET_KEY in every real
    deployment for tokens to survive a process restart or be verifiable
    across multiple worker processes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.repository import log_login

# See "Why a signed stdlib token" above. Generated once per process if
# the environment doesn't supply one — real deployments should set this
# explicitly so tokens survive a restart / are shared across workers.
_SESSION_SECRET_KEY: bytes = (
    os.environ["SESSION_SECRET_KEY"].encode("utf-8")
    if os.environ.get("SESSION_SECRET_KEY")
    else secrets.token_bytes(32)
)

# Default token lifetime: 12 hours. Not sourced from Settings — see the
# module docstring on why this file doesn't touch config.py; Phase 7 can
# thread a real setting through when it wires this up to actual requests.
DEFAULT_SESSION_TOKEN_TTL_SECONDS = 12 * 60 * 60


class DuplicateEmailError(ValueError):
    """Raised by create_user() when the email is already registered.
    Subclasses ValueError so an existing `except ValueError` catches it
    too, while still letting a caller that cares distinguish this specific
    case from some other invalid-input ValueError."""


# --- password hashing ---


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt. Returns a str (bcrypt's own
    hash format, e.g. "$2b$12$...") suitable for storing directly in
    User.password_hash. bcrypt handles its own per-hash salt generation —
    no salt column is needed on User, the salt lives inside the returned
    hash string itself."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison of a plaintext password against a bcrypt
    hash (bcrypt.checkpw() is itself constant-time over the hash
    comparison). Returns False (never raises) for a malformed `hashed`
    value — a corrupted/foreign hash should read as "doesn't match," not
    crash the caller."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A fixed, precomputed dummy hash — used by authenticate() to run a real
# bcrypt comparison even when the email lookup fails, so a failed lookup
# and a failed password check cost comparably long. Precomputed once at
# import time (not per-call) since bcrypt.gensalt() is itself the
# expensive step; the constant string below is the hash of an arbitrary
# fixed password, never used to authenticate anyone.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"dummy-password-for-timing-parity", bcrypt.gensalt()).decode("utf-8")


# --- user creation & authentication ---


def create_user(session: Session, *, email: str, password: str, display_name: str) -> User:
    """Create a User row, hashing `password` before it ever touches the
    session or the database. Raises DuplicateEmailError (a ValueError
    subclass) with a clear message if `email` is already registered,
    rather than letting an opaque UNIQUE-constraint IntegrityError surface
    from the eventual flush/commit — same "check first, fail clearly"
    approach as repository.upsert_mention()'s explicit SELECT."""
    existing = session.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise DuplicateEmailError(f"A user with email {email!r} already exists")
    user = User(email=email, password_hash=hash_password(password), display_name=display_name)
    session.add(user)
    session.flush()  # populate user.id/created_at before the caller sees it
    return user


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    """Look up `email`, verify `password`, and return the User on success
    or None (never an exception) on any failure — unknown email, wrong
    password, or an inactive account are all just "authentication
    failed," indistinguishable from the return value. This is
    deliberate: letting a caller tell "user doesn't exist" apart from
    "wrong password" is exactly how a login form turns into a
    user-enumeration vulnerability.

    verify_password() runs an equivalent-cost bcrypt comparison EVEN WHEN
    the email isn't found (against a fixed dummy hash) so a failed
    lookup and a failed password check take comparably long — otherwise
    the lookup's own DB round trip would still make "no such email"
    measurably faster than "wrong password," reintroducing the same
    enumeration signal through a side channel instead of the return
    value.

    On success: updates last_login_at and calls repository.log_login()
    with the user's email as `actor` (not display_name — email is this
    table's unique identifier, so the audit trail unambiguously names
    the account even if two users happen to share a display name)."""
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    hash_to_check = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, hash_to_check)

    if user is None or not user.is_active or not password_ok:
        return None

    user.last_login_at = datetime.now(timezone.utc)
    log_login(session, actor=user.email)
    return user


# --- session tokens ---
#
# Format: base64url(f"{user_id}:{expires_at_epoch_seconds}") + "." +
# hex HMAC-SHA256 of that same payload, keyed on _SESSION_SECRET_KEY.
# Deliberately not JWT: a JWT library is a second new dependency and a
# much larger surface (header/alg negotiation, "alg: none" and similar
# footguns) than this needs — see the module docstring's "don't
# over-build" reasoning. This is the smallest thing that is still
# tamper-evident and self-expiring.


def create_session_token(user_id: int, *, ttl_seconds: int = DEFAULT_SESSION_TOKEN_TTL_SECONDS) -> str:
    """Build a signed, self-expiring token for `user_id`. Opaque to the
    caller — Phase 7 stores/returns this as-is (e.g. in a cookie or
    Authorization header) and passes it back to verify_session_token()."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{user_id}:{expires_at}".encode("utf-8")
    signature = hmac.new(_SESSION_SECRET_KEY, payload, hashlib.sha256).hexdigest()
    return f"{base64.urlsafe_b64encode(payload).decode('ascii')}.{signature}"


def verify_session_token(token: str) -> int | None:
    """Verify a token from create_session_token(). Returns the user_id on
    a valid, unexpired, correctly-signed token; None for anything else
    (malformed, tampered, or expired) — never raises, since a bad token
    from a client is an ordinary "not authenticated" case, not a bug."""
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        expected_signature = hmac.new(_SESSION_SECRET_KEY, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        user_id_str, expires_at_str = payload.decode("utf-8").split(":", 1)
        if int(time.time()) >= int(expires_at_str):
            return None  # expired
        return int(user_id_str)
    except (ValueError, TypeError):
        return None
