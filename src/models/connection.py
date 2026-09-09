"""SQLite connection, transaction, path, and backup infrastructure."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


def get_app_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    override = os.environ.get("DIAOHUO_DATA_DIR")
    return Path(override).expanduser().resolve() if override else get_app_path() / "data"


def get_database_path() -> Path:
    return get_data_dir() / "diaohuo.db"


def get_backup_dir() -> Path:
    return get_data_dir() / "backup"


DB_PATH = str(get_database_path())
BACKUP_DIR = str(get_backup_dir())
MAX_BACKUPS = 30


def connect(db_path: str | os.PathLike[str] | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(db_path or get_database_path())
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    else:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if not read_only:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def transaction(
    db_path: str | os.PathLike[str] | None = None,
    *,
    immediate: bool = True,
) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    sha256: str


@dataclass(frozen=True)
class RestoreInfo:
    restored_from: Path
    restored_sha256: str
    safety_backup: BackupInfo | None


class DatabaseRestoreError(RuntimeError):
    def __init__(self, message: str, safety_backup: BackupInfo | None = None):
        super().__init__(message)
        self.safety_backup = safety_backup


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(
    db_path: str | os.PathLike[str] | None = None,
    *,
    prefix: str = "diaohuo_backup",
    retain: bool = True,
) -> BackupInfo | None:
    """Create a WAL-safe SQLite backup and verify the resulting database."""
    source_path = Path(db_path or get_database_path())
    if not source_path.exists():
        return None

    backup_dir = source_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = backup_dir / f"{prefix}_{timestamp}.db"

    source = connect(source_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
        check = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise sqlite3.DatabaseError(f"备份完整性检查失败: {check}")
    finally:
        destination.close()
        source.close()

    if retain and prefix == "diaohuo_backup":
        backups = sorted(backup_dir.glob("diaohuo_backup_*.db"), reverse=True)
        for old in backups[MAX_BACKUPS:]:
            old.unlink()

    return BackupInfo(target, file_sha256(target))


def database_version_and_integrity(path: Path) -> tuple[int, str]:
    conn = connect(path, read_only=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"products", "batches", "quotes"}.issubset(tables):
            raise DatabaseRestoreError("文件不是调货助手数据库备份")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return int(version), str(integrity)
    finally:
        conn.close()


def copy_database(source_path: Path, destination_path: Path) -> None:
    source = connect(source_path, read_only=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()

