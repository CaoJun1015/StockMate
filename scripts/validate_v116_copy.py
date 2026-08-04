"""Create and validate a schema-v4 migration copy without touching production."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from src.models.connection import connect
from src.models.migrations import migrate_database


CORE_TABLES = (
    "products",
    "batches",
    "customers",
    "suppliers",
    "quotes",
    "payments",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(path: Path) -> dict:
    conn = connect(path, read_only=True)
    try:
        counts = {
            table: int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in CORE_TABLES
        }
        customer_receipts = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(
                    CASE WHEN entry_kind='reversal'
                         THEN -amount_cents ELSE amount_cents END
                ),0)
                FROM payments WHERE type='receivable'
                """
            ).fetchone()[0]
        )
        supplier_balances = int(
            conn.execute(
                "SELECT COALESCE(SUM(balance_cents),0) FROM suppliers"
            ).fetchone()[0]
        )
        return {
            "schema_version": int(
                conn.execute("PRAGMA user_version").fetchone()[0]
            ),
            "counts": counts,
            "customer_receipts_cents": customer_receipts,
            "supplier_balance_cents": supplier_balances,
            "integrity_check": conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0],
            "foreign_key_issues": len(
                conn.execute("PRAGMA foreign_key_check").fetchall()
            ),
        }
    finally:
        conn.close()


def validate(source: Path, output: Path) -> dict:
    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    before = _snapshot(source)
    source_conn = connect(source, read_only=True)
    target_conn = sqlite3.connect(output)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()

    copy_sha256 = _sha256(output)
    migration_backup = migrate_database(output)
    after = _snapshot(output)
    if before["counts"] != after["counts"]:
        raise RuntimeError("migration changed core table counts")
    if before["customer_receipts_cents"] != after["customer_receipts_cents"]:
        raise RuntimeError("migration changed customer receipt total")
    if before["supplier_balance_cents"] != after["supplier_balance_cents"]:
        raise RuntimeError("migration changed supplier balance total")
    if after["schema_version"] != 4:
        raise RuntimeError("migration did not reach schema v4")
    if after["integrity_check"] != "ok" or after["foreign_key_issues"]:
        raise RuntimeError("migrated copy failed SQLite checks")
    return {
        "source": str(source),
        "copy": str(output),
        "copy_sha256_before_migration": copy_sha256,
        "migration_backup": str(migration_backup.path)
        if migration_backup
        else None,
        "migration_backup_sha256": migration_backup.sha256
        if migration_backup
        else None,
        "before": before,
        "after": after,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            validate(args.source, args.output),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
