"""Tests for password hashing, validation, and admin seeding."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cursustrace import auth, db


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "cursustrace.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
    db.init_db()
    yield path


@pytest.mark.parametrize("email", ["a@b.c", "jane.doe@example.co.uk", "x_y@z.io"])
def test_is_valid_email_accepts(email: str) -> None:
    assert auth.is_valid_email(email) is True


@pytest.mark.parametrize("email", ["", "   ", "no-at", "no@tld", "@example.com", "a@b."])
def test_is_valid_email_rejects(email: str) -> None:
    assert auth.is_valid_email(email) is False


def test_password_problems_reports_each_violation() -> None:
    assert auth.password_problems("A1xxxx") == []
    assert auth.password_problems("short") == [
        "At least 6 characters",
        "At least one uppercase letter",
        "At least one digit",
    ]
    assert auth.password_problems("LONGPASS") == ["At least one digit"]
    assert auth.password_problems("longpass1") == ["At least one uppercase letter"]


def test_hash_and_verify_roundtrip() -> None:
    password = "S3cretPass"
    password_hash = auth.hash_password(password)

    assert password_hash != password
    assert auth.verify_password(password, password_hash) is True
    assert auth.verify_password("wrong", password_hash) is False


def test_verify_password_rejects_malformed_hash() -> None:
    assert auth.verify_password("anything", "not-a-bcrypt-hash") is False
    assert auth.verify_password("anything", "") is False


def test_authenticate_returns_user_on_match(db_path: Path) -> None:
    user_id = db.create_user("jane@example.com", auth.hash_password("S3cretPass"))
    assert user_id is not None

    user = auth.authenticate("jane@example.com", "S3cretPass")

    assert user is not None
    assert user["id"] == user_id


def test_authenticate_rejects_wrong_password(db_path: Path) -> None:
    user_id = db.create_user("jane@example.com", auth.hash_password("S3cretPass"))
    assert user_id is not None

    assert auth.authenticate("jane@example.com", "wrong") is None


def test_authenticate_rejects_unknown_email(db_path: Path) -> None:
    assert auth.authenticate("missing@example.com", "whatever1") is None


def test_authenticate_is_case_insensitive_on_email(db_path: Path) -> None:
    user_id = db.create_user("jane@example.com", auth.hash_password("S3cretPass"))
    assert user_id is not None

    assert auth.authenticate("JANE@example.com", "S3cretPass") is not None


def test_seed_default_admin_creates_account(db_path: Path) -> None:
    user_id = auth.seed_default_admin()

    user = db.get_user(user_id)
    assert user is not None
    assert user["email"] == auth.DEFAULT_ADMIN_EMAIL
    assert user["is_admin"] == 1
    assert auth.verify_password(auth.DEFAULT_ADMIN_PASSWORD, user["password_hash"]) is True


def test_seed_default_admin_adopts_orphan_rows(db_path: Path) -> None:
    db.add_job("https://example.com/1", "Engineer", "Acme", "Remote", "x")
    db.create_tag("Remote")
    db.create_profile("Default")

    user_id = auth.seed_default_admin()

    job = db.get_job(1)
    assert job is not None
    assert job["user_id"] == user_id
    assert db.list_tags(user_id=user_id) == ["Remote"]
    assert db.list_profiles(user_id=user_id)[0]["name"] == "Default"


def test_seed_default_admin_is_idempotent(db_path: Path) -> None:
    first = auth.seed_default_admin()
    second = auth.seed_default_admin()

    assert first == second


def test_seed_default_admin_does_not_adopt_after_removal(db_path: Path) -> None:
    user_id = auth.seed_default_admin()
    orphan = db.create_user("other@example.com", auth.hash_password("Other123"))
    assert orphan is not None

    assert auth.seed_default_admin() == user_id
