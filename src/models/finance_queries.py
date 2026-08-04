"""Read-only queries for the v1.16 operating-finance UI and reports."""

from __future__ import annotations

from datetime import date

from src.models.connection import connect


def get_finance_setup_state(db_path=None) -> dict:
    conn = connect(db_path, read_only=True)
    try:
        settings = conn.execute(
            "SELECT enabled_at,initialized_at FROM finance_settings WHERE id=1"
        ).fetchone()
        shipped = conn.execute(
            "SELECT COUNT(*) AS count,"
            "COALESCE(SUM(quote_price_cents*quote_quantity-received_amount_cents),0) "
            "AS cents FROM quotes WHERE deleted_at IS NULL AND status='已出库' "
            "AND quote_price_cents*quote_quantity>received_amount_cents"
        ).fetchone()
        inventory = conn.execute(
            "SELECT COUNT(*) AS count,"
            "COALESCE(SUM(remaining*purchase_price_cents),0) AS cents "
            "FROM batches WHERE deleted_at IS NULL AND remaining>0"
        ).fetchone()
        supplier = conn.execute(
            "SELECT COALESCE(SUM(balance_cents),0) AS cents "
            "FROM suppliers WHERE deleted_at IS NULL"
        ).fetchone()
        return {
            "enabled": bool(settings and settings["enabled_at"]),
            "enabled_at": settings["enabled_at"] if settings else None,
            "initialized_at": settings["initialized_at"] if settings else None,
            "opening_receivable_count": shipped["count"],
            "opening_receivable_cents": shipped["cents"],
            "opening_inventory_count": inventory["count"],
            "opening_inventory_cents": inventory["cents"],
            "opening_payable_cents": supplier["cents"],
        }
    finally:
        conn.close()


