"""Transactional schema migrations for existing databases."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from src.models.connection import BackupInfo, connect, create_backup
from src.models.schema import CURRENT_SCHEMA_SQL, SCHEMA_VERSION


class DatabaseMigrationError(RuntimeError):
    def __init__(self, message: str, backup: BackupInfo | None = None):
        super().__init__(message)
        self.backup = backup


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _to_cents(value: object, table: str, record_id: int, column: str) -> int:
    decimal = Decimal(str(value or 0))
    cents = decimal * 100
    rounded = cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # Legacy REAL values may contain tiny IEEE-754 noise (for example
    # -0.009999999998 instead of -0.01).  Reject real sub-cent data while
    # accepting differences smaller than one millionth of a cent.
    if abs(cents - rounded) > Decimal("0.000001"):
        raise DatabaseMigrationError(
            f"{table}#{record_id} 的 {column} 超过两位小数，拒绝静默舍入"
        )
    return int(rounded)


def _backfill_money(
    conn: sqlite3.Connection,
    table: str,
    source: str,
    target: str,
) -> None:
    for row in conn.execute(f'SELECT id, "{source}" FROM "{table}"'):
        conn.execute(
            f'UPDATE "{table}" SET "{target}"=? WHERE id=?',
            (_to_cents(row[1], table, row[0], source), row[0]),
        )


def _ensure_legacy_compatibility_columns(conn: sqlite3.Connection) -> None:
    """Bring databases from any pre-v1.13 application release to one baseline."""
    for definition in (
        "remark TEXT",
        "supplier_id INTEGER",
        "sn_list TEXT",
    ):
        _add_column(conn, "batches", definition)
    for definition in (
        "paid TEXT",
        "quote_quantity INTEGER NOT NULL DEFAULT 1",
        "status TEXT NOT NULL DEFAULT '待确认'",
        "received_amount REAL NOT NULL DEFAULT 0",
        "sn_list TEXT",
        "tax_rate REAL DEFAULT NULL",
        "purchase_tax_inclusive INTEGER NOT NULL DEFAULT 0",
        "quote_tax_inclusive INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "quotes", definition)
    for definition in (
        "balance REAL NOT NULL DEFAULT 0",
        "default_tax_rate REAL DEFAULT NULL",
    ):
        _add_column(conn, "customers", definition)
    _add_column(conn, "suppliers", "balance REAL NOT NULL DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date TEXT NOT NULL,
            item_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshot_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            series TEXT,
            cpu TEXT,
            ram TEXT,
            storage TEXT,
            gpu TEXT,
            note TEXT,
            norm_key TEXT,
            FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
        )
    """)


def _needs_current_schema_repair(conn: sqlite3.Connection) -> bool:
    required = {
        "products": {"deleted_at", "deleted_reason"},
        "batches": {
            "remark", "supplier_id", "sn_list", "purchase_price_cents",
            "deleted_at", "deleted_reason",
        },
        "quotes": {
            "paid", "quote_quantity", "status", "received_amount", "sn_list",
            "tax_rate", "purchase_tax_inclusive", "quote_tax_inclusive",
            "quote_price_cents", "received_amount_cents", "deleted_at", "deleted_reason",
        },
        "customers": {
            "balance", "balance_cents", "default_tax_rate", "deleted_at", "deleted_reason",
        },
        "suppliers": {"balance", "balance_cents", "deleted_at", "deleted_reason"},
        "payments": {
            "amount_cents", "entry_kind", "reversal_of_id", "supersedes_id",
        },
    }
    missing_columns = any(
        not columns.issubset(_columns(conn, table)) for table, columns in required.items()
    )
    missing_tables = any(
        not _table_exists(conn, table)
        for table in ("payment_allocations", "audit_events", "schema_migrations")
    )
    return missing_columns or missing_tables


