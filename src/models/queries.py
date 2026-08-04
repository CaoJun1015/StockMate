"""Read-only application queries."""

from __future__ import annotations

from src.models.connection import connect
from src.models.repositories import cents_to_yuan
from src.utils.tax import calc_tax_adjusted_profit


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
                SELECT b.id,b.product_id,b.purchase_price,b.purchase_price_cents,
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
                SELECT b.date,p.series,p.cpu,b.quantity,b.purchase_price,
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
            "SELECT id,product_id,purchase_price,purchase_price_cents,quantity,"
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
            SELECT p.id,p.quote_id,p.customer_id,p.supplier_id,p.type,p.amount,
                   p.amount_cents,p.entry_kind,p.reversal_of_id,p.supersedes_id,
                   p.pay_date,p.method,p.remark,p.created_at,
                   c.name AS customer_name,s.name AS supplier_name,
                   q.quote_price,q.quote_quantity,q.received_amount
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
            SELECT q.id,q.batch_id,q.customer_id,q.quote_price,q.quote_price_cents,
                   q.quote_quantity,q.quote_date,q.remark,q.paid,q.status,
                   q.received_amount,q.received_amount_cents,q.sn_list,
                   q.tax_rate,q.purchase_tax_inclusive,q.quote_tax_inclusive,
                   p.series,p.cpu,p.ram,p.storage,p.gpu,
                   b.purchase_price,b.purchase_price_cents,b.sn_list AS batch_sn_list,
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
                SELECT q.id,q.quote_price,q.quote_price_cents,q.quote_quantity,
                       q.quote_date,q.remark,q.paid,q.status,q.received_amount,
                       q.received_amount_cents,q.sn_list,q.tax_rate,
                       q.purchase_tax_inclusive,q.quote_tax_inclusive,
                       p.series,p.cpu,p.ram,p.storage,p.gpu,p.screen,p.note,
                       b.purchase_price,b.purchase_price_cents,b.remark AS batch_remark,
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
                SELECT q.id,q.quote_date,q.quote_price,q.quote_quantity,q.status,
                       q.paid,q.received_amount,p.series,p.cpu,p.ram,p.storage,p.gpu,
                       b.purchase_price,b.remark AS batch_remark,q.remark,
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
                SELECT q.id,q.quote_price,q.quote_quantity,q.quote_date,q.remark,q.paid,
                       p.series,p.cpu,p.ram,p.storage,p.gpu,p.screen,p.note,
                       b.purchase_price,q.tax_rate,q.purchase_tax_inclusive,
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
            SELECT q.quote_price,q.quote_quantity,b.purchase_price,q.tax_rate,
                   q.purchase_tax_inclusive,q.quote_tax_inclusive
            FROM quotes q
            JOIN batches b ON q.batch_id=b.id
            WHERE q.customer_id=? AND q.deleted_at IS NULL AND q.status!='已取消'
            """,
            (customer_id,),
        ).fetchall()
    finally:
        conn.close()
    total_amount = 0.0
    total_profit = 0.0
    for row in rows:
        quantity = row["quote_quantity"] or 1
        quote_price = row["quote_price"] or 0
        purchase_price = row["purchase_price"] or 0
        total_amount += quote_price * quantity
        total_profit += calc_tax_adjusted_profit(
            purchase_price,
            quote_price,
            quantity,
            row["tax_rate"],
            bool(row["purchase_tax_inclusive"]),
            bool(row["quote_tax_inclusive"]),
        )
    return {
        "total_quotes": len(rows),
        "total_amount": total_amount,
        "total_profit": total_profit,
    }


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
                HAVING expected_cents!=allocated_cents
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
                           SELECT SUM(
                               CASE WHEN p.entry_kind='reversal'
                                    THEN -p.amount_cents ELSE p.amount_cents END
                           )
                           FROM payments p
                           WHERE p.supplier_id=s.id AND p.type='payable'
                       ),0) AS expected_cents
                FROM suppliers s
                WHERE s.balance_cents!=(
                    COALESCE((
                        SELECT SUM(b.purchase_price_cents*b.quantity)
                        FROM batches b
                        WHERE b.supplier_id=s.id AND b.deleted_at IS NULL
                    ),0) -
                    COALESCE((
                        SELECT SUM(
                            CASE WHEN p.entry_kind='reversal'
                                 THEN -p.amount_cents ELSE p.amount_cents END
                        )
                        FROM payments p
                        WHERE p.supplier_id=s.id AND p.type='payable'
                    ),0)
                )
                """
            ).fetchall()
        ]
        amount_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT 'batches' AS entity_type,id,'purchase_price' AS field
                FROM batches
                WHERE CAST(ROUND(purchase_price*100) AS INTEGER)!=purchase_price_cents
                UNION ALL
                SELECT 'quotes',id,'quote_price' FROM quotes
                WHERE CAST(ROUND(quote_price*100) AS INTEGER)!=quote_price_cents
                UNION ALL
                SELECT 'quotes',id,'received_amount' FROM quotes
                WHERE CAST(ROUND(received_amount*100) AS INTEGER)!=received_amount_cents
                UNION ALL
                SELECT 'payments',id,'amount' FROM payments
                WHERE CAST(ROUND(amount*100) AS INTEGER)!=amount_cents
                UNION ALL
                SELECT 'suppliers',id,'balance' FROM suppliers
                WHERE CAST(ROUND(balance*100) AS INTEGER)!=balance_cents
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

