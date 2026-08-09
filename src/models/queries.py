"""Read-only application queries."""

from __future__ import annotations

import json

from src.models.connection import connect
from src.utils.tax import calc_tax_adjusted_profit_cents


BACKUP_TABLES = (
    "products",
    "suppliers",
    "batches",
    "customers",
    "quotes",
    "payments",
    "payment_allocations",
    "finance_settings",
    "ledger_accounts",
    "finance_categories",
    "ledger_entries",
    "ledger_lines",
    "shipment_snapshots",
    "supplier_payment_allocations",
    "sales_returns",
    "purchase_returns",
    "audit_events",
)


def export_backup_data(db_path=None) -> dict[str, list[dict]]:
    """Return canonical schema rows for JSON backup without derived yuan fields."""
    conn = connect(db_path, read_only=True)
    try:
        return {
            table: [
                dict(row)
                for row in conn.execute(
                    f'SELECT * FROM "{table}" ORDER BY id'
                ).fetchall()
            ]
            for table in BACKUP_TABLES
        }
    finally:
        conn.close()


def list_products(keyword: str = "", db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        params: list[object] = []
        where = "p.deleted_at IS NULL"
        if keyword:
            value = f"%{keyword}%"
            where += (
                " AND (p.series LIKE ? OR p.cpu LIKE ? OR p.ram LIKE ? "
                "OR p.storage LIKE ? OR p.gpu LIKE ? OR p.screen LIKE ? OR p.note LIKE ?)"
            )
            params.extend([value] * 7)
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.id,p.series,p.cpu,p.ram,p.storage,p.gpu,p.screen,p.note,
                       COALESCE((
                           SELECT SUM(b.remaining) FROM batches b
                           WHERE b.product_id=p.id AND b.deleted_at IS NULL
                       ),0) AS total_remaining
                FROM products p
                WHERE """
                + where
                + " ORDER BY p.series,p.cpu",
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def list_batches(product_id: int, db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT b.id,b.product_id,b.purchase_price_cents,
                       b.quantity,b.remaining,b.date,b.remark,
                       CASE WHEN s.deleted_at IS NULL THEN b.supplier_id END AS supplier_id,
                       b.sn_list
                FROM batches b
                LEFT JOIN suppliers s ON b.supplier_id=s.id
                WHERE b.product_id=? AND b.deleted_at IS NULL
                ORDER BY b.date DESC,b.id DESC
                """,
                (product_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def list_customers(keyword: str = "", db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        params: list[object] = []
        where = "deleted_at IS NULL"
        if keyword:
            value = f"%{keyword}%"
            where += " AND (name LIKE ? OR wechat LIKE ? OR qq LIKE ? OR phone LIKE ? OR note LIKE ?)"
            params.extend([value] * 5)
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name,wechat,qq,phone,note,default_tax_rate "
                f"FROM customers WHERE {where} ORDER BY name",
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def get_customer(customer_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT id,name,wechat,qq,phone,note,default_tax_rate "
            "FROM customers WHERE id=? AND deleted_at IS NULL",
            (customer_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_customer_reference_counts(customer_id: int, db_path=None) -> dict[str, int]:
    conn = connect(db_path, read_only=True)
    try:
        return {
            "quotes": conn.execute(
                "SELECT COUNT(*) FROM quotes WHERE customer_id=?",
                (customer_id,),
            ).fetchone()[0],
            "payments": conn.execute(
                "SELECT COUNT(*) FROM payments WHERE customer_id=?",
                (customer_id,),
            ).fetchone()[0],
        }
    finally:
        conn.close()


def list_suppliers(keyword: str = "", db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        params: list[object] = []
        where = "deleted_at IS NULL"
        if keyword:
            value = f"%{keyword}%"
            where += " AND (name LIKE ? OR wechat LIKE ? OR qq LIKE ? OR phone LIKE ? OR note LIKE ?)"
            params.extend([value] * 5)
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name,wechat,qq,phone,note "
                f"FROM suppliers WHERE {where} ORDER BY name",
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def get_supplier(supplier_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT id,name,wechat,qq,phone,note "
            "FROM suppliers WHERE id=? AND deleted_at IS NULL",
            (supplier_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_supplier_purchase_history(supplier_id: int, db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT b.date,p.series,p.cpu,b.quantity,
                       b.purchase_price_cents,b.remark
                FROM batches b
                JOIN products p ON b.product_id=p.id
                WHERE b.supplier_id=? AND b.deleted_at IS NULL
                ORDER BY b.date DESC,b.id DESC
                """,
                (supplier_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_supplier_reference_counts(supplier_id: int, db_path=None) -> dict[str, int]:
    conn = connect(db_path, read_only=True)
    try:
        return {
            "batches": conn.execute(
                "SELECT COUNT(*) FROM batches WHERE supplier_id=?",
                (supplier_id,),
            ).fetchone()[0],
            "payments": conn.execute(
                "SELECT COUNT(*) FROM payments WHERE supplier_id=?",
                (supplier_id,),
            ).fetchone()[0],
        }
    finally:
        conn.close()


def get_batch_detail(batch_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT id,product_id,purchase_price_cents,quantity,"
            "remaining,date,remark,supplier_id,sn_list "
            "FROM batches WHERE id=? AND deleted_at IS NULL",
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_batch_remaining(batch_id: int, db_path=None) -> int:
    batch = get_batch_detail(batch_id, db_path)
    return int(batch["remaining"]) if batch else 0


def get_customer_default_tax_rate(customer_id: int, db_path=None) -> float | None:
    customer = get_customer(customer_id, db_path)
    return customer["default_tax_rate"] if customer else None


def get_payment_detail(payment_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            """
            SELECT p.id,p.quote_id,p.customer_id,p.supplier_id,p.type,
                   p.amount_cents,p.entry_kind,p.reversal_of_id,p.supersedes_id,
                   p.pay_date,p.method,p.remark,p.created_at,
                   c.name AS customer_name,s.name AS supplier_name,
                   q.quote_price_cents,q.quote_quantity,q.received_amount_cents
            FROM payments p
            LEFT JOIN customers c ON p.customer_id=c.id
            LEFT JOIN suppliers s ON p.supplier_id=s.id
            LEFT JOIN quotes q ON p.quote_id=q.id
            WHERE p.id=?
            """,
            (payment_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_operation_logs(limit: int = 100, db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_quote_detail(quote_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            """
            SELECT q.id,q.batch_id,q.customer_id,q.quote_price_cents,
                   q.quote_quantity,q.quote_date,q.remark,q.paid,q.status,
                   q.received_amount_cents,q.sn_list,
                   q.tax_rate,q.purchase_tax_inclusive,q.quote_tax_inclusive,
                   p.series,p.cpu,p.ram,p.storage,p.gpu,
                   b.purchase_price_cents,b.sn_list AS batch_sn_list,
                   c.name AS customer_name
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            JOIN products p ON b.product_id=p.id
            LEFT JOIN customers c ON q.customer_id=c.id
            WHERE q.id=? AND q.deleted_at IS NULL
              AND b.deleted_at IS NULL AND p.deleted_at IS NULL
              AND (c.id IS NULL OR c.deleted_at IS NULL)
            """,
            (quote_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def search_quotes(
    keyword: str = "",
    date_from: str = "",
    date_to: str = "",
    customer_id: int | None = None,
    db_path=None,
) -> list[dict]:
    conditions = [
        "q.deleted_at IS NULL",
        "b.deleted_at IS NULL",
        "p.deleted_at IS NULL",
        "(c.id IS NULL OR c.deleted_at IS NULL)",
    ]
    params: list[object] = []
    if keyword:
        value = f"%{keyword}%"
        conditions.append(
            "(p.series LIKE ? OR p.cpu LIKE ? OR p.ram LIKE ? OR p.storage LIKE ? "
            "OR p.gpu LIKE ? OR c.name LIKE ? OR b.remark LIKE ? OR q.remark LIKE ? "
            "OR b.sn_list LIKE ? OR s.name LIKE ?)"
        )
        params.extend([value] * 10)
    if date_from:
        conditions.append("q.quote_date>=?")
        params.append(date_from)
    if date_to:
        conditions.append("q.quote_date<=?")
        params.append(date_to)
    if customer_id is not None:
        conditions.append("q.customer_id=?")
        params.append(customer_id)
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT q.id,q.quote_price_cents,q.quote_quantity,
                       q.quote_date,q.remark,q.paid,q.status,
                       q.received_amount_cents,q.sn_list,q.tax_rate,
                       q.purchase_tax_inclusive,q.quote_tax_inclusive,
                       p.series,p.cpu,p.ram,p.storage,p.gpu,p.screen,p.note,
                       b.purchase_price_cents,b.remark AS batch_remark,
                       b.id AS batch_id,b.sn_list AS batch_sn_list,
                       c.name AS customer_name,c.id AS customer_id,
                       s.name AS supplier_name
                FROM quotes q
                JOIN batches b ON q.batch_id=b.id
                JOIN products p ON b.product_id=p.id
                LEFT JOIN customers c ON q.customer_id=c.id
                LEFT JOIN suppliers s ON b.supplier_id=s.id
                WHERE {" AND ".join(conditions)}
                ORDER BY q.quote_date DESC,q.id DESC
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def export_quotes(
    date_from: str = "",
    date_to: str = "",
    customer_id: int | None = None,
    db_path=None,
) -> list[dict]:
    return search_quotes(
        date_from=date_from,
        date_to=date_to,
        customer_id=customer_id,
        db_path=db_path,
    )


def get_customer_statement(
    customer_id: int,
    date_from: str = "",
    date_to: str = "",
    db_path=None,
) -> list[dict]:
    conditions = ["q.customer_id=?", "q.deleted_at IS NULL"]
    params: list[object] = [customer_id]
    if date_from:
        conditions.append("q.quote_date>=?")
        params.append(date_from)
    if date_to:
        conditions.append("q.quote_date<=?")
        params.append(date_to)
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT q.id,q.quote_date,q.quote_price_cents,q.quote_quantity,q.status,
                       q.paid,q.received_amount_cents,p.series,p.cpu,p.ram,p.storage,p.gpu,
                       b.purchase_price_cents,b.remark AS batch_remark,q.remark,
                       s.name AS supplier_name
                FROM quotes q
                JOIN batches b ON q.batch_id=b.id
                JOIN products p ON b.product_id=p.id
                LEFT JOIN suppliers s ON b.supplier_id=s.id
                WHERE {" AND ".join(conditions)}
                ORDER BY q.quote_date,q.id
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def get_customer_history(customer_id: int, db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT q.id,q.quote_price_cents,q.quote_quantity,q.quote_date,q.remark,q.paid,
                       p.series,p.cpu,p.ram,p.storage,p.gpu,p.screen,p.note,
                       b.purchase_price_cents,q.tax_rate,q.purchase_tax_inclusive,
                       q.quote_tax_inclusive
                FROM quotes q
                JOIN batches b ON q.batch_id=b.id
                JOIN products p ON b.product_id=p.id
                WHERE q.customer_id=? AND q.deleted_at IS NULL
                ORDER BY q.quote_date DESC,q.id DESC
                """,
                (customer_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_customer_stats(customer_id: int, db_path=None) -> dict:
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT q.quote_price_cents,q.quote_quantity,b.purchase_price_cents,q.tax_rate,
                   q.purchase_tax_inclusive,q.quote_tax_inclusive
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            WHERE q.customer_id=? AND q.deleted_at IS NULL AND q.status!='已取消'
            """,
            (customer_id,),
        ).fetchall()
    finally:
        conn.close()
    total_amount_cents = 0
    total_profit_cents = 0
    for row in rows:
        quantity = row["quote_quantity"] or 1
        quote_price_cents = row["quote_price_cents"] or 0
        purchase_price_cents = row["purchase_price_cents"] or 0
        total_amount_cents += quote_price_cents * quantity
        total_profit_cents += calc_tax_adjusted_profit_cents(
            purchase_price_cents,
            quote_price_cents,
            quantity,
            row["tax_rate"],
            bool(row["purchase_tax_inclusive"]),
            bool(row["quote_tax_inclusive"]),
        )
    return {
        "total_quotes": len(rows),
        "total_amount_cents": total_amount_cents,
        "total_profit_cents": total_profit_cents,
    }


def get_receivables(db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.wechat, c.phone,
                   COALESCE(SUM(
                       q.quote_price_cents * q.quote_quantity
                       - q.received_amount_cents
                       - COALESCE((
                           SELECT SUM(sr.revenue_cents)
                           FROM sales_returns sr WHERE sr.quote_id=q.id
                         ), 0)
                   ), 0) AS debt_cents,
                   COUNT(*) AS order_count
            FROM customers c
            JOIN quotes q ON q.customer_id = c.id
            WHERE c.deleted_at IS NULL
              AND q.deleted_at IS NULL
              AND q.status = '已出库'
            GROUP BY c.id, c.name, c.wechat, c.phone
            HAVING debt_cents > 0
            ORDER BY debt_cents DESC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_payables(db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute("""
            SELECT s.id, s.name, s.wechat, s.phone,
                   s.balance_cents AS debt_cents,
                   (SELECT COUNT(*) FROM batches b
                      WHERE b.supplier_id=s.id
                        AND b.deleted_at IS NULL) AS batch_count
            FROM suppliers s
            WHERE s.deleted_at IS NULL
              AND s.balance_cents > 0
            ORDER BY s.balance_cents DESC
        """).fetchall()
        return [dict(row) for row in rows]
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


FINANCIAL_ENTITY_TYPES = ("batches", "quotes", "payments")


def _json_object(value) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def list_financial_audit_events(
    *,
    date_from=None,
    date_to=None,
    category=None,
    action=None,
    keyword="",
    entity_id=None,
    limit=500,
    db_path=None,
) -> list[dict]:
    conditions = [
        "e.entity_type IN ('batches','quotes','payments')",
    ]
    params: list[object] = []
    if date_from:
        conditions.append("date(e.created_at)>=date(?)")
        params.append(date_from)
    if date_to:
        conditions.append("date(e.created_at)<=date(?)")
        params.append(date_to)
    if action:
        conditions.append("e.action=?")
        params.append(action)
    if entity_id is not None:
        conditions.append("e.entity_id=?")
        params.append(entity_id)
    if keyword:
        value = f"%{keyword}%"
        conditions.append(
            "(COALESCE(pc.name,qc.name,'') LIKE ? "
            "OR COALESCE(ps.name,bs.name,'') LIKE ? "
            "OR CAST(e.entity_id AS TEXT) LIKE ?)"
        )
        params.extend((value, value, value))
    params.append(limit)
    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            f"""
            SELECT e.*,pay.type AS payment_type,pay.entry_kind,pay.amount_cents,
                   pay.reversal_of_id,pay.supersedes_id,pay.quote_id AS payment_quote_id,
                   COALESCE(pc.name,qc.name) AS customer_name,
                   COALESCE(ps.name,bs.name) AS supplier_name,
                   q.id AS quote_id,q.quote_price_cents,q.quote_quantity,
                   b.id AS batch_id,b.purchase_price_cents,b.quantity
            FROM audit_events e
            LEFT JOIN payments pay
              ON e.entity_type='payments' AND pay.id=e.entity_id
            LEFT JOIN quotes q
              ON q.id=CASE
                  WHEN e.entity_type='quotes' THEN e.entity_id
                  WHEN e.entity_type='payments' THEN pay.quote_id
                  END
            LEFT JOIN batches b
              ON b.id=CASE
                  WHEN e.entity_type='batches' THEN e.entity_id
                  WHEN q.id IS NOT NULL THEN q.batch_id
                  END
            LEFT JOIN customers pc ON pc.id=pay.customer_id
            LEFT JOIN customers qc ON qc.id=q.customer_id
            LEFT JOIN suppliers ps ON ps.id=pay.supplier_id
            LEFT JOIN suppliers bs ON bs.id=b.supplier_id
            WHERE {" AND ".join(conditions)}
            ORDER BY e.created_at DESC,e.id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    result = []
    for raw in rows:
        row = dict(raw)
        before = _json_object(row["before_json"])
        after = _json_object(row["after_json"])
        amount_cents = row.get("amount_cents")
        if amount_cents is None:
            amount_cents = after.get("amount_cents")
        if amount_cents is None:
            amount_cents = after.get("total_cents")
        if amount_cents is None:
            amount_cents = before.get("amount_cents")
        if row["entity_type"] == "payments":
            event_category = (
                "客户收款" if row.get("payment_type") == "receivable" else "供应商付款"
            )
        elif row["entity_type"] == "quotes":
            event_category = "报价/应收"
        else:
            event_category = "入库/应付"
        if category and event_category != category:
            continue
        object_name = row.get("customer_name") or row.get("supplier_name") or ""
        result.append(
            {
                **row,
                "before": before,
                "after": after,
                "amount_cents": int(amount_cents or 0),
                "category": event_category,
                "object_name": object_name,
            }
        )
    return result


def get_financial_audit_detail(event_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        event = conn.execute(
            "SELECT * FROM audit_events WHERE id=?", (event_id,)
        ).fetchone()
        if not event or event["entity_type"] not in FINANCIAL_ENTITY_TYPES:
            return None
        event_dict = dict(event)
        before = _json_object(event["before_json"])
        after = _json_object(event["after_json"])
        detail = {**event_dict, "before": before, "after": after}

        payment_ids: set[int] = set()
        if event["entity_type"] == "payments" and event["entity_id"] is not None:
            payment_ids.add(event["entity_id"])
            for value in (
                before.get("id"),
                before.get("reversal_of_id"),
                before.get("supersedes_id"),
                after.get("reversal_id"),
                after.get("supersedes_id"),
            ):
                if isinstance(value, int):
                    payment_ids.add(value)
            while payment_ids:
                marks = ",".join("?" for _ in payment_ids)
                values = tuple(sorted(payment_ids))
                linked = conn.execute(
                    f"""
                    SELECT id,reversal_of_id,supersedes_id FROM payments
                    WHERE id IN ({marks})
                       OR reversal_of_id IN ({marks})
                       OR supersedes_id IN ({marks})
                    """,
                    values * 3,
                ).fetchall()
                expanded = set(payment_ids)
                for row in linked:
                    expanded.add(row["id"])
                    if row["reversal_of_id"] is not None:
                        expanded.add(row["reversal_of_id"])
                    if row["supersedes_id"] is not None:
                        expanded.add(row["supersedes_id"])
                if expanded == payment_ids:
                    break
                payment_ids = expanded

        if payment_ids:
            marks = ",".join("?" for _ in payment_ids)
            payments = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT p.*,c.name AS customer_name,s.name AS supplier_name
                    FROM payments p
                    LEFT JOIN customers c ON p.customer_id=c.id
                    LEFT JOIN suppliers s ON p.supplier_id=s.id
                    WHERE p.id IN ({marks}) ORDER BY p.id
                    """,
                    tuple(sorted(payment_ids)),
                ).fetchall()
            ]
            allocations = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT a.*,p.series,c.name AS customer_name
                    FROM payment_allocations a
                    JOIN quotes q ON a.quote_id=q.id
                    JOIN batches b ON q.batch_id=b.id
                    JOIN products p ON b.product_id=p.id
                    LEFT JOIN customers c ON q.customer_id=c.id
                    WHERE a.payment_id IN ({marks})
                    ORDER BY a.payment_id,a.id
                    """,
                    tuple(sorted(payment_ids)),
                ).fetchall()
            ]
        else:
            payments, allocations = [], []
        detail["payments"] = payments
        detail["allocations"] = allocations
        return detail
    finally:
        conn.close()


def get_quote_assist_history(
    *,
    series="",
    cpu="",
    ram="",
    storage="",
    gpu="",
    db_path=None,
) -> dict | None:
    conditions: list[str] = []
    params: list[object] = []
    for value, column in (
        (series, "p.series"),
        (cpu, "p.cpu"),
        (ram, "p.ram"),
        (storage, "p.storage"),
        (gpu, "p.gpu"),
    ):
        if value:
            conditions.append(f"{column} LIKE ?")
            params.append(f"%{value}%")
    if not conditions:
        return None
    where = " AND ".join(conditions)
    conn = connect(db_path, read_only=True)
    try:
        stats = conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   MIN(q.quote_price_cents) AS min_price_cents,
                   MAX(q.quote_price_cents) AS max_price_cents,
                   CAST(ROUND(AVG(q.quote_price_cents)) AS INTEGER) AS avg_price_cents,
                   MIN(b.purchase_price_cents) AS min_cost_cents,
                   CAST(ROUND(AVG(b.purchase_price_cents)) AS INTEGER) AS avg_cost_cents
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            JOIN products p ON b.product_id=p.id
            WHERE {where} AND q.deleted_at IS NULL
              AND q.status IN ('已报价','已出库','已收款')
            """,
            params,
        ).fetchone()
        if not stats or not stats["total"]:
            return None
        recent = conn.execute(
            f"""
            SELECT q.quote_price_cents,q.quote_quantity,q.quote_date,q.status,
                   b.purchase_price_cents,c.name AS customer_name
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            JOIN products p ON b.product_id=p.id
            LEFT JOIN customers c ON q.customer_id=c.id
            WHERE {where} AND q.deleted_at IS NULL
            ORDER BY q.quote_date DESC,q.id DESC LIMIT 5
            """,
            params,
        ).fetchall()
        return {**dict(stats), "recent_quotes": [dict(row) for row in recent]}
    finally:
        conn.close()


def get_customer_price_history_query(
    customer_name: str,
    series: str = "",
    *,
    db_path=None,
) -> list[dict]:
    conditions = ["c.name LIKE ?", "q.deleted_at IS NULL"]
    params: list[object] = [f"%{customer_name}%"]
    if series:
        conditions.append("p.series LIKE ?")
        params.append(f"%{series}%")
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT q.quote_price_cents,q.quote_quantity,q.quote_date,q.status,
                       p.series,p.cpu,p.ram,p.storage
                FROM quotes q
                JOIN batches b ON q.batch_id=b.id
                JOIN products p ON b.product_id=p.id
                LEFT JOIN customers c ON q.customer_id=c.id
                WHERE {" AND ".join(conditions)}
                ORDER BY q.quote_date DESC LIMIT 10
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def list_stale_quotes(status: str, cutoff: str, today: str, db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT q.id,q.quote_date,q.quote_price_cents,q.quote_quantity,q.remark,
                       p.series,p.cpu,p.ram,p.storage,p.gpu,c.name AS customer_name,
                       CAST(julianday(?) - julianday(q.quote_date) AS INTEGER) AS days_ago
                FROM quotes q
                JOIN batches b ON q.batch_id=b.id
                JOIN products p ON b.product_id=p.id
                LEFT JOIN customers c ON q.customer_id=c.id
                WHERE q.status=? AND q.quote_date<=? AND q.deleted_at IS NULL
                ORDER BY q.quote_date
                """,
                (today, status, cutoff),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_latest_price_snapshot(before_date=None, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        if before_date:
            row = conn.execute(
                "SELECT id,import_date,item_count FROM price_snapshots "
                "WHERE import_date<? ORDER BY import_date DESC,id DESC LIMIT 1",
                (before_date,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id,import_date,item_count FROM price_snapshots "
                "ORDER BY import_date DESC,id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        result = {
            "snapshot_id": row["id"],
            "import_date": row["import_date"],
            "item_count": row["item_count"],
        }
        result["items"] = [
            dict(item)
            for item in conn.execute(
                "SELECT id,series,cpu,ram,storage,gpu,note,norm_key "
                "FROM price_snapshot_items WHERE snapshot_id=?",
                (row["id"],),
            ).fetchall()
        ]
        return result
    finally:
        conn.close()


def list_price_snapshots(db_path=None) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,import_date,item_count,created_at FROM price_snapshots "
                "ORDER BY import_date DESC,id DESC"
            ).fetchall()
        ]
    finally:
        conn.close()


def get_monthly_business_data(
    date_from: str,
    date_to: str,
    prev_from: str,
    prev_to: str,
    db_path=None,
) -> dict[str, list[dict]]:
    conn = connect(db_path, read_only=True)
    try:
        sales_sql = """
            SELECT q.quote_price_cents,q.quote_quantity,b.purchase_price_cents,
                   q.tax_rate,q.purchase_tax_inclusive,q.quote_tax_inclusive,
                   q.received_amount_cents,p.series,p.cpu,p.ram,p.storage
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            JOIN products p ON b.product_id=p.id
            WHERE q.quote_date>=? AND q.quote_date<?
              AND q.deleted_at IS NULL
              AND q.status IN ('已报价','已出库','已收款')
        """
        current = [
            dict(row)
            for row in conn.execute(sales_sql, (date_from, date_to)).fetchall()
        ]
        previous = [
            dict(row)
            for row in conn.execute(sales_sql, (prev_from, prev_to)).fetchall()
        ]
        slow_movers = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.series,p.cpu,p.ram,p.storage,SUM(b.remaining) AS stock,
                       MIN(b.date) AS oldest_batch_date,
                       COALESCE(SUM(b.remaining*b.purchase_price_cents),0)
                           AS tied_capital_cents
                FROM batches b
                JOIN products p ON b.product_id=p.id
                WHERE b.remaining>0 AND b.deleted_at IS NULL
                  AND p.deleted_at IS NULL
                  AND p.id NOT IN (
                      SELECT DISTINCT b2.product_id
                      FROM quotes q2 JOIN batches b2 ON q2.batch_id=b2.id
                      WHERE q2.quote_date>=? AND q2.quote_date<?
                        AND q2.deleted_at IS NULL
                  )
                GROUP BY p.series,p.cpu,p.ram,p.storage
                HAVING stock>0
                ORDER BY tied_capital_cents DESC LIMIT 5
                """,
                (date_from, date_to),
            ).fetchall()
        ]
        return {"current": current, "previous": previous, "slow_movers": slow_movers}
    finally:
        conn.close()


def collect_reconciliation_snapshot(db_path=None) -> dict:
    """Collect read-only consistency facts; policy lives in ReconciliationService."""
    conn = connect(db_path, read_only=True)
    try:
        integrity_rows = [
            row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()
        ]
        foreign_key_rows = [
            dict(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
        ]
        inventory_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT b.id,b.quantity,b.remaining,
                       b.quantity-b.remaining AS deducted,
                       COALESCE(SUM(
                           CASE WHEN q.deleted_at IS NULL
                                     AND q.status IN ('已出库','已收款')
                                THEN q.quote_quantity ELSE 0 END
                       ),0) AS shipped
                FROM batches b
                LEFT JOIN quotes q ON q.batch_id=b.id
                WHERE b.deleted_at IS NULL
                GROUP BY b.id
                HAVING b.remaining<0 OR b.remaining>b.quantity
                    OR shipped>b.quantity-b.remaining
                """
            ).fetchall()
        ]
        stock_history = conn.execute(
            """
            SELECT COUNT(*) AS batch_count,
                   COALESCE(SUM((quantity-remaining)-shipped),0) AS units
            FROM (
                SELECT b.id,b.quantity,b.remaining,
                       COALESCE(SUM(
                           CASE WHEN q.deleted_at IS NULL
                                     AND q.status IN ('已出库','已收款')
                                THEN q.quote_quantity ELSE 0 END
                       ),0) AS shipped
                FROM batches b
                LEFT JOIN quotes q ON q.batch_id=b.id
                WHERE b.deleted_at IS NULL
                GROUP BY b.id
                HAVING b.quantity-b.remaining>shipped
            )
            """
        ).fetchone()
        quote_allocation_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT q.id,q.received_amount_cents,
                       COALESCE(SUM(a.amount_cents),0) AS allocated_cents
                FROM quotes q
                LEFT JOIN payment_allocations a ON a.quote_id=q.id
                GROUP BY q.id
                HAVING q.received_amount_cents!=allocated_cents
                """
            ).fetchall()
        ]
        payment_allocation_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.id,p.amount_cents,p.entry_kind,
                       CASE WHEN p.entry_kind='reversal'
                            THEN -p.amount_cents ELSE p.amount_cents END AS expected_cents,
                       COALESCE(SUM(a.amount_cents),0) AS allocated_cents
                FROM payments p
                LEFT JOIN payment_allocations a ON a.payment_id=p.id
                WHERE p.type='receivable'
                GROUP BY p.id
                HAVING
                    (expected_cents>=0
                     AND (allocated_cents<0 OR allocated_cents>expected_cents))
                    OR
                    (expected_cents<0
                     AND (allocated_cents>0 OR allocated_cents<expected_cents))
                """
            ).fetchall()
        ]
        supplier_balance_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.id,s.name,s.balance_cents,
                       COALESCE((
                           SELECT SUM(b.purchase_price_cents*b.quantity)
                           FROM batches b
                           WHERE b.supplier_id=s.id AND b.deleted_at IS NULL
                       ),0) -
                       COALESCE((
                           SELECT SUM(pr.amount_cents)
                           FROM purchase_returns pr
                           WHERE pr.supplier_id=s.id
                       ),0) -
                       COALESCE((
                           SELECT SUM(
                               CASE WHEN p.entry_kind='reversal'
                                    THEN -p.amount_cents ELSE p.amount_cents END
                           )
                           FROM payments p
                           WHERE p.supplier_id=s.id AND p.type='payable'
                       ),0) +
                       COALESCE((
                           SELECT SUM(pr.cash_refund_cents)
                           FROM purchase_returns pr
                           WHERE pr.supplier_id=s.id
                       ),0) AS expected_cents
                FROM suppliers s
                WHERE s.balance_cents!=(
                    COALESCE((
                        SELECT SUM(b.purchase_price_cents*b.quantity)
                        FROM batches b
                        WHERE b.supplier_id=s.id AND b.deleted_at IS NULL
                    ),0) -
                    COALESCE((
                        SELECT SUM(pr.amount_cents)
                        FROM purchase_returns pr
                        WHERE pr.supplier_id=s.id
                    ),0) -
                    COALESCE((
                        SELECT SUM(
                            CASE WHEN p.entry_kind='reversal'
                                 THEN -p.amount_cents ELSE p.amount_cents END
                        )
                        FROM payments p
                        WHERE p.supplier_id=s.id AND p.type='payable'
                    ),0) +
                    COALESCE((
                        SELECT SUM(pr.cash_refund_cents)
                        FROM purchase_returns pr
                        WHERE pr.supplier_id=s.id
                    ),0)
                )
                """
            ).fetchall()
        ]
        amount_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT 'batches' AS entity_type,id,'purchase_price_cents' AS field
                FROM batches
                WHERE purchase_price_cents IS NULL
                   OR typeof(purchase_price_cents)!='integer'
                UNION ALL
                SELECT 'quotes',id,'quote_price_cents' FROM quotes
                WHERE quote_price_cents IS NULL OR typeof(quote_price_cents)!='integer'
                UNION ALL
                SELECT 'quotes',id,'received_amount_cents' FROM quotes
                WHERE received_amount_cents IS NULL
                   OR typeof(received_amount_cents)!='integer'
                UNION ALL
                SELECT 'payments',id,'amount_cents' FROM payments
                WHERE amount_cents IS NULL OR typeof(amount_cents)!='integer'
                UNION ALL
                SELECT 'customers',id,'balance_cents' FROM customers
                WHERE balance_cents IS NULL OR typeof(balance_cents)!='integer'
                UNION ALL
                SELECT 'suppliers',id,'balance_cents' FROM suppliers
                WHERE balance_cents IS NULL OR typeof(balance_cents)!='integer'
                """
            ).fetchall()
        ]
        receivable_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,quote_price_cents*quote_quantity AS total_cents,
                       received_amount_cents
                FROM quotes
                WHERE received_amount_cents<0
                   OR received_amount_cents>quote_price_cents*quote_quantity
                """
            ).fetchall()
        ]
        owner_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,type,customer_id,supplier_id
                FROM payments
                WHERE (type='receivable' AND customer_id IS NULL)
                   OR (type='payable' AND supplier_id IS NULL)
                   OR type NOT IN ('receivable','payable')
                """
            ).fetchall()
        ]
        table_counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "products",
                "batches",
                "customers",
                "suppliers",
                "quotes",
                "payments",
            )
        }
        return {
            "integrity": integrity_rows,
            "foreign_keys": foreign_key_rows,
            "inventory": inventory_issues,
            "quote_allocations": quote_allocation_issues,
            "payment_allocations": payment_allocation_issues,
            "supplier_balances": supplier_balance_issues,
            "amounts": amount_issues,
            "receivables": receivable_issues,
            "payment_owners": owner_issues,
            "historical_stock_batches": stock_history["batch_count"],
            "historical_stock_units": stock_history["units"],
            "table_counts": table_counts,
        }
    finally:
        conn.close()

