"""Transactional schema migrations for existing databases."""

from __future__ import annotations

import json
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
    pairs = [
        *(
            (table, source, target)
            for table, (source, target) in LEGACY_MONEY_COLUMNS.items()
        ),
        ("quotes", "received_amount", "received_amount_cents"),
    ]
    # 修复：对 cents 列为 NULL 的行做一次补回填
    # 处理之前迁移中断导致列已创建但数据未填充的情况
    for table, source, target in pairs:
        if source not in _columns(conn, table) or target not in _columns(conn, table):
            continue
        for row in conn.execute(
            f'SELECT id, "{source}" FROM "{table}" WHERE "{target}" IS NULL'
        ):
            conn.execute(
                f'UPDATE "{table}" SET "{target}"=? WHERE id=?',
                (_to_cents(row[1], table, row[0], source), row[0]),
            )

    problems: list[str] = []
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


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Add the inventory subledger and reconstruct all explainable history."""
    # Make the legacy unit-cost cache nullable. Mixed-cost shipments use the
    # exact aggregate cost plus allocation rows instead of a rounded unit cost.
    if _table_exists(conn, "shipment_snapshots"):
        unit_column = next(
            row for row in conn.execute("PRAGMA table_info(shipment_snapshots)")
            if row[1] == "unit_cost_cents"
        )
        if unit_column[3]:
            conn.execute("""
                CREATE TABLE shipment_snapshots_v5 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quote_id INTEGER NOT NULL UNIQUE,
                    shipped_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    unit_sale_cents INTEGER NOT NULL,
                    unit_cost_cents INTEGER,
                    revenue_cents INTEGER NOT NULL,
                    cost_cents INTEGER NOT NULL,
                    ledger_entry_id INTEGER NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (quote_id) REFERENCES quotes(id),
                    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
                )
            """)
            conn.execute("""
                INSERT INTO shipment_snapshots_v5
                SELECT id,quote_id,shipped_date,quantity,unit_sale_cents,
                       unit_cost_cents,revenue_cents,cost_cents,ledger_entry_id,created_at
                FROM shipment_snapshots
            """)
            conn.execute("DROP TABLE shipment_snapshots")
            conn.execute("ALTER TABLE shipment_snapshots_v5 RENAME TO shipment_snapshots")

    _execute_current_schema(conn)

    # Every batch starts with one immutable receipt movement.
    for batch in conn.execute("SELECT * FROM batches ORDER BY id").fetchall():
        ledger = conn.execute(
            """SELECT id FROM ledger_entries
               WHERE event_type='inventory_receipt' AND source_type='batch'
                 AND source_id=CAST(? AS TEXT) ORDER BY id LIMIT 1""",
            (batch["id"],),
        ).fetchone()
        conn.execute(
            """INSERT OR IGNORE INTO inventory_movements(
                movement_date,movement_type,product_id,batch_id,quantity_delta,
                unit_cost_cents,total_cost_cents,source_type,source_id,
                ledger_entry_id,sn_list,idempotency_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                batch["date"], "purchase_receipt", batch["product_id"], batch["id"],
                batch["quantity"], batch["purchase_price_cents"],
                batch["quantity"] * batch["purchase_price_cents"], "batch",
                str(batch["id"]), ledger["id"] if ledger else None,
                batch["sn_list"] or "", f"batch:{batch['id']}:receipt",
            ),
        )

    # v4 supports one snapshot per quote and historically one batch per quote.
    for snapshot in conn.execute(
        """SELECT ss.*,q.batch_id,q.sn_list,b.product_id,b.purchase_price_cents
           FROM shipment_snapshots ss
           JOIN quotes q ON q.id=ss.quote_id
           JOIN batches b ON b.id=q.batch_id ORDER BY ss.id"""
    ).fetchall():
        allocation = conn.execute(
            "SELECT id FROM shipment_allocations WHERE shipment_snapshot_id=? AND batch_id=?",
            (snapshot["id"], snapshot["batch_id"]),
        ).fetchone()
        if allocation is None:
            allocation_id = conn.execute(
                """INSERT INTO shipment_allocations(
                    shipment_snapshot_id,batch_id,quantity,unit_cost_cents,cost_cents,sn_list
                ) VALUES (?,?,?,?,?,?)""",
                (
                    snapshot["id"], snapshot["batch_id"], snapshot["quantity"],
                    snapshot["purchase_price_cents"], snapshot["cost_cents"],
                    snapshot["sn_list"] or "",
                ),
            ).lastrowid
        else:
            allocation_id = allocation["id"]
        conn.execute(
            """INSERT OR IGNORE INTO inventory_movements(
                movement_date,movement_type,product_id,batch_id,quantity_delta,
                unit_cost_cents,total_cost_cents,source_type,source_id,
                shipment_allocation_id,ledger_entry_id,sn_list,idempotency_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot["shipped_date"], "sales_shipment", snapshot["product_id"],
                snapshot["batch_id"], -snapshot["quantity"],
                snapshot["purchase_price_cents"], snapshot["cost_cents"], "quote",
                str(snapshot["quote_id"]), allocation_id, snapshot["ledger_entry_id"],
                snapshot["sn_list"] or "", f"shipment-allocation:{allocation_id}",
            ),
        )

    for record in conn.execute(
        """SELECT sr.*,q.batch_id,b.product_id,b.purchase_price_cents,sa.id allocation_id
           FROM sales_returns sr JOIN quotes q ON q.id=sr.quote_id
           JOIN batches b ON b.id=q.batch_id
           LEFT JOIN shipment_snapshots ss ON ss.quote_id=q.id
           LEFT JOIN shipment_allocations sa
             ON sa.shipment_snapshot_id=ss.id AND sa.batch_id=q.batch_id
           WHERE sr.restock_quantity>0 ORDER BY sr.id"""
    ).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO inventory_movements(
                movement_date,movement_type,product_id,batch_id,quantity_delta,
                unit_cost_cents,total_cost_cents,source_type,source_id,
                shipment_allocation_id,ledger_entry_id,sn_list,idempotency_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["return_date"], "sales_return", record["product_id"],
                record["batch_id"], record["restock_quantity"],
                record["purchase_price_cents"],
                record["restock_quantity"] * record["purchase_price_cents"],
                "sales_return", str(record["id"]), record["allocation_id"],
                record["ledger_entry_id"], "", f"sales-return:{record['id']}:batch:{record['batch_id']}",
            ),
        )

    for record in conn.execute(
        """SELECT pr.*,b.product_id,b.purchase_price_cents
           FROM purchase_returns pr JOIN batches b ON b.id=pr.batch_id ORDER BY pr.id"""
    ).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO inventory_movements(
                movement_date,movement_type,product_id,batch_id,quantity_delta,
                unit_cost_cents,total_cost_cents,source_type,source_id,
                ledger_entry_id,sn_list,idempotency_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["return_date"], "purchase_return", record["product_id"],
                record["batch_id"], -record["quantity"], record["purchase_price_cents"],
                record["amount_cents"], "purchase_return", str(record["id"]),
                record["ledger_entry_id"], "", f"purchase-return:{record['id']}:inventory",
            ),
        )

    # Preserve unexplained legacy stock as an explicit, non-GL migration event.
    for batch in conn.execute("SELECT * FROM batches ORDER BY id").fetchall():
        calculated = int(conn.execute(
            "SELECT COALESCE(SUM(quantity_delta),0) FROM inventory_movements WHERE batch_id=?",
            (batch["id"],),
        ).fetchone()[0])
        delta = batch["remaining"] - calculated
        if delta:
            conn.execute(
                """INSERT INTO inventory_movements(
                    movement_date,movement_type,product_id,batch_id,quantity_delta,
                    unit_cost_cents,total_cost_cents,source_type,source_id,
                    sn_list,idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch["date"], "migration_adjustment", batch["product_id"],
                    batch["id"], delta, batch["purchase_price_cents"],
                    abs(delta) * batch["purchase_price_cents"], "migration_v5",
                    str(batch["id"]), "", f"migration-v5:batch:{batch['id']}:gap",
                ),
            )


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Recover return allocations without changing stock or financial history."""
    _execute_current_schema(conn)
    for record in conn.execute("SELECT * FROM sales_returns ORDER BY id").fetchall():
        if conn.execute("SELECT 1 FROM sales_return_allocations WHERE sales_return_id=?",
                        (record["id"],)).fetchone():
            continue
        originals = {row["id"]: dict(row) for row in conn.execute(
            "SELECT sa.* FROM shipment_allocations sa JOIN shipment_snapshots ss "
            "ON ss.id=sa.shipment_snapshot_id WHERE ss.quote_id=?", (record["quote_id"],),
        )}
        event = conn.execute(
            "SELECT after_json FROM audit_events WHERE entity_type='sales_returns' "
            "AND entity_id=? AND action='create' ORDER BY id DESC LIMIT 1", (record["id"],),
        ).fetchone()
        try:
            items = json.loads(event[0]).get("allocations", []) if event else []
        except (ValueError, TypeError, AttributeError):
            items = []
        valid = bool(items) and all(
            isinstance(item, dict) and item.get("shipment_allocation_id") in originals
            and type(item.get("quantity")) is int and item["quantity"] > 0
            for item in items
        )
        if not valid or sum(item["quantity"] for item in items) != record["quantity"]:
            items = [dict(shipment_allocation_id=row["shipment_allocation_id"],
                          quantity=row["quantity_delta"], sn_list=row["sn_list"])
                     for row in conn.execute(
                         "SELECT * FROM inventory_movements WHERE source_type='sales_return' "
                         "AND source_id=? AND movement_type='sales_return'", (str(record["id"]),),
                     ) if row["shipment_allocation_id"] in originals]
        if sum(item["quantity"] for item in items) != record["quantity"]:
            if len(originals) != 1:
                # Missing cross-batch history cannot be guessed. Reconciliation
                # reports the gap and further returns on this quote are blocked.
                continue
            items = [dict(shipment_allocation_id=next(iter(originals)),
                          quantity=record["quantity"], sn_list="")]
        remaining_restock = record["restock_quantity"]
        for item in items:
            restocked = min(remaining_restock, item["quantity"])
            conn.execute(
                "INSERT INTO sales_return_allocations "
                "(sales_return_id,shipment_allocation_id,quantity,restock_quantity,sn_list) "
                "VALUES (?,?,?,?,?)",
                (record["id"], item["shipment_allocation_id"], item["quantity"],
                 restocked, item.get("sn_list", "")),
            )
            remaining_restock -= restocked


def _verify_v5_schema(conn: sqlite3.Connection) -> None:
    _verify_v4_schema(conn)
    for table in ("shipment_allocations", "inventory_movements"):
        if not _table_exists(conn, table):
            raise DatabaseMigrationError(f"schema v5 missing table: {table}")
    mismatches = conn.execute(
        """SELECT b.id,b.remaining,COALESCE(SUM(im.quantity_delta),0) movement_balance
           FROM batches b LEFT JOIN inventory_movements im ON im.batch_id=b.id
           GROUP BY b.id HAVING b.remaining!=movement_balance LIMIT 10"""
    ).fetchall()
    if mismatches:
        detail = ", ".join(
            f"batch#{row['id']}={row['remaining']}/{row['movement_balance']}"
            for row in mismatches
        )
        raise DatabaseMigrationError("库存流水回建后不守恒: " + detail)


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
    _verify_v5_schema(conn)
    if not _table_exists(conn, "sales_return_allocations"):
        raise DatabaseMigrationError("schema v6 missing sales_return_allocations")
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
                (SCHEMA_VERSION, "fresh_schema_v6"),
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            _verify_database(conn, None)
            conn.commit()
            return None

        if version < SCHEMA_VERSION:
            before = _migration_snapshot(conn)
            conn.close()
            backup = create_backup(path, prefix="pre_migration_v1.17", retain=False)
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
            if version < 3:
                v3_cols = _columns(conn, "customers")
                if "balance" not in v3_cols and "balance_cents" in v3_cols:
                    raise DatabaseMigrationError(
                        f"数据库 schema 已是 v3+（无 REAL 金额列），但 user_version={version}。"
                        "请勿手动修改数据库版本号。"
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
            if version < 5:
                _migrate_v4_to_v5(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name) VALUES (5,?)",
                    ("inventory_subledger_allocations",),
                )
            if version < 6:
                _migrate_v5_to_v6(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version,name) VALUES (6,?)",
                    ("sales_return_allocations",),
                )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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
