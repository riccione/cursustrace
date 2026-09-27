"""Consistent on-disk copies of the SQLite database, with retention pruning."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from cursustrace import db

BACKUP_PREFIX = "cursustrace-"
BACKUP_SUFFIX = ".db"


@dataclass(frozen=True)
class BackupResult:
    """A finished backup: where it landed and which older copies were pruned."""

    path: Path
    pruned: tuple[str, ...]


def backup_database(
    dest_dir: Path,
    *,
    keep: int | None = None,
    source: Path | None = None,
) -> BackupResult | None:
    """Copy the database into ``dest_dir`` using SQLite's online backup API.

    Returns ``None`` when there is no database yet. ``keep`` is the number of
    backups to retain (``None`` or ``<= 0`` keeps everything); only files
    matching :data:`BACKUP_PREFIX` are ever pruned.
    """
    source_path = source if source is not None else db.DEFAULT_DB_PATH
    if not source_path.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _available_path(dest_dir)

    try:
        with (
            closing(sqlite3.connect(source_path, timeout=5.0)) as reader,
            closing(sqlite3.connect(target)) as writer,
        ):
            reader.backup(writer)
            writer.commit()
    except Exception:
        target.unlink(missing_ok=True)
        raise

    pruned = _prune(dest_dir, keep)
    return BackupResult(path=target, pruned=pruned)


def _timestamp() -> str:
    """Local wall-clock stamp used in backup file names."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _available_path(dest_dir: Path) -> Path:
    stamp = _timestamp()
    candidate = dest_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    counter = 1
    while candidate.exists():
        candidate = dest_dir / f"{BACKUP_PREFIX}{stamp}-{counter}{BACKUP_SUFFIX}"
        counter += 1
    return candidate


def _prune(dest_dir: Path, keep: int | None) -> tuple[str, ...]:
    if keep is None or keep <= 0:
        return ()
    backups = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
    removed: list[str] = []
    for old in backups[: len(backups) - keep]:
        old.unlink(missing_ok=True)
        removed.append(old.name)
    return tuple(removed)