def list_financial_accounts(
    *,
    include_inactive: bool = False,
    db_path=None,
) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        active = "" if include_inactive else "AND a.is_active=1"
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT a.*,
                       COALESCE(SUM(l.debit_cents-l.credit_cents),0) AS balance_cents
                FROM ledger_accounts a
                LEFT JOIN ledger_lines l ON l.account_id=a.id
                WHERE a.is_system=0 AND a.deleted_at IS NULL {active}
                GROUP BY a.id
                ORDER BY a.is_active DESC,a.id
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def list_account_transactions(
    *,
    date_from: str,
    date_to: str,
    db_path=None,
) -> list[dict]:
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.id,e.entry_date,e.event_type,e.source_type,e.source_id,
                       e.status,e.remark,e.reason,a.id AS account_id,
                       a.name AS account_name,l.debit_cents,l.credit_cents,
                       c.name AS customer_name,s.name AS supplier_name
                FROM ledger_lines l
                JOIN ledger_entries e ON e.id=l.entry_id
                JOIN ledger_accounts a ON a.id=l.account_id
                LEFT JOIN customers c ON c.id=l.customer_id
                LEFT JOIN suppliers s ON s.id=l.supplier_id
                WHERE a.is_system=0
                  AND e.entry_date BETWEEN ? AND ?
                ORDER BY e.entry_date,e.id,l.id
                """,
                (date_from, date_to),
            ).fetchall()
        ]
    finally:
        conn.close()


def list_finance_categories(
    kind: str | None = None,
    *,
    include_inactive: bool = False,
    db_path=None,
) -> list[dict]:
    conditions = ["deleted_at IS NULL"]
    params: list[object] = []
    if kind:
        conditions.append("kind=?")
        params.append(kind)
    if not include_inactive:
        conditions.append("is_active=1")
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM finance_categories WHERE "
                + " AND ".join(conditions)
                + " ORDER BY kind,name",
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def get_finance_dashboard(
    date_from: str | None = None,
    date_to: str | None = None,
    db_path=None,
) -> dict:
    today = date.today()
    date_from = date_from or today.replace(day=1).isoformat()
    date_to = date_to or today.isoformat()
    conn = connect(db_path, read_only=True)
    try:
        funds = conn.execute(
            """
            SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0)
            FROM ledger_lines l
            JOIN ledger_accounts a ON a.id=l.account_id
            WHERE a.is_system=0 AND a.deleted_at IS NULL
            """
        ).fetchone()[0]
        controls = {
            row["code"]: row["balance"]
            for row in conn.execute(
                """
                SELECT a.code,
                       COALESCE(SUM(
                           CASE WHEN a.account_type IN ('asset','expense')
                                THEN l.debit_cents-l.credit_cents
                                ELSE l.credit_cents-l.debit_cents END
                       ),0) AS balance
                FROM ledger_accounts a
                LEFT JOIN ledger_lines l ON l.account_id=a.id
                WHERE a.code IN ('AR','AP')
                GROUP BY a.code
                """
            )
        }
        period = {
            row["code"]: row["amount"]
            for row in conn.execute(
                """
                SELECT a.code,
                       COALESCE(SUM(
                           CASE WHEN a.account_type IN ('asset','expense')
                                THEN l.debit_cents-l.credit_cents
                                ELSE l.credit_cents-l.debit_cents END
                       ),0) AS amount
                FROM ledger_accounts a
                LEFT JOIN ledger_lines l ON l.account_id=a.id
                LEFT JOIN ledger_entries e ON e.id=l.entry_id
                WHERE a.code IN ('SALES','COGS','EXPENSE','OTHER_INCOME')
                  AND e.entry_date BETWEEN ? AND ?
                GROUP BY a.code
                """,
                (date_from, date_to),
            )
        }
        cash_change = conn.execute(
            """
            SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0)
            FROM ledger_lines l
            JOIN ledger_accounts a ON a.id=l.account_id
            JOIN ledger_entries e ON e.id=l.entry_id
            WHERE a.is_system=0
              AND e.entry_date BETWEEN ? AND ?
              AND e.event_type NOT IN (
                  'opening_funds','account_opening','funds_adjustment'
              )
            """,
            (date_from, date_to),
        ).fetchone()[0]
        sales = period.get("SALES", 0)
        cogs = period.get("COGS", 0)
        expense = period.get("EXPENSE", 0)
        other_income = period.get("OTHER_INCOME", 0)
        gross = sales - cogs
        return {
            "date_from": date_from,
            "date_to": date_to,
            "funds_cents": funds,
            "receivable_cents": max(controls.get("AR", 0), 0),
            "customer_advance_cents": max(-controls.get("AR", 0), 0),
            "payable_cents": max(controls.get("AP", 0), 0),
            "supplier_advance_cents": max(-controls.get("AP", 0), 0),
            "sales_cents": sales,
            "cogs_cents": cogs,
            "gross_profit_cents": gross,
            "expense_cents": expense,
            "other_income_cents": other_income,
            "net_profit_cents": gross - expense + other_income,
            "cash_change_cents": cash_change,
        }
    finally:
        conn.close()


def list_operating_entries(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    source_id: str | int | None = None,
    keyword: str | None = None,
    limit: int = 1000,
    db_path=None,
) -> list[dict]:
    conditions = ["1=1"]
    params: list[object] = []
    if date_from:
        conditions.append("e.entry_date>=?")
        params.append(date_from)
    if date_to:
        conditions.append("e.entry_date<=?")
        params.append(date_to)
    if event_type:
        conditions.append("e.event_type=?")
        params.append(event_type)
    if status:
        conditions.append("e.status=?")
        params.append(status)
    if source_id not in (None, ""):
        conditions.append("e.source_id=?")
        params.append(str(source_id))
    if keyword:
        conditions.append(
            "(COALESCE(e.remark,'') LIKE ? OR COALESCE(e.reason,'') LIKE ? "
            "OR COALESCE(c.name,'') LIKE ? OR COALESCE(s.name,'') LIKE ?)"
        )
        token = f"%{keyword}%"
        params.extend([token, token, token, token])
    params.append(limit)
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT e.*,
                       MAX(c.name) AS customer_name,
                       MAX(s.name) AS supplier_name,
                       COALESCE(SUM(l.debit_cents),0) AS debit_total_cents,
                       COALESCE(SUM(
                           CASE WHEN a.is_system=0
                                THEN l.debit_cents-l.credit_cents ELSE 0 END
                       ),0) AS cash_change_cents
                FROM ledger_entries e
                JOIN ledger_lines l ON l.entry_id=e.id
                JOIN ledger_accounts a ON a.id=l.account_id
                LEFT JOIN customers c ON c.id=l.customer_id
                LEFT JOIN suppliers s ON s.id=l.supplier_id
                WHERE {" AND ".join(conditions)}
                GROUP BY e.id
                ORDER BY e.entry_date DESC,e.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def get_operating_entry_detail(entry_id: int, db_path=None) -> dict | None:
    conn = connect(db_path, read_only=True)
    try:
        entry = conn.execute(
            "SELECT * FROM ledger_entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return None
        lines = [
            dict(row)
            for row in conn.execute(
                """
                SELECT l.*,a.code,a.name AS account_name,c.name AS customer_name,
                       s.name AS supplier_name,fc.name AS category_name
                FROM ledger_lines l
                JOIN ledger_accounts a ON a.id=l.account_id
                LEFT JOIN customers c ON c.id=l.customer_id
                LEFT JOIN suppliers s ON s.id=l.supplier_id
                LEFT JOIN finance_categories fc ON fc.id=l.category_id
                WHERE l.entry_id=? ORDER BY l.id
                """,
                (entry_id,),
            ).fetchall()
        ]
        result = dict(entry)
        result["lines"] = lines
        result["related_entries"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,entry_date,event_type,status,reversal_of_id,supersedes_id
                FROM ledger_entries
                WHERE id IN (?,?)
                   OR reversal_of_id=?
                   OR supersedes_id=?
                ORDER BY id
                """,
                (
                    entry["reversal_of_id"] or -1,
                    entry["supersedes_id"] or -1,
                    entry_id,
                    entry_id,
                ),
            ).fetchall()
        ]
        quote_ids = sorted(
            {line["quote_id"] for line in lines if line["quote_id"] is not None}
        )
        batch_ids = sorted(
            {line["batch_id"] for line in lines if line["batch_id"] is not None}
        )
        result["shipment_snapshots"] = []
        result["sales_returns"] = []
        if quote_ids:
            placeholders = ",".join("?" for _ in quote_ids)
            result["shipment_snapshots"] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM shipment_snapshots "
                    f"WHERE quote_id IN ({placeholders}) ORDER BY id",
                    quote_ids,
                ).fetchall()
            ]
            result["sales_returns"] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM sales_returns "
                    f"WHERE quote_id IN ({placeholders}) ORDER BY id",
                    quote_ids,
                ).fetchall()
            ]
        result["purchase_returns"] = []
        if batch_ids:
            placeholders = ",".join("?" for _ in batch_ids)
            result["purchase_returns"] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM purchase_returns "
                    f"WHERE batch_id IN ({placeholders}) ORDER BY id",
                    batch_ids,
                ).fetchall()
            ]
        result["payment"] = None
        result["allocations"] = []
        if entry["source_type"] == "payment" and entry["source_id"]:
            try:
                payment_id = int(entry["source_id"])
            except (TypeError, ValueError):
                payment_id = None
            if payment_id is not None:
                payment = conn.execute(
                    "SELECT * FROM payments WHERE id=?",
                    (payment_id,),
                ).fetchone()
                result["payment"] = dict(payment) if payment else None
                if payment:
                    allocation_table = (
                        "payment_allocations"
                        if payment["type"] == "receivable"
                        else "supplier_payment_allocations"
                    )
                    result["allocations"] = [
                        dict(row)
                        for row in conn.execute(
                            f"SELECT * FROM {allocation_table} "
                            "WHERE payment_id=? ORDER BY id",
                            (payment_id,),
                        ).fetchall()
                    ]
        return result
    finally:
        conn.close()


