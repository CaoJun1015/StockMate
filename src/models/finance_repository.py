"""Write helpers for the v1.16 operating ledger.

These helpers never open, commit, or close a connection.  Services own the
transaction boundary.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_finance_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM finance_settings WHERE id=1").fetchone()
    if row is None:
        conn.execute("INSERT INTO finance_settings(id) VALUES (1)")
        row = conn.execute("SELECT * FROM finance_settings WHERE id=1").fetchone()
    return dict(row)


def set_finance_enabled(conn: sqlite3.Connection, enabled_at: str) -> None:
    conn.execute(
        "UPDATE finance_settings "
        "SET enabled_at=?, initialized_at=CURRENT_TIMESTAMP WHERE id=1",
        (enabled_at,),
    )


def get_finance_enabled_at(conn: sqlite3.Connection) -> str | None:
    return get_finance_settings(conn).get("enabled_at")


def insert_ledger_account(
    conn: sqlite3.Connection,
    *,
    code: str,
    name: str,
    account_type: str = "asset",
    is_system: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO ledger_accounts(code,name,account_type,is_system) "
        "VALUES (?,?,?,?)",
        (code, name, account_type, int(is_system)),
    )
    return int(cursor.lastrowid)


def get_ledger_account(
    conn: sqlite3.Connection,
    account_id: int,
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM ledger_accounts WHERE id=?",
            (account_id,),
        ).fetchone()
    )


def get_ledger_account_by_code(
    conn: sqlite3.Connection,
    code: str,
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM ledger_accounts WHERE code=?",
            (code,),
        ).fetchone()
    )


def update_ledger_account(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    name: str,
    is_active: bool,
) -> None:
    conn.execute(
        "UPDATE ledger_accounts SET name=?,is_active=? "
        "WHERE id=? AND is_system=0 AND deleted_at IS NULL",
        (name, int(is_active), account_id),
    )


def insert_finance_category(
    conn: sqlite3.Connection,
    *,
    name: str,
    kind: str,
    affects_profit: bool = True,
) -> int:
    cursor = conn.execute(
        "INSERT INTO finance_categories(name,kind,affects_profit) VALUES (?,?,?)",
        (name, kind, int(affects_profit)),
    )
    return int(cursor.lastrowid)


def update_finance_category(
    conn: sqlite3.Connection,
    category_id: int,
    *,
    name: str,
    is_active: bool,
) -> None:
    conn.execute(
        "UPDATE finance_categories SET name=?,is_active=? "
        "WHERE id=? AND deleted_at IS NULL",
        (name, int(is_active), category_id),
    )


def get_finance_category(
    conn: sqlite3.Connection,
    category_id: int,
    *,
    kind: str | None = None,
    active_only: bool = False,
) -> dict[str, Any] | None:
    conditions = ["id=?", "deleted_at IS NULL"]
    params: list[object] = [category_id]
    if kind:
        conditions.append("kind=?")
        params.append(kind)
    if active_only:
        conditions.append("is_active=1")
    return _dict(
        conn.execute(
            "SELECT * FROM finance_categories WHERE " + " AND ".join(conditions),
            params,
        ).fetchone()
    )


def find_ledger_entry_by_key(
    conn: sqlite3.Connection,
    idempotency_key: str,
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM ledger_entries WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
    )


def insert_ledger_entry(
    conn: sqlite3.Connection,
    *,
    entry_date: str,
    event_type: str,
    source_type: str,
    source_id: str | None,
    idempotency_key: str,
    status: str = "normal",
    reversal_of_id: int | None = None,
    supersedes_id: int | None = None,
    reason: str = "",
    remark: str = "",
) -> int:
    cursor = conn.execute(
        "INSERT INTO ledger_entries "
        "(entry_date,event_type,source_type,source_id,idempotency_key,status,"
        "reversal_of_id,supersedes_id,reason,remark) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            entry_date,
            event_type,
            source_type,
            source_id,
            idempotency_key,
            status,
            reversal_of_id,
            supersedes_id,
            reason,
            remark,
        ),
    )
    return int(cursor.lastrowid)


def insert_ledger_line(
    conn: sqlite3.Connection,
    *,
    entry_id: int,
    account_id: int,
    debit_cents: int,
    credit_cents: int,
    customer_id: int | None = None,
    supplier_id: int | None = None,
    quote_id: int | None = None,
    batch_id: int | None = None,
    category_id: int | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO ledger_lines "
        "(entry_id,account_id,debit_cents,credit_cents,customer_id,supplier_id,"
        "quote_id,batch_id,category_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            entry_id,
            account_id,
            debit_cents,
            credit_cents,
            customer_id,
            supplier_id,
            quote_id,
            batch_id,
            category_id,
        ),
    )
    return int(cursor.lastrowid)


def get_ledger_entry(
    conn: sqlite3.Connection,
    entry_id: int,
) -> dict[str, Any] | None:
    return _dict(
        conn.execute("SELECT * FROM ledger_entries WHERE id=?", (entry_id,)).fetchone()
    )


def list_ledger_lines(
    conn: sqlite3.Connection,
    entry_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM ledger_lines WHERE entry_id=? ORDER BY id",
            (entry_id,),
        ).fetchall()
    ]


def set_ledger_entry_status(
    conn: sqlite3.Connection,
    entry_id: int,
    status: str,
) -> None:
    conn.execute(
        "UPDATE ledger_entries SET status=? WHERE id=?",
        (status, entry_id),
    )


def ledger_entry_has_reversal(conn: sqlite3.Connection, entry_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM ledger_entries WHERE reversal_of_id=?",
        (entry_id,),
    ).fetchone() is not None


def account_balance_cents(conn: sqlite3.Connection, account_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(debit_cents-credit_cents),0) "
            "FROM ledger_lines WHERE account_id=?",
            (account_id,),
        ).fetchone()[0]
    )


def finance_setup_control_totals(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          (SELECT COALESCE(SUM(balance_cents),0) FROM customers)
              AS customer_cache_cents,
          (SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0)
           FROM ledger_lines l
           JOIN ledger_accounts a ON a.id=l.account_id
           WHERE a.code='AR') AS customer_ledger_cents,
          (SELECT COALESCE(SUM(balance_cents),0) FROM suppliers)
              AS supplier_cache_cents,
          (SELECT COALESCE(SUM(l.credit_cents-l.debit_cents),0)
           FROM ledger_lines l
           JOIN ledger_accounts a ON a.id=l.account_id
           WHERE a.code='AP') AS supplier_ledger_cents,
          (SELECT COALESCE(SUM(
               (SELECT COALESCE(SUM(im.quantity_delta),0)
                FROM inventory_movements im WHERE im.batch_id=b.id)
               * b.purchase_price_cents
           ),0) FROM batches b WHERE b.deleted_at IS NULL)
              AS inventory_expected_cents,
          (SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0)
           FROM ledger_lines l
           JOIN ledger_accounts a ON a.id=l.account_id
           WHERE a.code='INVENTORY') AS inventory_ledger_cents,
          (SELECT COUNT(*) FROM (
             SELECT l.entry_id
             FROM ledger_lines l
             GROUP BY l.entry_id
             HAVING SUM(l.debit_cents)!=SUM(l.credit_cents)
           )) AS unbalanced_entries
        """
    ).fetchone()
    return {key: int(row[key]) for key in row.keys()}


