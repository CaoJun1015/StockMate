"""Database startup orchestration outside the compatibility facade."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from src.models.connection import (
    create_backup, get_database_path, transaction, copy_database, connect,
    database_version_and_integrity, file_sha256, DatabaseRestoreError, RestoreInfo,
)
from src.models.migrations import migrate_database, rebuild_import_history
from src.models.schema import SCHEMA_VERSION
from src.models.repositories import require_empty_import_target
from src.services.reconciliation_service import (
    ReconciliationReport,
    ReconciliationService,
)


def reconcile_database(db_path=None) -> ReconciliationReport:
    return ReconciliationService(db_path or get_database_path()).run()


def initialize_database(db_path=None) -> tuple[bool, str]:
    path = Path(db_path or get_database_path())
    migration_backup = migrate_database(path)
    report = reconcile_database(path)
    reconciliation_note = (
        "自动对账通过"
        if report.is_clean
        else f"自动对账发现 {len(report.issues)} 个问题，请在“数据安全”菜单查看"
    )
    try:
        regular_backup = create_backup(path)
        backup_note = (
            f"备份成功: {regular_backup.path.name}"
            if regular_backup
            else "数据库文件不存在，跳过备份"
        )
    except Exception as exc:
        backup_note = f"备份失败: {exc}"
    migration_note = (
        f"；迁移备份: {migration_backup.path.name}" if migration_backup else ""
    )
    return (
        True,
        f"数据库初始化成功{migration_note}；{reconciliation_note}；{backup_note}",
    )


def import_json_into_empty_database(json_path, db_path) -> dict:
    """Low-level compatibility entry for an explicit staging destination, never merge."""
    from src.utils.json_export import read_json_backup, populate_json_database

    if db_path is None or Path(db_path).resolve() == get_database_path().resolve():
        raise ValueError("JSON 只能导入隔离空库，请使用完整备份恢复")
    document = read_json_backup(json_path)
    if Path(db_path).exists():
        conn = connect(db_path, read_only=True)
        try:
            require_empty_import_target(conn)
        finally:
            conn.close()
    migrate_database(db_path)
    with transaction(db_path) as conn:
        require_empty_import_target(conn)
        stats = populate_json_database(conn, document)
        rebuild_import_history(conn, document.get("schema_version", 0))
    return stats


@dataclass(frozen=True)
class PreparedRestore:
    source: Path
    staged: Path
    report: ReconciliationReport
    sha256: str


def _require_clean_database(path):
    version, integrity = database_version_and_integrity(path)
    if version != SCHEMA_VERSION or integrity != "ok":
        raise DatabaseRestoreError("恢复库版本或完整性检查失败")
    report = reconcile_database(path)
    if not report.is_clean:
        raise DatabaseRestoreError("恢复被阻止，当前库未替换。请先核对备份异常：\n" + report.format_text())
    return report


@contextmanager
def prepare_database_restore(backup_path, db_path=None, *, backup_format="sqlite"):
    """Validate a private snapshot before presenting any destructive confirmation."""
    source = Path(backup_path).expanduser().resolve()
    target = Path(db_path or get_database_path()).expanduser().resolve()
    if source == target:
        raise DatabaseRestoreError("备份文件不能与当前生产库相同")
    if not source.is_file():
        raise DatabaseRestoreError(f"备份文件不存在: {source}")
    with TemporaryDirectory(prefix="diaohuo-restore-") as folder:
        staged = Path(folder) / "prepared.db"
        try:
            if backup_format == "json":
                import_json_into_empty_database(source, staged)
            elif backup_format == "sqlite":
                version, integrity = database_version_and_integrity(source)
                if version > SCHEMA_VERSION:
                    raise DatabaseRestoreError("备份版本高于程序支持的版本")
                if integrity != "ok":
                    raise DatabaseRestoreError("备份文件完整性检查失败")
                copy_database(source, staged)
                migrate_database(staged)
            else:
                raise ValueError("不支持的备份类型")
            report = _require_clean_database(staged)
            prepared = PreparedRestore(source, staged, report, file_sha256(source))
        except DatabaseRestoreError:
            raise
        except Exception as exc:
            raise DatabaseRestoreError(f"恢复预检失败，当前库未替换：{exc}") from exc
        yield prepared


def apply_database_restore(prepared: PreparedRestore, db_path=None) -> RestoreInfo:
    """Caller suspends UI writes; only a validated staging copy can be applied."""
    target = Path(db_path or get_database_path()).expanduser().resolve()
    _require_clean_database(prepared.staged)
    existed = target.exists()
    safety = None
    try:
        safety = create_backup(target, prefix="pre_restore", retain=False)
        if existed and safety is None:
            raise DatabaseRestoreError("无法创建恢复前安全备份")
    except Exception as exc:
        raise DatabaseRestoreError(f"恢复前备份失败，当前库未替换：{exc}") from exc
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_database(prepared.staged, target)
        _require_clean_database(target)
    except Exception as exc:
        if safety:
            try:
                copy_database(safety.path, target)
                if database_version_and_integrity(target)[1] != "ok":
                    raise ValueError("安全备份回滚完整性检查失败")
            except Exception as rollback:
                raise DatabaseRestoreError(f"恢复失败且回滚失败：{exc}；{rollback}", safety) from exc
        elif not existed:
            for path in (target, Path(str(target) + "-wal"), Path(str(target) + "-shm")):
                path.unlink(missing_ok=True)
        raise DatabaseRestoreError(f"恢复失败，已还原恢复前状态：{exc}", safety) from exc
    return RestoreInfo(prepared.source, prepared.sha256, safety)


def restore_database(backup_path, db_path=None, *, backup_format="sqlite") -> RestoreInfo:
    """Programmatic full restore; UI uses prepare/apply to preview before confirmation."""
    with prepare_database_restore(backup_path, db_path, backup_format=backup_format) as prepared:
        return apply_database_restore(prepared, db_path)