def list_counterparty_balances(kind: str, db_path=None) -> list[dict]:
    if kind not in ("customer", "supplier"):
        raise ValueError("kind must be customer or supplier")
    table = "customers" if kind == "customer" else "suppliers"
    dimension = "customer_id" if kind == "customer" else "supplier_id"
    code = "AR" if kind == "customer" else "AP"
    sign = "l.debit_cents-l.credit_cents" if kind == "customer" else (
        "l.credit_cents-l.debit_cents"
    )
    if kind == "customer":
        open_items = """
            (SELECT COUNT(*) FROM quotes q
             WHERE q.customer_id=p.id AND q.status='已出库'
               AND q.deleted_at IS NULL
               AND q.quote_price_cents*q.quote_quantity
                   - COALESCE((
                       SELECT SUM(sr.revenue_cents)
                       FROM sales_returns sr WHERE sr.quote_id=q.id
                     ),0)
                   - q.received_amount_cents > 0)
        """
        oldest_date = """
            (SELECT MIN(COALESCE(ss.shipped_date,q.quote_date))
             FROM quotes q
             LEFT JOIN shipment_snapshots ss ON ss.quote_id=q.id
             WHERE q.customer_id=p.id AND q.status='已出库'
               AND q.deleted_at IS NULL
               AND q.quote_price_cents*q.quote_quantity
                   - COALESCE((
                       SELECT SUM(sr.revenue_cents)
                       FROM sales_returns sr WHERE sr.quote_id=q.id
                     ),0)
                   - q.received_amount_cents > 0)
        """
    else:
        open_items = """
            (SELECT COUNT(*) FROM batches b
             WHERE b.supplier_id=p.id AND b.deleted_at IS NULL
               AND b.purchase_price_cents*b.quantity
                   - COALESCE((
                       SELECT SUM(pr.amount_cents)
                       FROM purchase_returns pr WHERE pr.batch_id=b.id
                     ),0)
                   - COALESCE((
                       SELECT SUM(spa.amount_cents)
                       FROM supplier_payment_allocations spa
                       WHERE spa.batch_id=b.id
                     ),0) > 0)
        """
        oldest_date = """
            (SELECT MIN(b.date) FROM batches b
             WHERE b.supplier_id=p.id AND b.deleted_at IS NULL
               AND b.purchase_price_cents*b.quantity
                   - COALESCE((
                       SELECT SUM(pr.amount_cents)
                       FROM purchase_returns pr WHERE pr.batch_id=b.id
                     ),0)
                   - COALESCE((
                       SELECT SUM(spa.amount_cents)
                       FROM supplier_payment_allocations spa
                       WHERE spa.batch_id=b.id
                     ),0) > 0)
        """
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT p.id,p.name,p.wechat,p.phone,
                       COALESCE(SUM({sign}),0) AS balance_cents,
                       {open_items} AS open_item_count,
                       {oldest_date} AS oldest_open_date
                FROM {table} p
                LEFT JOIN ledger_lines l ON l.{dimension}=p.id
                    AND l.account_id=(
                        SELECT id FROM ledger_accounts WHERE code=?
                    )
                WHERE p.deleted_at IS NULL
                GROUP BY p.id
                HAVING balance_cents!=0
                ORDER BY ABS(balance_cents) DESC,p.id
                """,
                (code,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_profit_report(
    *,
    date_from: str,
    date_to: str,
    group_by: str = "quote",
    db_path=None,
) -> list[dict]:
    grouping = {
        "quote": ("COALESCE(l.quote_id,0)", "CAST(l.quote_id AS TEXT)"),
        "customer": (
            "COALESCE(l.customer_id,0)",
            "COALESCE(c.name,'未关联客户')",
        ),
        "product": (
            "COALESCE(p.id,0)",
            "TRIM(COALESCE(p.series,'') || ' ' || COALESCE(p.cpu,''))",
        ),
        "month": ("SUBSTR(e.entry_date,1,7)", "SUBSTR(e.entry_date,1,7)"),
    }
    if group_by not in grouping:
        raise ValueError("unsupported profit grouping")
    key_expr, label_expr = grouping[group_by]
    conn = connect(db_path, read_only=True)
    try:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT {key_expr} AS group_key,{label_expr} AS label,
                       COALESCE(SUM(
                           CASE WHEN a.code='SALES'
                                THEN l.credit_cents-l.debit_cents ELSE 0 END
                       ),0) AS sales_cents,
                       COALESCE(SUM(
                           CASE WHEN a.code='COGS'
                                THEN l.debit_cents-l.credit_cents ELSE 0 END
                       ),0) AS cogs_cents,
                       COALESCE(SUM(
                           CASE WHEN a.code='EXPENSE'
                                THEN l.debit_cents-l.credit_cents ELSE 0 END
                       ),0) AS expense_cents,
                       COALESCE(SUM(
                           CASE WHEN a.code='OTHER_INCOME'
                                THEN l.credit_cents-l.debit_cents ELSE 0 END
                       ),0) AS other_income_cents
                FROM ledger_lines l
                JOIN ledger_entries e ON e.id=l.entry_id
                JOIN ledger_accounts a ON a.id=l.account_id
                LEFT JOIN quotes q ON q.id=l.quote_id
                LEFT JOIN customers c ON c.id=COALESCE(l.customer_id,q.customer_id)
                LEFT JOIN batches b ON b.id=COALESCE(l.batch_id,q.batch_id)
                LEFT JOIN products p ON p.id=b.product_id
                WHERE e.entry_date BETWEEN ? AND ?
                  AND a.code IN ('SALES','COGS','EXPENSE','OTHER_INCOME')
                GROUP BY {key_expr}
                ORDER BY sales_cents DESC,label
                """,
                (date_from, date_to),
            ).fetchall()
        ]
    finally:
        conn.close()


