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


def _sha256(path: Path) -> str:
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

    return BackupInfo(target, _sha256(target))


def _database_version_and_integrity(path: Path) -> tuple[int, str]:
    conn = connect(path, read_only=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return int(version), str(integrity)
    finally:
        conn.close()


def _backup_over_database(source_path: Path, destination_path: Path) -> None:
    source = connect(source_path, read_only=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def restore_database(
    backup_path: str | os.PathLike[str],
    db_path: str | os.PathLike[str] | None = None,
) -> RestoreInfo:
    """Restore a verified SQLite backup and preserve a pre-restore safety copy."""
    from src.models.schema import SCHEMA_VERSION

    source_path = Path(backup_path).expanduser().resolve()
    target_path = Path(db_path or get_database_path()).expanduser().resolve()
    if not source_path.is_file():
        raise DatabaseRestoreError(f"备份文件不存在: {source_path}")
    if source_path == target_path:
        raise DatabaseRestoreError("备份文件不能与当前生产库相同")

    try:
        source_version, source_integrity = _database_version_and_integrity(source_path)
    except sqlite3.DatabaseError as exc:
        raise DatabaseRestoreError(f"无法读取备份数据库: {exc}") from exc
    if source_integrity != "ok":
        raise DatabaseRestoreError(f"备份完整性检查失败: {source_integrity}")
    if source_version > SCHEMA_VERSION:
        raise DatabaseRestoreError(
            f"备份版本 {source_version} 高于程序支持的版本 {SCHEMA_VERSION}"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    safety_backup = create_backup(
        target_path,
        prefix="pre_restore_v1.15",
        retain=False,
    )
    try:
        _backup_over_database(source_path, target_path)
        if source_version < SCHEMA_VERSION:
            from src.models.migrations import migrate_database

            migrate_database(target_path)
        restored_version, restored_integrity = _database_version_and_integrity(target_path)
        if restored_integrity != "ok":
            raise sqlite3.DatabaseError(f"恢复后完整性检查失败: {restored_integrity}")
        if restored_version != SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"恢复后版本不一致: 期望 {SCHEMA_VERSION}，目标 {restored_version}"
            )
    except Exception as exc:
        if safety_backup is not None:
            try:
                _backup_over_database(safety_backup.path, target_path)
            except Exception as rollback_exc:
                raise DatabaseRestoreError(
                    f"恢复失败，且安全备份回滚失败: {exc}；{rollback_exc}",
                    safety_backup,
                ) from exc
        raise DatabaseRestoreError(
            f"恢复失败，已保留当前数据库: {exc}",
            safety_backup,
        ) from exc

    return RestoreInfo(
        restored_from=source_path,
        restored_sha256=_sha256(source_path),
        safety_backup=safety_backup,
    )

