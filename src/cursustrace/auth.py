"""Password hashing, validation, and the default admin account."""

from __future__ import annotations

import re

import bcrypt

from cursustrace import db

EMAIL_RE = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")

MIN_PASSWORD_LENGTH = 6

# Verified (and always rejected) against when an email has no account, so
# unknown emails take the same time as known ones.
_DUMMY_HASH = bcrypt.hashpw(b"invalid-placeholder", bcrypt.gensalt())

DEFAULT_ADMIN_EMAIL = "admin@admin.dev"
DEFAULT_ADMIN_PASSWORD = "admin"


def is_valid_email(email: str) -> bool:
    """Return True when the email is non-empty and structurally valid."""
    return bool(EMAIL_RE.match((email or "").strip()))


def password_problems(password: str) -> list[str]:
    """Return human-readable policy violations for ``password`` (empty when valid)."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"At least {MIN_PASSWORD_LENGTH} characters")
    if not any(char.isupper() for char in password):
        problems.append("At least one uppercase letter")
    if not any(char.isdigit() for char in password):
        problems.append("At least one digit")
    return problems


def hash_password(password: str) -> str:
    """Return the bcrypt hash of ``password`` as an ASCII string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True when ``password`` matches ``password_hash``."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


def authenticate(email: str, password: str) -> db.UserRow | None:
    """Return the account row when the email and password match, else None."""
    user = db.get_user_by_email(email)
    stored = user["password_hash"] if user is not None else _DUMMY_HASH.decode("ascii")
    if not verify_password(password, stored):
        return None
    return user


def seed_default_admin() -> int:
    """Create the default admin account on first multi-user startup; return its id.

    The seeded credentials bypass the password policy (the UI forces a change
    after first login); existing rows are never overwritten.
    """
    existing = db.get_user_by_email(DEFAULT_ADMIN_EMAIL)
    if existing is not None:
        return existing["id"]
    user_id = db.create_user(
        DEFAULT_ADMIN_EMAIL, hash_password(DEFAULT_ADMIN_PASSWORD), is_admin=True
    )
    if user_id is None:
        # Another process won the race; adopt its account.
        raced = db.get_user_by_email(DEFAULT_ADMIN_EMAIL)
        if raced is None:
            raise RuntimeError("failed to seed the default admin account")
        user_id = raced["id"]
    db.adopt_orphan_rows(user_id)
    return user_id