def has_supplier_payment_allocations(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM supplier_payment_allocations LIMIT 1"
    ).fetchone() is not None


def list_supplier_batches_for_allocation(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,supplier_id,purchase_price_cents*quantity AS total_cents "
            "FROM batches WHERE supplier_id IS NOT NULL AND deleted_at IS NULL "
            "ORDER BY date,id"
        ).fetchall()
    ]


def list_supplier_payments_for_allocation(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,supplier_id,amount_cents,entry_kind,reversal_of_id "
            "FROM payments WHERE type='payable' ORDER BY pay_date,id"
        ).fetchall()
    ]


def list_open_customer_balances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT customer_id,"
            "SUM(quote_price_cents*quote_quantity-received_amount_cents-COALESCE(("
            "SELECT SUM(sr.revenue_cents) FROM sales_returns sr WHERE sr.quote_id=quotes.id"
            "),0)) "
            "AS open_cents FROM quotes "
            "WHERE deleted_at IS NULL AND status='已出库' "
            "AND customer_id IS NOT NULL "
            "AND quote_price_cents*quote_quantity-COALESCE(("
            "SELECT SUM(sr.revenue_cents) FROM sales_returns sr WHERE sr.quote_id=quotes.id"
            "),0)>received_amount_cents "
            "GROUP BY customer_id"
        ).fetchall()
    ]


