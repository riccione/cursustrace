"""Tests for the SQLite backup helpers."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cursustrace import backup, db


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data" / "cursustrace.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
    return path


def _fake_backups(dest: Path, *stamps: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    return [dest / f"{backup.BACKUP_PREFIX}{stamp}{backup.BACKUP_SUFFIX}" for stamp in stamps]


def test_backup_copies_stored_positions(db_path: Path, tmp_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "SRE", "Acme", "Remote", "Body")
    dest = tmp_path / "backups"

    result = backup.backup_database(dest)

    assert result is not None
    assert result.pruned == ()
    assert result.path.parent == dest
    assert result.path.name.startswith(backup.BACKUP_PREFIX)
    with closing(sqlite3.connect(result.path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 2


def test_backup_without_database_returns_none(db_path: Path, tmp_path: Path) -> None:
    assert backup.backup_database(tmp_path / "backups") is None


def test_backup_creates_destination_directories(db_path: Path, tmp_path: Path) -> None:
    db.init_db()
    dest = tmp_path / "nested" / "backups"

    result = backup.backup_database(dest)

    assert result is not None
    assert dest.is_dir()
    assert result.path.is_file()


def test_backup_prunes_oldest_but_keeps_unrelated_files(db_path: Path, tmp_path: Path) -> None:
    db.init_db()
    dest = tmp_path / "backups"
    old = _fake_backups(dest, "20200101-000000", "20200102-000000", "20200103-000000")
    for path in old:
        path.write_text("old", encoding="utf-8")
    unrelated = dest / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    other_db = dest / "other.db"
    other_db.write_text("keep me too", encoding="utf-8")

    result = backup.backup_database(dest, keep=2)

    assert result is not None
    assert set(result.pruned) == {old[0].name, old[1].name}
    assert not old[0].exists()
    assert not old[1].exists()
    assert old[2].exists()
    assert result.path.exists()
    assert unrelated.exists()
    assert other_db.exists()


def test_backup_keep_zero_keeps_everything(db_path: Path, tmp_path: Path) -> None:
    db.init_db()
    dest = tmp_path / "backups"
    old = _fake_backups(dest, "20200101-000000", "20200102-000000")
    for path in old:
        path.write_text("old", encoding="utf-8")

    result = backup.backup_database(dest, keep=0)

    assert result is not None
    assert result.pruned == ()
    assert all(path.exists() for path in old)


def test_backup_avoids_name_collisions(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    dest = tmp_path / "backups"

    first = backup.backup_database(dest)
    second = backup.backup_database(dest)

    assert first is not None and second is not None
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert first.path.name == "cursustrace-20260101-120000.db"
    assert second.path.name == "cursustrace-20260101-120000-1.db"