def _migrate_legacy_to_v2(conn: sqlite3.Connection) -> None:
    required = {"products", "batches", "customers", "suppliers", "quotes", "payments"}
    missing = sorted(table for table in required if not _table_exists(conn, table))
    if missing:
        raise DatabaseMigrationError(f"旧数据库缺少必要表: {', '.join(missing)}")

    _ensure_legacy_compatibility_columns(conn)

    for table in ("products", "batches", "customers", "suppliers", "quotes"):
        _add_column(conn, table, "deleted_at TIMESTAMP")
        _add_column(conn, table, "deleted_reason TEXT")

    _add_column(conn, "batches", "purchase_price_cents INTEGER")
    _add_column(conn, "quotes", "quote_price_cents INTEGER")
    _add_column(conn, "quotes", "received_amount_cents INTEGER")
    _add_column(conn, "payments", "amount_cents INTEGER")
    _add_column(conn, "payments", "entry_kind TEXT NOT NULL DEFAULT 'payment'")
    _add_column(conn, "payments", "reversal_of_id INTEGER")
    _add_column(conn, "payments", "supersedes_id INTEGER")
    _add_column(conn, "customers", "balance_cents INTEGER")
    _add_column(conn, "suppliers", "balance_cents INTEGER")

    _backfill_money(conn, "batches", "purchase_price", "purchase_price_cents")
    _backfill_money(conn, "quotes", "quote_price", "quote_price_cents")
    _backfill_money(conn, "quotes", "received_amount", "received_amount_cents")
    _backfill_money(conn, "payments", "amount", "amount_cents")
    _backfill_money(conn, "customers", "balance", "balance_cents")
    _backfill_money(conn, "suppliers", "balance", "balance_cents")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            quote_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (payment_id) REFERENCES payments(id),
            FOREIGN KEY (quote_id) REFERENCES quotes(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("DELETE FROM payment_allocations")
    quote_remaining: dict[int, int] = {
        row["id"]: row["received_amount_cents"]
        for row in conn.execute(
            "SELECT id, received_amount_cents FROM quotes ORDER BY quote_date, id"
        )
    }
    quotes_by_customer: dict[int, list[int]] = {}
    for row in conn.execute(
        "SELECT id, customer_id FROM quotes WHERE customer_id IS NOT NULL "
        "ORDER BY quote_date, id"
    ):
        quotes_by_customer.setdefault(row["customer_id"], []).append(row["id"])

    for payment in conn.execute(
        "SELECT id, quote_id, customer_id, amount_cents FROM payments "
        "WHERE type='receivable' AND entry_kind='payment' ORDER BY pay_date, id"
    ):
        remaining = payment["amount_cents"]
        candidates = (
            [payment["quote_id"]]
            if payment["quote_id"] is not None
            else quotes_by_customer.get(payment["customer_id"], [])
        )
        for quote_id in candidates:
            available = quote_remaining.get(quote_id, 0)
            if available <= 0:
                continue
            allocated = min(remaining, available)
            conn.execute(
                "INSERT INTO payment_allocations(payment_id, quote_id, amount_cents) "
                "VALUES (?,?,?)",
                (payment["id"], quote_id, allocated),
            )
            quote_remaining[quote_id] -= allocated
            remaining -= allocated
            if remaining == 0:
                break
        if remaining:
            raise DatabaseMigrationError(
                f"收款#{payment['id']} 有 {remaining} 分无法分配到历史报价"
            )

    unresolved = {qid: amount for qid, amount in quote_remaining.items() if amount}
    if unresolved:
        sample = ", ".join(f"quote#{qid}={amount}" for qid, amount in list(unresolved.items())[:5])
        raise DatabaseMigrationError(f"历史已收金额无法由流水完整解释: {sample}")

    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_supplier ON payments(supplier_id)",
        "CREATE INDEX IF NOT EXISTS idx_allocations_payment ON payment_allocations(payment_id)",
        "CREATE INDEX IF NOT EXISTS idx_allocations_quote ON payment_allocations(quote_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_reversal_per_payment "
        "ON payments(reversal_of_id) WHERE reversal_of_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id)",
    ):
        conn.execute(statement)


def migrate_database(db_path: str | Path) -> BackupInfo | None:
    path = Path(db_path)
    backup: BackupInfo | None = None
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise DatabaseMigrationError(
                f"数据库版本 {version} 高于程序支持的版本 {SCHEMA_VERSION}"
            )

        is_fresh = not _table_exists(conn, "products")
        if is_fresh:
            conn.executescript(CURRENT_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (?,?)",
                (SCHEMA_VERSION, "fresh_schema_v2"),
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            return None

        if version < SCHEMA_VERSION:
            conn.close()
            backup = create_backup(path, prefix="pre_migration_v1.13", retain=False)
            conn = connect(path)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            if version == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, name) VALUES (1,?)",
                    ("legacy_schema_validated",),
                )
            _migrate_legacy_to_v2(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (2,?)",
                ("cents_soft_delete_ledger",),
            )
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        elif version == SCHEMA_VERSION:
            # Structural verification is intentionally idempotent. It also
            # repairs a v2 database produced by an interrupted/older v1.13
            # candidate without changing business rows.
            if _needs_current_schema_repair(conn):
                conn.close()
                backup = create_backup(path, prefix="pre_migration_v1.13", retain=False)
                conn = connect(path)
                conn.execute("BEGIN IMMEDIATE")
                _migrate_legacy_to_v2(conn)
                conn.commit()

        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise DatabaseMigrationError(f"迁移后完整性检查失败: {check}", backup)
        return backup
    except DatabaseMigrationError as exc:
        if conn.in_transaction:
            conn.rollback()
        if exc.backup is None:
            exc.backup = backup
        raise
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        raise DatabaseMigrationError(f"数据库迁移失败: {exc}", backup) from exc
    finally:
        conn.close()