def reset_customer_balances(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE customers SET balance_cents=0")


def set_customer_balance(
    conn: sqlite3.Connection,
    customer_id: int,
    balance_cents: int,
) -> None:
    conn.execute(
        "UPDATE customers SET balance_cents=? WHERE id=?",
        (balance_cents, customer_id),
    )


def list_open_receivable_quotes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,customer_id,"
            "quote_price_cents*quote_quantity-received_amount_cents-COALESCE(("
            "SELECT SUM(sr.revenue_cents) FROM sales_returns sr WHERE sr.quote_id=quotes.id"
            "),0) AS open_cents "
            "FROM quotes WHERE deleted_at IS NULL AND status='已出库' "
            "AND customer_id IS NOT NULL "
            "AND quote_price_cents*quote_quantity-COALESCE(("
            "SELECT SUM(sr.revenue_cents) FROM sales_returns sr WHERE sr.quote_id=quotes.id"
            "),0)>received_amount_cents "
            "ORDER BY quote_date,id"
        ).fetchall()
    ]


def list_nonzero_supplier_balances(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,balance_cents FROM suppliers "
            "WHERE deleted_at IS NULL AND balance_cents!=0 ORDER BY id"
        ).fetchall()
    ]


def list_opening_inventory_batches(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,remaining,purchase_price_cents FROM batches "
            "WHERE deleted_at IS NULL AND remaining>0 ORDER BY date,id"
        ).fetchall()
    ]


