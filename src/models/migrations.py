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


def _needs_v2_schema_repair(conn: sqlite3.Connection) -> bool:
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


LEGACY_MONEY_COLUMNS = {
    "batches": ("purchase_price", "purchase_price_cents"),
    "quotes": ("quote_price", "quote_price_cents"),
    "payments": ("amount", "amount_cents"),
    "customers": ("balance", "balance_cents"),
    "suppliers": ("balance", "balance_cents"),
}


def _validate_v2_money(conn: sqlite3.Connection) -> None:
    """Refuse to discard a legacy REAL value that differs from its cents value."""
    problems: list[str] = []
    pairs = [
        *(
            (table, source, target)
            for table, (source, target) in LEGACY_MONEY_COLUMNS.items()
        ),
        ("quotes", "received_amount", "received_amount_cents"),
    ]
    for table, source, target in pairs:
        if source not in _columns(conn, table) or target not in _columns(conn, table):
            problems.append(f"{table}.{source}/{target} 缺失")
            continue
        for row in conn.execute(f'SELECT id,"{source}","{target}" FROM "{table}"'):
            expected = _to_cents(row[source], table, row["id"], source)
            if expected != row[target]:
                problems.append(
                    f"{table}#{row['id']} {source}={row[source]!r}，"
                    f"{target}={row[target]!r}"
                )
                if len(problems) >= 20:
                    break
        if len(problems) >= 20:
            break
    if problems:
        raise DatabaseMigrationError(
            "REAL/整数分金额不一致，拒绝删除旧字段：\n" + "\n".join(problems)
        )


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Drop legacy REAL money columns; SQLite performs a transactional rebuild."""
    _validate_v2_money(conn)
    for table, source in (
        ("batches", "purchase_price"),
        ("quotes", "quote_price"),
        ("quotes", "received_amount"),
        ("payments", "amount"),
        ("customers", "balance"),
        ("suppliers", "balance"),
    ):
        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{source}"')

    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action)",
    ):
        conn.execute(statement)


def _verify_v3_schema(conn: sqlite3.Connection) -> None:
    forbidden = [
        f"{table}.{column}"
        for table, column in (
            ("batches", "purchase_price"),
            ("quotes", "quote_price"),
            ("quotes", "received_amount"),
            ("payments", "amount"),
            ("customers", "balance"),
            ("suppliers", "balance"),
        )
        if column in _columns(conn, table)
    ]
    if forbidden:
        raise DatabaseMigrationError(
            "schema v3 仍包含旧金额字段: " + ", ".join(forbidden)
        )
    required = {
        "batches": {"purchase_price_cents"},
        "quotes": {"quote_price_cents", "received_amount_cents"},
        "payments": {"amount_cents", "entry_kind", "reversal_of_id", "supersedes_id"},
        "customers": {"balance_cents"},
        "suppliers": {"balance_cents"},
    }
    missing = [
        f"{table}.{column}"
        for table, columns in required.items()
        for column in columns
        if column not in _columns(conn, table)
    ]
    if missing:
        raise DatabaseMigrationError("schema v3 缺少字段: " + ", ".join(missing))


def _execute_current_schema(conn: sqlite3.Connection) -> None:
    """Execute the idempotent schema without sqlite3.executescript implicit commits."""
    for statement in CURRENT_SCHEMA_SQL.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add the operating ledger while leaving historical business rows untouched."""
    _execute_current_schema(conn)
    _add_column(
        conn,
        "payments",
        "account_id INTEGER REFERENCES ledger_accounts(id)",
    )


def _verify_v4_schema(conn: sqlite3.Connection) -> None:
    _verify_v3_schema(conn)
    required_tables = {
        "finance_settings",
        "ledger_accounts",
        "finance_categories",
        "ledger_entries",
        "ledger_lines",
        "shipment_snapshots",
        "supplier_payment_allocations",
        "sales_returns",
        "purchase_returns",
    }
    missing_tables = sorted(
        table for table in required_tables if not _table_exists(conn, table)
    )
    if missing_tables:
        raise DatabaseMigrationError(
            "schema v4 missing tables: " + ", ".join(missing_tables)
        )
    if "account_id" not in _columns(conn, "payments"):
        raise DatabaseMigrationError("schema v4 missing payments.account_id")
    system_codes = {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM ledger_accounts WHERE is_system=1"
        )
    }
    required_codes = {
        "AR",
        "AP",
        "INVENTORY",
        "SALES",
        "COGS",
        "EXPENSE",
        "OTHER_INCOME",
        "OWNER_EQUITY",
    }
    if not required_codes.issubset(system_codes):
        raise DatabaseMigrationError("schema v4 system ledger accounts are incomplete")


def _migration_snapshot(conn: sqlite3.Connection) -> dict[str, int]:
    snapshot = {
        f"count:{table}": int(
            conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        )
        for table in (
            "products",
            "batches",
            "customers",
            "suppliers",
            "quotes",
            "payments",
        )
        if _table_exists(conn, table)
    }
    money_columns = (
        ("batches", "purchase_price_cents"),
        ("quotes", "quote_price_cents"),
        ("quotes", "received_amount_cents"),
        ("payments", "amount_cents"),
        ("customers", "balance_cents"),
        ("suppliers", "balance_cents"),
    )
    for table, column in money_columns:
        if _table_exists(conn, table) and column in _columns(conn, table):
            snapshot[f"sum:{table}.{column}"] = int(
                conn.execute(
                    f'SELECT COALESCE(SUM("{column}"),0) FROM "{table}"'
                ).fetchone()[0]
            )
    return snapshot


def _verify_database(
    conn: sqlite3.Connection,
    backup: BackupInfo | None,
) -> None:
    _verify_v4_schema(conn)
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise DatabaseMigrationError(f"迁移后完整性检查失败: {check}", backup)
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        sample = ", ".join(
            f"{row['table']}#{row['rowid']}" for row in foreign_keys[:10]
        )
        raise DatabaseMigrationError(f"迁移后外键检查失败: {sample}", backup)


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
            conn.execute("BEGIN IMMEDIATE")
            _execute_current_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (?,?)",
                (SCHEMA_VERSION, "fresh_schema_v4"),
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            _verify_database(conn, None)
            conn.commit()
            return None

        if version < SCHEMA_VERSION:
            before = _migration_snapshot(conn)
            conn.close()
            backup = create_backup(path, prefix="pre_migration_v1.16", retain=False)
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
            if version < 2:
                _migrate_legacy_to_v2(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (2,?)",
                    ("cents_soft_delete_ledger",),
                )
            elif version < 3 and _needs_v2_schema_repair(conn):
                _migrate_legacy_to_v2(conn)
            if version < 3:
                _migrate_v2_to_v3(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (3,?)",
                    ("integer_money_audit_ui",),
                )
            if version < 4:
                _migrate_v3_to_v4(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (4,?)",
                    ("operating_finance_ledger",),
                )
            conn.execute("PRAGMA user_version=4")
            after = _migration_snapshot(conn)
            changed = {
                key: (value, after.get(key))
                for key, value in before.items()
                if after.get(key) != value
            }
            if changed:
                details = "；".join(
                    f"{key}: {values[0]} -> {values[1]}"
                    for key, values in sorted(changed.items())
                )
                raise DatabaseMigrationError(
                    f"迁移前后核心记录或金额不一致：{details}",
                    backup,
                )
            _verify_database(conn, backup)
            conn.commit()

        _verify_database(conn, backup)
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
