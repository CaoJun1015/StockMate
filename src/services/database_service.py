"""Database startup orchestration outside the compatibility facade."""

from __future__ import annotations

from pathlib import Path

from src.models.connection import create_backup, get_database_path
from src.models.migrations import migrate_database
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