def list_active_unreversed_payments(
    conn: sqlite3.Connection,
    *,
    owner_field: str,
    owner_id: int,
    pay_type: str,
) -> list[dict[str, Any]]:
    if owner_field not in ("customer_id", "supplier_id"):
        raise ValueError("invalid payment owner field")
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT p.id,p.amount_cents
            FROM payments p
            WHERE p.{owner_field}=? AND p.type=?
              AND p.entry_kind='payment'
              AND NOT EXISTS(
                  SELECT 1 FROM payments r WHERE r.reversal_of_id=p.id
              )
            ORDER BY p.pay_date,p.id
            """,
            (owner_id, pay_type),
        ).fetchall()
    ]


def customer_payment_allocated_cents(
    conn: sqlite3.Connection,
    payment_id: int,
) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) "
            "FROM payment_allocations WHERE payment_id=?",
            (payment_id,),
        ).fetchone()[0]
    )


def supplier_payment_allocated_cents(
    conn: sqlite3.Connection,
    payment_id: int,
) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) "
            "FROM supplier_payment_allocations WHERE payment_id=?",
            (payment_id,),
        ).fetchone()[0]
    )


def payment_refunded_cents(conn: sqlite3.Connection, payment_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) "
            "FROM payment_refund_allocations WHERE payment_id=?",
            (payment_id,),
        ).fetchone()[0]
    )


def insert_payment_refund_allocation(
    conn: sqlite3.Connection,
    payment_id: int,
    amount_cents: int,
    *,
    sales_return_id: int | None = None,
    purchase_return_id: int | None = None,
) -> int:
    if (sales_return_id is None) == (purchase_return_id is None):
        raise ValueError("退款来源必须且只能关联一张退货单")
    cursor = conn.execute(
        "INSERT INTO payment_refund_allocations "
        "(payment_id,sales_return_id,purchase_return_id,amount_cents) "
        "VALUES (?,?,?,?)",
        (payment_id, sales_return_id, purchase_return_id, amount_cents),
    )
    return int(cursor.lastrowid)


def list_unattributed_cash_refunds(
    conn: sqlite3.Connection,
    *,
    owner_field: str,
    owner_id: int,
    pay_type: str,
) -> list[dict[str, Any]]:
    if owner_field == "customer_id" and pay_type == "receivable":
        sql = """
            SELECT sr.id,sr.cash_refund_cents,
                   COALESCE(SUM(pra.amount_cents),0) AS attributed_cents
            FROM sales_returns sr
            JOIN quotes q ON q.id=sr.quote_id
            LEFT JOIN payment_refund_allocations pra
              ON pra.sales_return_id=sr.id
            WHERE q.customer_id=? AND sr.cash_refund_cents>0
            GROUP BY sr.id
            HAVING attributed_cents!=sr.cash_refund_cents
        """
    elif owner_field == "supplier_id" and pay_type == "payable":
        sql = """
            SELECT pr.id,pr.cash_refund_cents,
                   COALESCE(SUM(pra.amount_cents),0) AS attributed_cents
            FROM purchase_returns pr
            LEFT JOIN payment_refund_allocations pra
              ON pra.purchase_return_id=pr.id
            WHERE pr.supplier_id=? AND pr.cash_refund_cents>0
            GROUP BY pr.id
            HAVING attributed_cents!=pr.cash_refund_cents
        """
    else:
        raise ValueError("invalid refund owner")
    return [dict(row) for row in conn.execute(sql, (owner_id,)).fetchall()]


def list_supplier_payable_batches(
    conn: sqlite3.Connection,
    supplier_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT b.id,
                   b.purchase_price_cents*b.quantity
                     - COALESCE((
                         SELECT SUM(pr.amount_cents)
                         FROM purchase_returns pr WHERE pr.batch_id=b.id
                       ),0) AS payable_cents,
                   COALESCE((
                       SELECT SUM(spa.amount_cents)
                       FROM supplier_payment_allocations spa
                       WHERE spa.batch_id=b.id
                   ),0) AS allocated_cents
            FROM batches b
            WHERE b.supplier_id=? AND b.deleted_at IS NULL
            ORDER BY b.date,b.id
            """,
            (supplier_id,),
        ).fetchall()
    ]


def sales_return_totals(
    conn: sqlite3.Connection,
    quote_id: int,
) -> dict[str, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) AS quantity,"
        "COALESCE(SUM(revenue_cents),0) AS revenue_cents "
        "FROM sales_returns WHERE quote_id=?",
        (quote_id,),
    ).fetchone()
    return {"quantity": row["quantity"], "revenue_cents": row["revenue_cents"]}


def list_customer_allocations_for_release(
    conn: sqlite3.Connection,
    quote_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT payment_id,SUM(amount_cents) AS allocated_cents
            FROM payment_allocations
            WHERE quote_id=?
            GROUP BY payment_id
            HAVING allocated_cents>0
            ORDER BY payment_id DESC
            """,
            (quote_id,),
        ).fetchall()
    ]


def list_supplier_allocations_for_release(
    conn: sqlite3.Connection,
    batch_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT payment_id,SUM(amount_cents) AS allocated_cents
            FROM supplier_payment_allocations
            WHERE batch_id=?
            GROUP BY payment_id
            HAVING allocated_cents>0
            ORDER BY payment_id DESC
            """,
            (batch_id,),
        ).fetchall()
    ]


def supplier_batch_allocated_cents(
    conn: sqlite3.Connection,
    batch_id: int,
) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) "
            "FROM supplier_payment_allocations WHERE batch_id=?",
            (batch_id,),
        ).fetchone()[0]
    )


