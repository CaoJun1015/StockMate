"""Read-only application queries."""

from __future__ import annotations

from src.models.connection import connect
from src.models.repositories import cents_to_yuan


def get_receivables(db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.wechat, c.phone,
                   COALESCE(SUM(
                       q.quote_price_cents * q.quote_quantity - q.received_amount_cents
                   ), 0) AS debt_cents,
                   COUNT(q.id) AS order_count
            FROM customers c
            LEFT JOIN quotes q ON q.customer_id=c.id
                AND q.deleted_at IS NULL
                AND q.status IN ('已报价','已出库')
            WHERE c.deleted_at IS NULL
            GROUP BY c.id
            HAVING debt_cents > 0
            ORDER BY debt_cents DESC
        """).fetchall()
        return [
            {
                **dict(row),
                "debt": cents_to_yuan(row["debt_cents"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_payables(db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT s.id, s.name, s.wechat, s.phone,
                   COALESCE(SUM(
                       CASE WHEN b.deleted_at IS NULL
                            THEN b.purchase_price_cents * b.quantity ELSE 0 END
                   ), 0) -
                   COALESCE((
                       SELECT SUM(
                           CASE WHEN p.entry_kind='reversal'
                                THEN -p.amount_cents ELSE p.amount_cents END
                       )
                       FROM payments p
                       WHERE p.supplier_id=s.id AND p.type='payable'
                   ), 0) AS debt_cents,
                   SUM(CASE WHEN b.deleted_at IS NULL THEN 1 ELSE 0 END) AS batch_count
            FROM suppliers s
            LEFT JOIN batches b ON b.supplier_id=s.id
            WHERE s.deleted_at IS NULL
            GROUP BY s.id
            HAVING debt_cents > 0
            ORDER BY debt_cents DESC
        """).fetchall()
        return [{**dict(row), "debt": cents_to_yuan(row["debt_cents"])} for row in rows]
    finally:
        conn.close()


def get_payment_flow(
    pay_type=None,
    customer_id=None,
    supplier_id=None,
    date_from=None,
    date_to=None,
    db_path=None,
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []
    for value, condition in (
        (pay_type, "p.type=?"),
        (customer_id, "p.customer_id=?"),
        (supplier_id, "p.supplier_id=?"),
        (date_from, "p.pay_date>=?"),
        (date_to, "p.pay_date<=?"),
    ):
        if value is not None:
            conditions.append(condition)
            params.append(value)
    where = " AND ".join(conditions) if conditions else "1=1"
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            f"""
            SELECT p.*, c.name AS customer_name, s.name AS supplier_name,
                   CASE
                     WHEN p.entry_kind='reversal' THEN '冲销'
                     WHEN EXISTS(
                         SELECT 1 FROM payments r WHERE r.reversal_of_id=p.id
                     ) THEN '已冲销'
                     WHEN p.supersedes_id IS NOT NULL THEN '更正'
                     ELSE '正常'
                   END AS ledger_status
            FROM payments p
            LEFT JOIN customers c ON p.customer_id=c.id
            LEFT JOIN suppliers s ON p.supplier_id=s.id
            WHERE {where}
            ORDER BY p.pay_date DESC, p.id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_audit_events(entity_type=None, entity_id=None, limit=200, db_path=None) -> list[dict]:
    conditions = []
    params: list[object] = []
    if entity_type:
        conditions.append("entity_type=?")
        params.append(entity_type)
    if entity_id is not None:
        conditions.append("entity_id=?")
        params.append(entity_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM audit_events WHERE {where} ORDER BY id DESC LIMIT ?",
                params,
            )
        ]
    finally:
        conn.close()

