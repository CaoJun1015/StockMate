"""Write-oriented repository helpers.

Repositories never open, commit, or close connections. Transaction ownership
belongs to the application services.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def yuan_to_cents(value: int | float | str | Decimal) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_yuan(value: int | None) -> float:
    return float(Decimal(value or 0) / Decimal(100))


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


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
    entry_kind: str = "payment",
    reversal_of_id: int | None = None,
    supersedes_id: int | None = None,
) -> int:
    amount = cents_to_yuan(amount_cents)
    cursor = conn.execute(
        "INSERT INTO payments "
        "(quote_id, customer_id, supplier_id, type, amount, amount_cents, "
        "entry_kind, reversal_of_id, supersedes_id, pay_date, method, remark) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            quote_id,
            customer_id,
            supplier_id,
            pay_type,
            amount,
            amount_cents,
            entry_kind,
            reversal_of_id,
            supersedes_id,
            pay_date,
            method,
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
        "UPDATE quotes SET received_amount=?, paid=?, status=? WHERE id=?",
        (cents_to_yuan(received), paid, status, quote_id),
    )