def clear_import_defaults(conn: sqlite3.Connection, *, accounts: bool, categories: bool) -> None:
    """Only called after the service has verified an empty staging database."""
    if accounts:
        conn.execute("DELETE FROM ledger_accounts")
    if categories:
        conn.execute("DELETE FROM finance_categories")


def update_import_ledger_links(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    reversal_of_id: int | None,
    supersedes_id: int | None,
) -> None:
    conn.execute(
        "UPDATE ledger_entries SET reversal_of_id=?,supersedes_id=? WHERE id=?",
        (reversal_of_id, supersedes_id, entry_id),
    )


def update_import_finance_settings(
    conn: sqlite3.Connection,
    *,
    enabled_at: str | None,
    initialized_at: str | None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "UPDATE finance_settings SET enabled_at=?,initialized_at=?,created_at=COALESCE(?,created_at) WHERE id=1",
        (enabled_at, initialized_at, created_at),
    )


def insert_shipment_snapshot(
    conn: sqlite3.Connection,
    *,
    quote_id: int,
    shipped_date: str,
    quantity: int,
    unit_sale_cents: int,
    unit_cost_cents: int | None,
    cost_cents: int | None = None,
    ledger_entry_id: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO shipment_snapshots "
        "(quote_id,shipped_date,quantity,unit_sale_cents,unit_cost_cents,"
        "revenue_cents,cost_cents,ledger_entry_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            quote_id,
            shipped_date,
            quantity,
            unit_sale_cents,
            unit_cost_cents,
            unit_sale_cents * quantity,
            cost_cents if cost_cents is not None else unit_cost_cents * quantity,
            ledger_entry_id,
        ),
    )
    return int(cursor.lastrowid)


def get_shipment_snapshot(
    conn: sqlite3.Connection,
    quote_id: int,
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM shipment_snapshots WHERE quote_id=?",
            (quote_id,),
        ).fetchone()
    )


def add_supplier_payment_allocation(
    conn: sqlite3.Connection,
    payment_id: int,
    batch_id: int,
    amount_cents: int,
) -> None:
    conn.execute(
        "INSERT INTO supplier_payment_allocations(payment_id,batch_id,amount_cents) "
        "VALUES (?,?,?)",
        (payment_id, batch_id, amount_cents),
    )


def list_supplier_payment_allocations(
    conn: sqlite3.Connection,
    payment_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT batch_id,amount_cents FROM supplier_payment_allocations "
            "WHERE payment_id=? ORDER BY id",
            (payment_id,),
        ).fetchall()
    ]


def insert_sales_return(
    conn: sqlite3.Connection,
    *,
    quote_id: int,
    return_date: str,
    quantity: int,
    revenue_cents: int,
    cost_cents: int,
    restock_quantity: int,
    cash_refund_cents: int,
    account_id: int | None,
    ledger_entry_id: int,
    reason: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO sales_returns "
        "(quote_id,return_date,quantity,revenue_cents,cost_cents,"
        "restock_quantity,cash_refund_cents,account_id,ledger_entry_id,reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            quote_id,
            return_date,
            quantity,
            revenue_cents,
            cost_cents,
            restock_quantity,
            cash_refund_cents,
            account_id,
            ledger_entry_id,
            reason,
        ),
    )
    return int(cursor.lastrowid)


def insert_purchase_return(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    supplier_id: int,
    return_date: str,
    quantity: int,
    amount_cents: int,
    cash_refund_cents: int,
    account_id: int | None,
    ledger_entry_id: int,
    reason: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO purchase_returns "
        "(batch_id,supplier_id,return_date,quantity,amount_cents,"
        "cash_refund_cents,account_id,ledger_entry_id,reason) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            batch_id,
            supplier_id,
            return_date,
            quantity,
            amount_cents,
            cash_refund_cents,
            account_id,
            ledger_entry_id,
            reason,
        ),
    )
    return int(cursor.lastrowid)
