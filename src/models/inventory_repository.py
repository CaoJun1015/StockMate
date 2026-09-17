"""Write/read helpers for the immutable inventory subledger."""

from __future__ import annotations

import sqlite3
from typing import Any


def normalize_sn_list(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        raw = value.replace("\r", "\n").replace(",", "\n").replace(" ", "\n")
        values = raw.splitlines()
    else:
        values = value
    return [str(item).strip() for item in values if str(item).strip()]


def serialize_sn_list(value: str | list[str] | tuple[str, ...] | None) -> str:
    return ",".join(normalize_sn_list(value))


def insert_inventory_movement(
    conn: sqlite3.Connection,
    *,
    movement_date: str,
    movement_type: str,
    product_id: int,
    batch_id: int,
    quantity_delta: int,
    unit_cost_cents: int,
    source_type: str,
    source_id: int | str | None,
    idempotency_key: str,
    shipment_allocation_id: int | None = None,
    ledger_entry_id: int | None = None,
    sn_list: str | list[str] | None = None,
) -> int:
    if quantity_delta == 0:
        raise ValueError("inventory movement quantity cannot be zero")
    cursor = conn.execute(
        """
        INSERT INTO inventory_movements(
            movement_date,movement_type,product_id,batch_id,quantity_delta,
            unit_cost_cents,total_cost_cents,source_type,source_id,
            shipment_allocation_id,ledger_entry_id,sn_list,idempotency_key
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            movement_date, movement_type, product_id, batch_id, quantity_delta,
            unit_cost_cents, abs(quantity_delta) * unit_cost_cents, source_type,
            str(source_id) if source_id is not None else None,
            shipment_allocation_id, ledger_entry_id, serialize_sn_list(sn_list),
            idempotency_key,
        ),
    )
    return int(cursor.lastrowid)


def insert_shipment_allocation(
    conn: sqlite3.Connection,
    *,
    shipment_snapshot_id: int,
    batch_id: int,
    quantity: int,
    unit_cost_cents: int,
    sn_list: str | list[str] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO shipment_allocations(
            shipment_snapshot_id,batch_id,quantity,unit_cost_cents,cost_cents,sn_list
        ) VALUES (?,?,?,?,?,?)
        """,
        (shipment_snapshot_id, batch_id, quantity, unit_cost_cents,
         quantity * unit_cost_cents, serialize_sn_list(sn_list)),
    )
    return int(cursor.lastrowid)


def inventory_balance(conn: sqlite3.Connection, batch_id: int) -> int:
    return int(conn.execute(
        "SELECT COALESCE(SUM(quantity_delta),0) FROM inventory_movements WHERE batch_id=?",
        (batch_id,),
    ).fetchone()[0])


def apply_inventory_delta(
    conn: sqlite3.Connection, *, batch_id: int, quantity_delta: int
) -> bool:
    row = conn.execute(
        "SELECT remaining FROM batches WHERE id=? AND deleted_at IS NULL", (batch_id,)
    ).fetchone()
    if row is None:
        return False
    movement_remaining = inventory_balance(conn, batch_id)
    if int(row["remaining"]) != movement_remaining:
        return False
    if quantity_delta < 0:
        if movement_remaining < -quantity_delta:
            return False
        cursor = conn.execute(
            "UPDATE batches SET remaining=remaining+? "
            "WHERE id=? AND deleted_at IS NULL AND remaining>=?",
            (quantity_delta, batch_id, -quantity_delta),
        )
    else:
        cursor = conn.execute(
            "UPDATE batches SET remaining=remaining+? WHERE id=? AND deleted_at IS NULL",
            (quantity_delta, batch_id),
        )
    return cursor.rowcount == 1


def list_shipment_allocations(
    conn: sqlite3.Connection, *, quote_id: int | None = None,
    snapshot_id: int | None = None
) -> list[dict[str, Any]]:
    if snapshot_id is not None:
        where, value = "sa.shipment_snapshot_id=?", snapshot_id
    elif quote_id is not None:
        where, value = "ss.quote_id=?", quote_id
    else:
        raise ValueError("quote_id or snapshot_id is required")
    return [dict(row) for row in conn.execute(
        f"""
        SELECT sa.*,ss.quote_id,b.product_id,b.date AS batch_date,
               b.remaining,b.quantity AS batch_quantity,
               COALESCE((SELECT SUM(ra.quantity)
                         FROM sales_return_allocations ra
                         WHERE ra.shipment_allocation_id=sa.id),0) AS returned_quantity
        FROM shipment_allocations sa
        JOIN shipment_snapshots ss ON ss.id=sa.shipment_snapshot_id
        JOIN batches b ON b.id=sa.batch_id
        WHERE {where} ORDER BY sa.id
        """, (value,)
    ).fetchall()]


def returned_quantity_for_allocation(
    conn: sqlite3.Connection, shipment_allocation_id: int
) -> int:
    return int(conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM sales_return_allocations "
        "WHERE shipment_allocation_id=?", (shipment_allocation_id,),
    ).fetchone()[0])


def allocated_return_quantity_for_quote(conn, quote_id: int) -> int:
    return int(conn.execute(
        "SELECT COALESCE(SUM(ra.quantity),0) FROM sales_return_allocations ra "
        "JOIN sales_returns sr ON sr.id=ra.sales_return_id WHERE sr.quote_id=?",
        (quote_id,),
    ).fetchone()[0])


def returned_sns_for_allocation(
    conn: sqlite3.Connection, shipment_allocation_id: int
) -> set[str]:
    return {sn for row in conn.execute(
        "SELECT sn_list FROM sales_return_allocations WHERE shipment_allocation_id=?",
        (shipment_allocation_id,),
    ) for sn in normalize_sn_list(row[0])}


def insert_sales_return_allocation(conn, return_id, allocation_id, quantity,
                                   restock_quantity, sn_list):
    conn.execute(
        "INSERT INTO sales_return_allocations "
        "(sales_return_id,shipment_allocation_id,quantity,restock_quantity,sn_list) "
        "VALUES (?,?,?,?,?)",
        (return_id, allocation_id, quantity, restock_quantity, sn_list),
    )


def _active_shipped_sn_counts(conn) -> dict[str, int]:
    # A return releases only its original shipment. A later sale of that SN
    # is a separate allocation and must remain occupied.
    returned_by_allocation: dict[int, set[str]] = {}
    for row in conn.execute(
        "SELECT shipment_allocation_id,restock_quantity,sn_list FROM sales_return_allocations "
        "WHERE restock_quantity>0"
    ):
        returned_sns = normalize_sn_list(row["sn_list"])
        if len(returned_sns) == row["restock_quantity"]:
            returned_by_allocation.setdefault(row["shipment_allocation_id"], set()).update(
                returned_sns
            )
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT id,sn_list FROM shipment_allocations"):
        returned = returned_by_allocation.get(row["id"], set())
        for sn in normalize_sn_list(row["sn_list"]):
            if sn not in returned:
                counts[sn] = counts.get(sn, 0) + 1
    return counts


def shipped_sns(conn: sqlite3.Connection) -> set[str]:
    return set(_active_shipped_sn_counts(conn))


def duplicate_active_shipped_sns(conn: sqlite3.Connection) -> list[str]:
    return sorted(sn for sn, count in _active_shipped_sn_counts(conn).items() if count > 1)


def list_inventory_movements(
    conn: sqlite3.Connection, *, batch_id: int | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM inventory_movements"
    params: tuple[Any, ...] = ()
    if batch_id is not None:
        sql += " WHERE batch_id=?"
        params = (batch_id,)
    sql += " ORDER BY movement_date,id"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