def collect_finance_reconciliation_snapshot(db_path=None) -> dict:
    conn = connect(db_path, read_only=True)
    try:
        enabled = bool(
            conn.execute(
                "SELECT enabled_at FROM finance_settings WHERE id=1"
            ).fetchone()["enabled_at"]
        )
        unbalanced = [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.id,SUM(l.debit_cents) AS debit_cents,
                       SUM(l.credit_cents) AS credit_cents
                FROM ledger_entries e
                JOIN ledger_lines l ON l.entry_id=e.id
                GROUP BY e.id
                HAVING SUM(l.debit_cents)!=SUM(l.credit_cents)
                """
            ).fetchall()
        ]
        customer_balances = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.id,c.name,c.balance_cents,
                       COALESCE(SUM(l.debit_cents-l.credit_cents),0)
                           AS ledger_cents
                FROM customers c
                LEFT JOIN ledger_lines l ON l.customer_id=c.id
                    AND l.account_id=(
                        SELECT id FROM ledger_accounts WHERE code='AR'
                    )
                GROUP BY c.id
                HAVING balance_cents!=ledger_cents
                """
            ).fetchall()
        ]
        supplier_balances = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.id,s.name,s.balance_cents,
                       COALESCE(SUM(l.credit_cents-l.debit_cents),0)
                           AS ledger_cents
                FROM suppliers s
                LEFT JOIN ledger_lines l ON l.supplier_id=s.id
                    AND l.account_id=(
                        SELECT id FROM ledger_accounts WHERE code='AP'
                    )
                GROUP BY s.id
                HAVING balance_cents!=ledger_cents
                """
            ).fetchall()
        ]
        inventory = conn.execute(
            """
            SELECT
                COALESCE((
                    SELECT SUM(remaining*purchase_price_cents)
                    FROM batches WHERE deleted_at IS NULL
                ),0) AS business_cents,
                COALESCE((
                    SELECT SUM(l.debit_cents-l.credit_cents)
                    FROM ledger_lines l
                    WHERE l.account_id=(
                        SELECT id FROM ledger_accounts WHERE code='INVENTORY'
                    )
                ),0) AS ledger_cents
            """
        ).fetchone()
        shipment_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT q.id
                FROM quotes q
                LEFT JOIN shipment_snapshots ss ON ss.quote_id=q.id
                WHERE q.deleted_at IS NULL
                  AND q.status IN ('已出库','已收款')
                  AND ss.id IS NULL
                  AND EXISTS(
                      SELECT 1 FROM ledger_entries e
                      WHERE e.source_type='quote'
                        AND e.source_id=CAST(q.id AS TEXT)
                        AND e.event_type='sales_shipment'
                  )
                """
            ).fetchall()
        ]
        sales_return_issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT ss.quote_id,ss.quantity,
                       COALESCE(SUM(sr.quantity),0) AS returned_quantity
                FROM shipment_snapshots ss
                LEFT JOIN sales_returns sr ON sr.quote_id=ss.quote_id
                GROUP BY ss.quote_id
                HAVING returned_quantity>ss.quantity
                """
            ).fetchall()
        ]
        customer_allocations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.id,p.amount_cents,p.entry_kind,
                       COALESCE(SUM(a.amount_cents),0) AS allocated_cents
                FROM payments p
                LEFT JOIN payment_allocations a ON a.payment_id=p.id
                WHERE p.type='receivable'
                GROUP BY p.id
                HAVING
                    (p.entry_kind='payment'
                     AND (allocated_cents<0 OR allocated_cents>p.amount_cents))
                    OR
                    (p.entry_kind='reversal'
                     AND (allocated_cents>0 OR allocated_cents< -p.amount_cents))
                """
            ).fetchall()
        ]
        supplier_allocations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.id,p.amount_cents,p.entry_kind,
                       COALESCE(SUM(a.amount_cents),0) AS allocated_cents
                FROM payments p
                LEFT JOIN supplier_payment_allocations a ON a.payment_id=p.id
                WHERE p.type='payable'
                GROUP BY p.id
                HAVING
                    (p.entry_kind='payment'
                     AND (allocated_cents<0 OR allocated_cents>p.amount_cents))
                    OR
                    (p.entry_kind='reversal'
                     AND (allocated_cents>0 OR allocated_cents< -p.amount_cents))
                """
            ).fetchall()
        ]
        return {
            "enabled": enabled,
            "unbalanced_entries": unbalanced,
            "customer_balances": customer_balances,
            "supplier_balances": supplier_balances,
            "inventory": dict(inventory),
            "shipment_snapshots": shipment_issues,
            "sales_returns": sales_return_issues,
            "customer_allocations": customer_allocations,
            "supplier_allocations": supplier_allocations,
        }
    finally:
        conn.close()
