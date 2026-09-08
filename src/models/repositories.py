"""Write-oriented repository helpers.

Repositories never open, commit, or close connections. Transaction ownership
belongs to the application services.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any



def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


IMPORT_COLUMNS = {
    "products": (
        "series", "cpu", "ram", "storage", "gpu", "screen", "note",
        "created_at", "deleted_at", "deleted_reason",
    ),
    "suppliers": (
        "name", "wechat", "qq", "phone", "note", "balance_cents",
        "created_at", "deleted_at", "deleted_reason",
    ),
    "customers": (
        "name", "wechat", "qq", "phone", "note", "balance_cents",
        "default_tax_rate", "created_at", "deleted_at", "deleted_reason",
    ),
    "batches": (
        "product_id", "purchase_price_cents", "quantity", "remaining", "date",
        "remark", "supplier_id", "sn_list", "created_at", "deleted_at",
        "deleted_reason",
    ),
    "quotes": (
        "batch_id", "customer_id", "quote_price_cents", "quote_quantity",
        "quote_date", "remark", "paid", "status", "received_amount_cents",
        "sn_list", "tax_rate", "purchase_tax_inclusive",
        "quote_tax_inclusive", "created_at", "deleted_at", "deleted_reason",
    ),
    "payments": (
        "quote_id", "customer_id", "supplier_id", "type", "amount_cents",
        "entry_kind", "reversal_of_id", "supersedes_id", "pay_date", "method",
        "account_id", "remark", "created_at",
    ),
    "payment_allocations": (
        "payment_id", "quote_id", "amount_cents", "created_at",
    ),
    "audit_events": (
        "entity_type", "entity_id", "action", "before_json", "after_json",
        "reason", "created_at",
    ),
    "ledger_accounts": (
        "code", "name", "account_type", "is_system", "is_active", "created_at",
        "deleted_at",
    ),
    "finance_categories": (
        "name", "kind", "affects_profit", "is_system", "is_active", "created_at",
        "deleted_at",
    ),
    "ledger_entries": (
        "entry_date", "event_type", "source_type", "source_id",
        "idempotency_key", "status", "reversal_of_id", "supersedes_id",
        "reason", "remark", "created_at",
    ),
    "ledger_lines": (
        "entry_id", "account_id", "debit_cents", "credit_cents", "customer_id",
        "supplier_id", "quote_id", "batch_id", "category_id", "created_at",
    ),
    "shipment_snapshots": (
        "quote_id", "shipped_date", "quantity", "unit_sale_cents",
        "unit_cost_cents", "revenue_cents", "cost_cents", "ledger_entry_id",
        "created_at",
    ),
    "shipment_allocations": (
        "shipment_snapshot_id", "batch_id", "quantity", "unit_cost_cents",
        "cost_cents", "sn_list", "created_at",
    ),
    "inventory_movements": (
        "movement_date", "movement_type", "product_id", "batch_id",
        "quantity_delta", "unit_cost_cents", "total_cost_cents",
        "source_type", "source_id", "shipment_allocation_id",
        "ledger_entry_id", "sn_list", "idempotency_key", "created_at",
    ),
    "supplier_payment_allocations": (
        "payment_id", "batch_id", "amount_cents", "created_at",
    ),
    "sales_returns": (
        "quote_id", "return_date", "quantity", "revenue_cents", "cost_cents",
        "restock_quantity", "cash_refund_cents", "account_id",
        "ledger_entry_id", "reason", "created_at",
    ),
    "purchase_returns": (
        "batch_id", "supplier_id", "return_date", "quantity", "amount_cents",
        "cash_refund_cents", "account_id", "ledger_entry_id", "reason",
        "created_at",
    ),
}


def import_record(
    conn: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
) -> int:
    columns = IMPORT_COLUMNS.get(table)
    if columns is None:
        raise ValueError(f"不支持导入表: {table}")
    selected = [column for column in columns if column in record]
    placeholders = ",".join("?" for _ in selected)
    names = ",".join(f'"{column}"' for column in selected)
    cursor = conn.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
        tuple(record[column] for column in selected),
    )
    return int(cursor.lastrowid)


def update_import_payment_links(
    conn: sqlite3.Connection,
    payment_id: int,
    *,
    reversal_of_id: int | None,
    supersedes_id: int | None,
) -> None:
    conn.execute(
        "UPDATE payments SET reversal_of_id=?,supersedes_id=? WHERE id=?",
        (reversal_of_id, supersedes_id, payment_id),
    )


def insert_price_snapshot(
    conn: sqlite3.Connection,
    import_date: str,
    products: list[dict[str, Any]],
) -> int:
    cursor = conn.execute(
        "INSERT INTO price_snapshots(import_date,item_count) VALUES (?,?)",
        (import_date, len(products)),
    )
    snapshot_id = int(cursor.lastrowid)
    for product in products:
        conn.execute(
            "INSERT INTO price_snapshot_items "
            "(snapshot_id,series,cpu,ram,storage,gpu,note,norm_key) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                product.get("series", ""),
                product.get("cpu", ""),
                product.get("ram", ""),
                product.get("storage", ""),
                product.get("gpu", ""),
                product.get("note", ""),
                product.get("norm_key", ""),
            ),
        )
    return snapshot_id


def get_active_entity(
    conn: sqlite3.Connection,
    table: str,
    record_id: int,
) -> dict[str, Any] | None:
    allowed = {"products", "batches", "customers", "suppliers", "quotes"}
    if table not in allowed:
        raise ValueError(f"不支持读取表: {table}")
    return row_dict(
        conn.execute(
            f'SELECT * FROM "{table}" WHERE id=? AND deleted_at IS NULL',
            (record_id,),
        ).fetchone()
    )


def get_quote(conn: sqlite3.Connection, quote_id: int) -> dict[str, Any] | None:
    return row_dict(conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone())


def get_payment(conn: sqlite3.Connection, payment_id: int) -> dict[str, Any] | None:
    return row_dict(
        conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    )


def insert_product(
    conn: sqlite3.Connection,
    *,
    series: str,
    cpu: str = "",
    ram: str = "",
    storage: str = "",
    gpu: str = "",
    screen: str = "",
    note: str = "",
) -> int:
    cursor = conn.execute(
        "INSERT INTO products(series,cpu,ram,storage,gpu,screen,note) "
        "VALUES (?,?,?,?,?,?,?)",
        (series, cpu, ram, storage, gpu, screen, note),
    )
    return int(cursor.lastrowid)


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    *,
    series: str,
    cpu: str = "",
    ram: str = "",
    storage: str = "",
    gpu: str = "",
    screen: str = "",
    note: str = "",
) -> None:
    conn.execute(
        "UPDATE products SET series=?,cpu=?,ram=?,storage=?,gpu=?,screen=?,note=? "
        "WHERE id=?",
        (series, cpu, ram, storage, gpu, screen, note, product_id),
    )


def list_active_product_batches(
    conn: sqlite3.Connection,
    product_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM batches WHERE product_id=? AND deleted_at IS NULL",
            (product_id,),
        ).fetchall()
    ]


def list_active_batch_quotes(
    conn: sqlite3.Connection,
    batch_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM quotes WHERE batch_id=? AND deleted_at IS NULL",
            (batch_id,),
        ).fetchall()
    ]


def insert_quote(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    customer_id: int | None,
    quote_price_cents: int,
    quote_quantity: int,
    quote_date: str,
    remark: str,
    tax_rate: float | None,
    purchase_tax_inclusive: bool,
    quote_tax_inclusive: bool,
) -> int:
    cursor = conn.execute(
        "INSERT INTO quotes "
        "(batch_id,customer_id,quote_price_cents,quote_quantity,"
        "quote_date,remark,paid,status,received_amount_cents,"
        "sn_list,tax_rate,purchase_tax_inclusive,quote_tax_inclusive) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            batch_id,
            customer_id,
            quote_price_cents,
            quote_quantity,
            quote_date,
            remark,
            "否",
            "待确认",
            0,
            "",
            tax_rate,
            int(purchase_tax_inclusive),
            int(quote_tax_inclusive),
        ),
    )
    return int(cursor.lastrowid)


def set_quote_status(
    conn: sqlite3.Connection,
    quote_id: int,
    status: str,
    *,
    sn_list: str | None = None,
) -> None:
    if sn_list is None:
        conn.execute("UPDATE quotes SET status=? WHERE id=?", (status, quote_id))
    else:
        conn.execute(
            "UPDATE quotes SET status=?, sn_list=? WHERE id=?",
            (status, sn_list, quote_id),
        )


def update_quote(
    conn: sqlite3.Connection,
    quote_id: int,
    *,
    batch_id: int,
    customer_id: int | None,
    quote_price_cents: int,
    quote_quantity: int,
    quote_date: str,
    remark: str,
    paid: str,
    sn_list: str,
    tax_rate: float | None,
    purchase_tax_inclusive: bool,
    quote_tax_inclusive: bool,
) -> None:
    conn.execute(
        "UPDATE quotes SET batch_id=?,customer_id=?,quote_price_cents=?,"
        "quote_quantity=?,quote_date=?,remark=?,paid=?,sn_list=?,tax_rate=?,"
        "purchase_tax_inclusive=?,quote_tax_inclusive=? WHERE id=?",
        (
            batch_id,
            customer_id,
            quote_price_cents,
            quote_quantity,
            quote_date,
            remark,
            paid,
            sn_list,
            tax_rate,
            int(purchase_tax_inclusive),
            int(quote_tax_inclusive),
            quote_id,
        ),
    )


def insert_batch(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    purchase_price_cents: int,
    quantity: int,
    date: str,
    remark: str,
    supplier_id: int | None,
    sn_list: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO batches "
        "(product_id,purchase_price_cents,quantity,remaining,"
        "date,remark,supplier_id,sn_list) VALUES (?,?,?,?,?,?,?,?)",
        (
            product_id,
            purchase_price_cents,
            quantity,
            quantity,
            date,
            remark,
            supplier_id,
            sn_list,
        ),
    )
    return int(cursor.lastrowid)


def list_fifo_receivable_quotes(
    conn: sqlite3.Connection,
    customer_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, quote_price_cents, quote_quantity, received_amount_cents "
            "FROM quotes WHERE customer_id=? AND deleted_at IS NULL "
            "AND status='已出库' "
            "AND status IN ('待确认','已报价','已出库') "
            "AND quote_price_cents*quote_quantity>received_amount_cents "
            "ORDER BY quote_date,id",
            (customer_id,),
        ).fetchall()
    ]


def add_quote_received_amount(
    conn: sqlite3.Connection,
    quote_id: int,
    amount_cents: int,
) -> None:
    conn.execute(
        "UPDATE quotes SET received_amount_cents=received_amount_cents+? WHERE id=?",
        (amount_cents, quote_id),
    )


def payment_has_reversal(conn: sqlite3.Connection, payment_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM payments WHERE reversal_of_id=?",
            (payment_id,),
        ).fetchone()
        is not None
    )


def list_payment_allocations(
    conn: sqlite3.Connection,
    payment_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT quote_id, amount_cents FROM payment_allocations WHERE payment_id=?",
            (payment_id,),
        ).fetchall()
    ]


def adjust_supplier_balance(
    conn: sqlite3.Connection,
    supplier_id: int,
    delta_cents: int,
) -> None:
    conn.execute(
        "UPDATE suppliers SET balance_cents=balance_cents+? WHERE id=?",
        (delta_cents, supplier_id),
    )


def adjust_customer_balance(
    conn: sqlite3.Connection,
    customer_id: int,
    delta_cents: int,
) -> None:
    conn.execute(
        "UPDATE customers SET balance_cents=balance_cents+? WHERE id=?",
        (delta_cents, customer_id),
    )


def insert_customer(
    conn: sqlite3.Connection,
    *,
    name: str,
    wechat: str = "",
    qq: str = "",
    phone: str = "",
    note: str = "",
    default_tax_rate: float | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO customers(name,wechat,qq,phone,note,default_tax_rate) "
        "VALUES (?,?,?,?,?,?)",
        (name, wechat, qq, phone, note, default_tax_rate),
    )
    return int(cursor.lastrowid)


def update_customer(
    conn: sqlite3.Connection,
    customer_id: int,
    *,
    name: str,
    wechat: str = "",
    qq: str = "",
    phone: str = "",
    note: str = "",
    default_tax_rate: float | None = None,
) -> None:
    conn.execute(
        "UPDATE customers SET name=?,wechat=?,qq=?,phone=?,note=?,default_tax_rate=? "
        "WHERE id=?",
        (name, wechat, qq, phone, note, default_tax_rate, customer_id),
    )


def insert_supplier(
    conn: sqlite3.Connection,
    *,
    name: str,
    wechat: str = "",
    qq: str = "",
    phone: str = "",
    note: str = "",
) -> int:
    cursor = conn.execute(
        "INSERT INTO suppliers(name,wechat,qq,phone,note) VALUES (?,?,?,?,?)",
        (name, wechat, qq, phone, note),
    )
    return int(cursor.lastrowid)


def update_supplier(
    conn: sqlite3.Connection,
    supplier_id: int,
    *,
    name: str,
    wechat: str = "",
    qq: str = "",
    phone: str = "",
    note: str = "",
) -> None:
    conn.execute(
        "UPDATE suppliers SET name=?,wechat=?,qq=?,phone=?,note=? WHERE id=?",
        (name, wechat, qq, phone, note, supplier_id),
    )


def log_operation(
    conn: sqlite3.Connection,
    operation: str,
    table_name: str,
    record_id: int | None,
    description: str = "",
) -> None:
    conn.execute(
        "INSERT INTO operation_logs(operation,table_name,record_id,description) "
        "VALUES (?,?,?,?)",
        (operation, table_name, record_id, description),
    )


def audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int | None,
    action: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    conn.execute(
        "INSERT INTO audit_events "
        "(entity_type, entity_id, action, before_json, after_json, reason) "
        "VALUES (?,?,?,?,?,?)",
        (
            entity_type,
            entity_id,
            action,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            reason,
        ),
    )


def soft_delete(
    conn: sqlite3.Connection,
    table: str,
    record_id: int,
    reason: str,
) -> bool:
    allowed = {"products", "batches", "customers", "suppliers", "quotes"}
    if table not in allowed:
        raise ValueError(f"不支持软删除表: {table}")
    before = row_dict(conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (record_id,)).fetchone())
    if not before or before.get("deleted_at"):
        return False
    deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        f'UPDATE "{table}" SET deleted_at=?, deleted_reason=? WHERE id=?',
        (deleted_at, reason, record_id),
    )
    after = dict(before)
    after.update(deleted_at=deleted_at, deleted_reason=reason)
    audit(conn, table, record_id, "soft_delete", before=before, after=after, reason=reason)
    return True


def insert_payment(
    conn: sqlite3.Connection,
    *,
    quote_id: int | None = None,
    customer_id: int | None = None,
    supplier_id: int | None = None,
    pay_type: str,
    amount_cents: int,
    pay_date: str,
    method: str,
    remark: str,
    account_id: int | None = None,
    entry_kind: str = "payment",
    reversal_of_id: int | None = None,
    supersedes_id: int | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO payments "
        "(quote_id, customer_id, supplier_id, type, amount_cents, "
        "entry_kind, reversal_of_id, supersedes_id, pay_date, method, account_id, remark) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            quote_id,
            customer_id,
            supplier_id,
            pay_type,
            amount_cents,
            entry_kind,
            reversal_of_id,
            supersedes_id,
            pay_date,
            method,
            account_id,
            remark,
        ),
    )
    return int(cursor.lastrowid)


def add_allocation(
    conn: sqlite3.Connection,
    payment_id: int,
    quote_id: int,
    amount_cents: int,
) -> None:
    conn.execute(
        "INSERT INTO payment_allocations(payment_id, quote_id, amount_cents) "
        "VALUES (?,?,?)",
        (payment_id, quote_id, amount_cents),
    )


def sync_quote_payment_state(conn: sqlite3.Connection, quote_id: int) -> None:
    row = conn.execute(
        "SELECT quote_price_cents, quote_quantity, received_amount_cents, "
        "status, sn_list FROM quotes WHERE id=?",
        (quote_id,),
    ).fetchone()
    if not row:
        return
    total = row["quote_price_cents"] * row["quote_quantity"]
    received = row["received_amount_cents"]
    paid = "是" if received >= total else "否"
    status = row["status"]
    if received >= total:
        status = "已收款"
    elif status == "已收款":
        status = "已出库" if row["sn_list"] else "待确认"
    conn.execute(
        "UPDATE quotes SET paid=?, status=? WHERE id=?",
        (paid, status, quote_id),
    )

