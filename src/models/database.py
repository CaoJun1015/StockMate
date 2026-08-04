"""v1.14 compatibility facade for the legacy function API.

New code should use ``src.services`` and the repository/query modules.  The
functions in this module remain available for one compatibility release.
"""

from pathlib import Path

from src.models.connection import (
    BACKUP_DIR,
    DB_PATH,
    MAX_BACKUPS,
    connect,
    create_backup,
    get_app_path,
    get_data_dir,
)
from src.models.migrations import DatabaseMigrationError, migrate_database
from src.models.repositories import (
    add_allocation,
    audit,
    cents_to_yuan,
    insert_payment,
    soft_delete,
    sync_quote_payment_state,
    yuan_to_cents,
)

APP_DATA_DIR = str(get_data_dir())


def get_connection():
    # Keep DB_PATH patchable for existing integrations and tests.
    return connect(DB_PATH)


def backup_database():
    """Create a WAL-safe backup while preserving the legacy return shape."""
    try:
        info = create_backup(DB_PATH)
        if info is None:
            return True, "数据库文件不存在，跳过备份"
        return True, f"备份成功: {info.path.name}"
    except Exception as exc:
        return False, f"备份失败: {exc}"


def verify_data_integrity():
    """启动时运行 v1.14 自动对账，返回旧接口形状。"""
    from src.services.reconciliation_service import ReconciliationService

    report = ReconciliationService(DB_PATH).run()
    return report.is_clean, [issue.message for issue in report.issues]


def init_db():
    """Migrate automatically, fail closed, then create a normal startup backup."""
    backup = migrate_database(Path(DB_PATH))
    from src.services.reconciliation_service import ReconciliationService

    report = ReconciliationService(DB_PATH).run()
    ok, message = backup_database()
    reconciliation_note = (
        "自动对账通过"
        if report.is_clean
        else f"自动对账发现 {len(report.issues)} 个问题，请在“数据安全”菜单查看"
    )
    if not ok:
        return True, f"数据库初始化成功；{reconciliation_note}；{message}"
    migration_note = f"；迁移备份: {backup.path.name}" if backup else ""
    return True, f"数据库初始化成功{migration_note}；{reconciliation_note}；{message}"


# ---------- 机型管理 ----------

def add_product(series, cpu="", ram="", storage="", gpu="", screen="", note=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO products (series, cpu, ram, storage, gpu, screen, note) VALUES (?,?,?,?,?,?,?)",
        (series, cpu, ram, storage, gpu, screen, note),
    )
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return pid


def update_product(pid, series, cpu, ram, storage, gpu, screen, note):
    conn = get_connection()
    conn.execute(
        "UPDATE products SET series=?, cpu=?, ram=?, storage=?, gpu=?, screen=?, note=? WHERE id=?",
        (series, cpu, ram, storage, gpu, screen, note, pid),
    )
    conn.commit()
    conn.close()


def delete_product(pid):
    """Soft-delete a product and its active batches without erasing history."""
    conn = get_connection()
    try:
        soft_delete(conn, "products", pid, "用户删除机型")
        for row in conn.execute(
            "SELECT id,supplier_id,purchase_price,purchase_price_cents,quantity "
            "FROM batches WHERE product_id=? AND deleted_at IS NULL", (pid,)
        ):
            if row["supplier_id"]:
                conn.execute(
                    "UPDATE suppliers SET balance=balance-?, balance_cents=balance_cents-? "
                    "WHERE id=?",
                    (
                        row["purchase_price"] * row["quantity"],
                        row["purchase_price_cents"] * row["quantity"],
                        row["supplier_id"],
                    ),
                )
            soft_delete(conn, "batches", row["id"], "所属机型已删除")
            for quote in conn.execute(
                "SELECT id,status FROM quotes WHERE batch_id=? AND deleted_at IS NULL",
                (row["id"],),
            ):
                if quote["status"] in ("待确认", "已取消"):
                    soft_delete(conn, "quotes", quote["id"], "所属机型已删除")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def search_products(keyword):
    conn = get_connection()
    like = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT id, series, cpu, ram, storage, gpu, screen, note
        FROM products
        WHERE deleted_at IS NULL AND
              (series LIKE ? OR cpu LIKE ? OR ram LIKE ? OR storage LIKE ? OR gpu LIKE ? OR screen LIKE ? OR note LIKE ?)
        ORDER BY series, cpu
        """,
        (like, like, like, like, like, like, like),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_products():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, series, cpu, ram, storage, gpu, screen, note "
        "FROM products WHERE deleted_at IS NULL ORDER BY series, cpu"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- 批次库存管理 ----------

def add_batch(product_id, purchase_price, quantity, remaining, date_str, remark="", supplier_id=None, sn_list=""):
    conn = get_connection()
    price_cents = yuan_to_cents(purchase_price)
    conn.execute(
        "INSERT INTO batches (product_id, purchase_price, purchase_price_cents, quantity, remaining, date, remark, supplier_id, sn_list) VALUES (?,?,?,?,?,?,?,?,?)",
        (product_id, purchase_price, price_cents, quantity, remaining, date_str, remark, supplier_id, sn_list),
    )
    if supplier_id:
        conn.execute(
            "UPDATE suppliers SET balance=COALESCE(balance,0)+?, "
            "balance_cents=COALESCE(balance_cents,0)+? WHERE id=?",
            (purchase_price * quantity, price_cents * quantity, supplier_id),
        )
    conn.commit()
    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return bid


def get_batches(product_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT b.id, b.product_id, b.purchase_price, b.quantity, b.remaining, "
        "b.date, b.remark, CASE WHEN s.deleted_at IS NULL THEN b.supplier_id END AS supplier_id, "
        "b.sn_list FROM batches b LEFT JOIN suppliers s ON b.supplier_id=s.id "
        "WHERE b.product_id=? AND b.deleted_at IS NULL ORDER BY b.date DESC, b.id DESC",
        (product_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_batch_remaining(batch_id, new_remaining):
    conn = get_connection()
    conn.execute("UPDATE batches SET remaining=? WHERE id=?", (new_remaining, batch_id))
    conn.commit()
    conn.close()


def deduct_batch_remaining(batch_id, quantity):
    """扣减批次库存，返回是否成功"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT remaining FROM batches WHERE id=?", (batch_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "批次不存在"
    
    current_remaining = row[0]
    if current_remaining < quantity:
        conn.close()
        return False, f"库存不足！当前剩余 {current_remaining} 台，报价 {quantity} 台"
    
    new_remaining = current_remaining - quantity
    conn.execute("UPDATE batches SET remaining=? WHERE id=?", (new_remaining, batch_id))
    conn.commit()
    conn.close()
    return True, f"扣减成功，剩余 {new_remaining} 台"


def get_batch_remaining(batch_id):
    """获取指定批次的剩余库存"""
    conn = get_connection()
    cursor = conn.execute("SELECT remaining FROM batches WHERE id=?", (batch_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def delete_batch(batch_id):
    """Soft-delete a batch while retaining orders and ledger history."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT supplier_id,purchase_price,purchase_price_cents,quantity "
            "FROM batches WHERE id=? AND deleted_at IS NULL",
            (batch_id,),
        ).fetchone()
        if row and row["supplier_id"]:
            conn.execute(
                "UPDATE suppliers SET balance=balance-?, balance_cents=balance_cents-? "
                "WHERE id=?",
                (
                    row["purchase_price"] * row["quantity"],
                    row["purchase_price_cents"] * row["quantity"],
                    row["supplier_id"],
                ),
            )
        soft_delete(conn, "batches", batch_id, "用户删除批次")
        for quote in conn.execute(
            "SELECT id,status FROM quotes WHERE batch_id=? AND deleted_at IS NULL",
            (batch_id,),
        ):
            if quote["status"] in ("待确认", "已取消"):
                soft_delete(conn, "quotes", quote["id"], "所属批次已删除")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_total_remaining(product_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(remaining),0) FROM batches "
        "WHERE product_id=? AND deleted_at IS NULL", (product_id,)
    ).fetchone()[0]
    conn.close()
    return row


# ---------- 客户管理 ----------

def add_customer(name, wechat="", qq="", phone="", note="", default_tax_rate=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO customers (name, wechat, qq, phone, note, default_tax_rate) VALUES (?,?,?,?,?,?)",
        (name, wechat, qq, phone, note, default_tax_rate),
    )
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return cid


def search_customers(keyword):
    conn = get_connection()
    kw = f"%{keyword}%"
    rows = conn.execute(
        "SELECT id, name, wechat, qq, phone, note FROM customers "
        "WHERE deleted_at IS NULL AND "
        "(name LIKE ? OR wechat LIKE ? OR qq LIKE ? OR phone LIKE ? OR note LIKE ?) ORDER BY name",
        (kw, kw, kw, kw, kw),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_customers():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, wechat, qq, phone, note FROM customers "
        "WHERE deleted_at IS NULL ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- 上游管理 ----------

def add_supplier(name, wechat="", qq="", phone="", note=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO suppliers (name, wechat, qq, phone, note) VALUES (?,?,?,?,?)",
        (name, wechat, qq, phone, note),
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return sid


def search_suppliers(keyword):
    conn = get_connection()
    kw = f"%{keyword}%"
    rows = conn.execute(
        "SELECT id, name, wechat, qq, phone, note FROM suppliers "
        "WHERE deleted_at IS NULL AND "
        "(name LIKE ? OR wechat LIKE ? OR qq LIKE ? OR phone LIKE ? OR note LIKE ?) ORDER BY name",
        (kw, kw, kw, kw, kw),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_suppliers():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, wechat, qq, phone, note FROM suppliers "
        "WHERE deleted_at IS NULL ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_customer_cascade(customer_id):
    """Compatibility name: soft-delete the customer and preserve history."""
    conn = get_connection()
    quote_count = conn.execute("SELECT COUNT(*) FROM quotes WHERE customer_id=?", (customer_id,)).fetchone()[0]
    payment_count = conn.execute("SELECT COUNT(*) FROM payments WHERE customer_id=?", (customer_id,)).fetchone()[0]
    soft_delete(conn, "customers", customer_id, "用户删除客户")
    conn.commit()
    conn.close()
    return {"quotes": quote_count, "payments": payment_count}


def delete_supplier_cascade(supplier_id):
    """Compatibility name: soft-delete the supplier and preserve history."""
    conn = get_connection()
    batch_count = conn.execute("SELECT COUNT(*) FROM batches WHERE supplier_id=?", (supplier_id,)).fetchone()[0]
    payment_count = conn.execute("SELECT COUNT(*) FROM payments WHERE supplier_id=?", (supplier_id,)).fetchone()[0]
    soft_delete(conn, "suppliers", supplier_id, "用户删除供应商")
    conn.commit()
    conn.close()
    return {"batches": batch_count, "payments": payment_count}


# ---------- 报价记录 ----------

def add_quote(batch_id, customer_id, quote_price, quote_quantity, quote_date, remark="", paid="", status="待确认", received_amount=0, sn_list="", tax_rate=None, purchase_tax_inclusive=0, quote_tax_inclusive=0):
    conn = get_connection()
    quote_price_cents = yuan_to_cents(quote_price)
    received_amount_cents = yuan_to_cents(received_amount)
    conn.execute(
        "INSERT INTO quotes (batch_id, customer_id, quote_price, quote_price_cents, quote_quantity, quote_date, remark, paid, status, received_amount, received_amount_cents, sn_list, tax_rate, purchase_tax_inclusive, quote_tax_inclusive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, customer_id, quote_price, quote_price_cents, quote_quantity, quote_date, remark, paid, status, received_amount, received_amount_cents, sn_list, tax_rate, purchase_tax_inclusive, quote_tax_inclusive),
    )
    conn.commit()
    qid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return qid


def update_quote(quote_id, batch_id, customer_id, quote_price, quote_quantity, quote_date, remark, paid, sn_list="", tax_rate=None, purchase_tax_inclusive=0, quote_tax_inclusive=0):
    conn = get_connection()
    conn.execute(
        "UPDATE quotes SET batch_id=?, customer_id=?, quote_price=?, quote_price_cents=?, quote_quantity=?, quote_date=?, remark=?, paid=?, sn_list=?, tax_rate=?, purchase_tax_inclusive=?, quote_tax_inclusive=? WHERE id=?",
        (batch_id, customer_id, quote_price, yuan_to_cents(quote_price), quote_quantity, quote_date, remark, paid, sn_list, tax_rate, purchase_tax_inclusive, quote_tax_inclusive, quote_id),
    )
    conn.commit()
    conn.close()


def delete_quote(quote_id):
    """Soft-delete only draft/cancelled quotes; never erase financial history."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status,batch_id,quote_quantity FROM quotes WHERE id=?", (quote_id,)
        ).fetchone()
        if row and row["status"] == "已出库":
            conn.execute(
                "UPDATE batches SET remaining=remaining+? WHERE id=?",
                (row["quote_quantity"], row["batch_id"]),
            )
            conn.execute(
                "UPDATE quotes SET status='已取消',sn_list='' WHERE id=?", (quote_id,)
            )
            audit(
                conn,
                "quotes",
                quote_id,
                "transition",
                before={"status": "已出库"},
                after={"status": "已取消"},
                reason="兼容删除前自动取消",
            )
        elif row and row["status"] not in ("待确认", "已取消"):
            raise ValueError("已报价、已出库或已收款记录不能直接删除")
        soft_delete(conn, "quotes", quote_id, "用户删除报价")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_quote_by_id(quote_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT q.id, q.batch_id, q.customer_id, q.quote_price, q.quote_quantity, q.quote_date, q.remark, q.paid, q.status, q.received_amount, q.sn_list, "
        "q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive, "
        "p.series, p.cpu, p.ram, p.storage, p.gpu, "
        "b.purchase_price, b.sn_list as batch_sn_list, "
        "c.name as customer_name "
        "FROM quotes q "
        "JOIN batches b ON q.batch_id = b.id "
        "JOIN products p ON b.product_id = p.id "
        "LEFT JOIN customers c ON q.customer_id = c.id "
        "WHERE q.id=? AND q.deleted_at IS NULL AND b.deleted_at IS NULL "
        "AND p.deleted_at IS NULL AND (c.id IS NULL OR c.deleted_at IS NULL)",
        (quote_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def search_quotes(keyword="", date_from="", date_to="", customer_id=None):
    conn = get_connection()
    conditions = [
        "q.deleted_at IS NULL",
        "b.deleted_at IS NULL",
        "p.deleted_at IS NULL",
        "(c.id IS NULL OR c.deleted_at IS NULL)",
    ]
    params = []

    if keyword:
        kw = f"%{keyword}%"
        conditions.append("(p.series LIKE ? OR p.cpu LIKE ? OR p.ram LIKE ? OR p.storage LIKE ? OR p.gpu LIKE ? OR c.name LIKE ? OR b.remark LIKE ? OR q.remark LIKE ? OR b.sn_list LIKE ? OR s.name LIKE ?)")
        params.extend([kw] * 10)
    if date_from:
        conditions.append("q.quote_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("q.quote_date <= ?")
        params.append(date_to)
    if customer_id:
        conditions.append("q.customer_id = ?")
        params.append(customer_id)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT q.id, q.quote_price, q.quote_quantity, q.quote_date, q.remark, q.paid, q.status, q.received_amount, q.sn_list,
               q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive,
               p.series, p.cpu, p.ram, p.storage, p.gpu, p.screen, p.note,
               b.purchase_price, b.remark as batch_remark, b.id as batch_id, b.sn_list as batch_sn_list,
               c.name as customer_name, c.id as customer_id,
               s.name as supplier_name
        FROM quotes q
        JOIN batches b ON q.batch_id = b.id
        JOIN products p ON b.product_id = p.id
        LEFT JOIN customers c ON q.customer_id = c.id
        LEFT JOIN suppliers s ON b.supplier_id = s.id
        WHERE {where}
        ORDER BY q.quote_date DESC, q.id DESC
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_quotes(date_from="", date_to="", customer_id=None):
    """导出的数据包含完整财务信息"""
    return search_quotes("", date_from, date_to, customer_id)


# 合法状态流转表
VALID_TRANSITIONS = {
    "待确认": ["已报价", "已取消"],
    "已报价": ["已出库", "已取消"],
    "已出库": ["已收款", "已取消"],
    "已收款": [],
    "已取消": [],
}


def ship_quote(quote_id, sn_list=""):
    """
    出库操作（封装校验+扣减+保存SN+状态更新）

    步骤：
    1. 校验报价是否存在
    2. 校验状态是否允许出库（待确认/已报价）
    3. 校验库存是否充足
    4. 扣减 batches.remaining
    5. 保存 SN 到 quotes.sn_list
    6. 更新状态为"已出库"

    返回 (True, "出库成功") 或 (False, 错误信息)
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status, batch_id, quote_quantity FROM quotes WHERE id=?",
            (quote_id,)
        ).fetchone()
        if not row:
            return False, "报价记录不存在"

        old_status = row[0]
        batch_id = row[1]
        quote_quantity = row[2] or 0

        # 校验状态
        if old_status not in ("待确认", "已报价"):
            return False, f"当前状态「{old_status}」不允许出库"

        # 校验库存
        batch_row = conn.execute(
            "SELECT remaining FROM batches WHERE id=? AND deleted_at IS NULL", (batch_id,)
        ).fetchone()
        if not batch_row:
            return False, "批次不存在或已删除"
        if batch_row[0] < quote_quantity:
            return False, "库存不足，无法出库"

        # 执行出库
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute(
            "UPDATE quotes SET status='已出库', sn_list=? WHERE id=?",
            (sn_list, quote_id)
        )
        conn.commit()
        return True, "出库成功"
    except Exception as e:
        conn.rollback()
        return False, f"出库失败: {str(e)}"
    finally:
        conn.close()


def update_quote_status(quote_id, new_status):
    """
    更新报价状态（带状态机守卫）

    特殊处理：
    - 校验状态流转合法性，非法跳转返回 (False, 错误信息)
    - 从"已出库"变为"已取消"时，回补库存 + 清空报价SN
    - 返回 (True, "状态更新成功") 或 (False, 错误信息)
    """
    conn = get_connection()
    try:
        # 获取当前报价信息
        row = conn.execute(
            "SELECT status, batch_id, quote_quantity FROM quotes WHERE id=?",
            (quote_id,)
        ).fetchone()
        if not row:
            return False, "报价记录不存在"

        old_status = row[0]
        batch_id = row[1]
        quote_quantity = row[2] or 0

        # 校验状态流转合法性
        if new_status not in VALID_TRANSITIONS.get(old_status, []):
            return False, f"不允许从「{old_status}」变更为「{new_status}」"

        # 从"已出库"变为"已取消"：回补库存 + 清空报价SN
        if old_status == "已出库" and new_status == "已取消":
            conn.execute(
                "UPDATE batches SET remaining = remaining + ? WHERE id = ?",
                (quote_quantity, batch_id)
            )
            conn.execute(
                "UPDATE quotes SET sn_list = '' WHERE id = ?",
                (quote_id,)
            )

        conn.execute("UPDATE quotes SET status=? WHERE id=?", (new_status, quote_id))
        conn.commit()
        return True, "状态更新成功"
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def _add_payment_raw(conn, quote_id=None, customer_id=None, supplier_id=None,
                      pay_type="receivable", amount=0, pay_date="", method="", remark=""):
    """
    添加收付款记录的原始操作（不管理连接和事务）

    供批量收款/付款时在同一个事务中调用。
    外部调用方需自行管理 conn.commit() / conn.rollback() / conn.close()
    """
    amount_cents = yuan_to_cents(amount)
    payment_id = insert_payment(
        conn,
        quote_id=quote_id,
        customer_id=customer_id,
        supplier_id=supplier_id,
        pay_type=pay_type,
        amount_cents=amount_cents,
        pay_date=pay_date,
        method=method,
        remark=remark,
    )
    if quote_id and pay_type == "receivable":
        add_allocation(conn, payment_id, quote_id, amount_cents)
        conn.execute(
            "UPDATE quotes SET received_amount_cents=received_amount_cents+? WHERE id=?",
            (amount_cents, quote_id),
        )
        sync_quote_payment_state(conn, quote_id)
    if pay_type == "payable" and supplier_id:
        conn.execute(
            "UPDATE suppliers SET balance=balance-?, balance_cents=balance_cents-? "
            "WHERE id=?",
            (amount, amount_cents, supplier_id),
        )
    audit(
        conn,
        "payments",
        payment_id,
        "create",
        after={"type": pay_type, "amount_cents": amount_cents},
    )
    return payment_id


def add_payment(quote_id=None, customer_id=None, supplier_id=None, pay_type="receivable",
                amount=0, pay_date="", method="", remark=""):
    """
    添加收付款记录（对外接口，自动管理连接和事务）
    """
    conn = get_connection()
    try:
        pid = _add_payment_raw(conn, quote_id, customer_id, supplier_id, pay_type, amount, pay_date, method, remark)
        conn.commit()
        return pid
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_payments(quote_id=None, customer_id=None, supplier_id=None):
    conn = get_connection()
    conditions = []
    params = []
    if quote_id:
        conditions.append("p.quote_id = ?")
        params.append(quote_id)
        conditions.append(
            "EXISTS(SELECT 1 FROM quotes q "
            "JOIN batches b ON q.batch_id=b.id "
            "JOIN products pr ON b.product_id=pr.id "
            "LEFT JOIN customers c ON q.customer_id=c.id "
            "WHERE q.id=p.quote_id AND q.deleted_at IS NULL "
            "AND b.deleted_at IS NULL AND pr.deleted_at IS NULL "
            "AND (c.id IS NULL OR c.deleted_at IS NULL))"
        )
    if customer_id:
        conditions.append("p.customer_id = ?")
        params.append(customer_id)
    if supplier_id:
        conditions.append("p.supplier_id = ?")
        params.append(supplier_id)
        conditions.append(
            "EXISTS(SELECT 1 FROM suppliers s WHERE s.id=p.supplier_id AND s.deleted_at IS NULL)"
        )
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM payments p WHERE {where} ORDER BY p.pay_date DESC, p.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_customer_balance(customer_id=None):
    conn = get_connection()
    if customer_id:
        row = conn.execute(
            "SELECT COALESCE(SUM(q.quote_price * q.quote_quantity - q.received_amount), 0) "
            "FROM quotes q WHERE q.customer_id = ? AND q.deleted_at IS NULL "
            "AND q.status IN ('已报价','已出库')",
            (customer_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(q.quote_price * q.quote_quantity - q.received_amount), 0) "
            "FROM quotes q WHERE q.deleted_at IS NULL "
            "AND q.status IN ('已报价','已出库')",
        ).fetchone()
    conn.close()
    return row[0] if row else 0


def get_supplier_payable(supplier_id=None):
    conn = get_connection()
    if supplier_id:
        row = conn.execute("SELECT COALESCE(balance, 0) FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    else:
        row = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM suppliers").fetchone()
    conn.close()
    return row[0] if row else 0


def get_customer_statement(customer_id, date_from="", date_to=""):
    conn = get_connection()
    conditions = ["q.customer_id = ?"]
    params = [customer_id]
    if date_from:
        conditions.append("q.quote_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("q.quote_date <= ?")
        params.append(date_to)
    where = " AND ".join(conditions)
    sql = f"""
        SELECT q.id, q.quote_date, q.quote_price, q.quote_quantity, q.status, q.paid, q.received_amount,
               p.series, p.cpu, p.ram, p.storage, p.gpu,
               b.purchase_price, b.remark as batch_remark, q.remark,
               s.name as supplier_name
        FROM quotes q
        JOIN batches b ON q.batch_id = b.id
        JOIN products p ON b.product_id = p.id
        LEFT JOIN suppliers s ON b.supplier_id = s.id
        WHERE {where}
        ORDER BY q.quote_date, q.id
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_operation_log(operation, table_name, record_id, description=""):
    """记录操作日志"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO operation_logs (operation, table_name, record_id, description) VALUES (?,?,?,?)",
        (operation, table_name, record_id, description),
    )
    conn.commit()
    conn.close()


def get_operation_logs(limit=100):
    """获取最近的操作日志"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_customer_quotes(customer_id):
    """获取客户的所有报价记录（购买历史）"""
    conn = get_connection()
    sql = """
        SELECT q.id, q.quote_price, q.quote_quantity, q.quote_date, q.remark, q.paid,
               p.series, p.cpu, p.ram, p.storage, p.gpu, p.screen, p.note,
               b.purchase_price, q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive
        FROM quotes q
        JOIN batches b ON q.batch_id = b.id
        JOIN products p ON b.product_id = p.id
        WHERE q.customer_id = ?
        ORDER BY q.quote_date DESC, q.id DESC
    """
    rows = conn.execute(sql, (customer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_customer_stats(customer_id):
    """获取客户统计信息（含税后利润）"""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT q.quote_price, q.quote_quantity, b.purchase_price,
               q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive
        FROM quotes q
        JOIN batches b ON q.batch_id = b.id
        WHERE q.customer_id = ? AND q.deleted_at IS NULL AND q.status != '已取消'
        """,
        (customer_id,),
    ).fetchall()
    conn.close()
    total_quotes = len(rows)
    total_amount = 0
    total_profit = 0
    for r in rows:
        quote_price = r["quote_price"] or 0
        quote_quantity = r["quote_quantity"] or 1
        purchase_price = r["purchase_price"] or 0
        tax_rate = r["tax_rate"]
        purchase_tax_inclusive = r["purchase_tax_inclusive"] or 0
        quote_tax_inclusive = r["quote_tax_inclusive"] or 0
        total_amount += quote_price * quote_quantity
        total_profit += calc_tax_adjusted_profit(
            purchase_price, quote_price, quote_quantity, tax_rate,
            purchase_tax_inclusive, quote_tax_inclusive,
        )
    return {"total_quotes": total_quotes, "total_amount": total_amount, "total_profit": total_profit}


# ---------- 收付款记录管理（修改/删除） ----------

def get_payment_by_id(payment_id):
    """获取单条收付款记录详情"""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT p.id, p.quote_id, p.customer_id, p.supplier_id, p.type, p.amount,
               p.amount_cents, p.entry_kind, p.reversal_of_id, p.supersedes_id,
               p.pay_date, p.method, p.remark, p.created_at,
               c.name as customer_name, s.name as supplier_name,
               q.quote_price, q.quote_quantity, q.received_amount
        FROM payments p
        LEFT JOIN customers c ON p.customer_id = c.id
        LEFT JOIN suppliers s ON p.supplier_id = s.id
        LEFT JOIN quotes q ON p.quote_id = q.id
        WHERE p.id = ?
        """,
        (payment_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_payments_with_details(pay_type=None, customer_id=None, supplier_id=None, date_from=None, date_to=None):
    """获取所有收付款记录（带关联对象名称），支持筛选"""
    conn = get_connection()
    conditions = []
    params = []
    
    if pay_type and pay_type != "全部":
        conditions.append("p.type = ?")
        params.append(pay_type)
    if customer_id:
        conditions.append("p.customer_id = ?")
        params.append(customer_id)
    if supplier_id:
        conditions.append("p.supplier_id = ?")
        params.append(supplier_id)
    if date_from:
        conditions.append("p.pay_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("p.pay_date <= ?")
        params.append(date_to)
    
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT p.id, p.quote_id, p.customer_id, p.supplier_id, p.type, p.amount,
               p.amount_cents, p.entry_kind, p.reversal_of_id, p.supersedes_id,
               p.pay_date, p.method, p.remark, p.created_at,
               COALESCE(c.name, '') as customer_name,
               COALESCE(s.name, '') as supplier_name,
               CASE
                 WHEN p.entry_kind='reversal' THEN '冲销'
                 WHEN EXISTS(SELECT 1 FROM payments r WHERE r.reversal_of_id=p.id) THEN '已冲销'
                 WHEN p.supersedes_id IS NOT NULL THEN '更正'
                 ELSE '正常'
               END AS ledger_status
        FROM payments p
        LEFT JOIN customers c ON p.customer_id = c.id
        LEFT JOIN suppliers s ON p.supplier_id = s.id
        WHERE {where}
        ORDER BY p.pay_date DESC, p.id DESC
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _update_quote_payment_status(conn, quote_id):
    """
    根据 quote 的 received_amount 重新计算 paid 和 status
    内部辅助函数，不管理连接和事务
    """
    sync_quote_payment_state(conn, quote_id)


def _allocate_customer_raw(conn, payment_id, customer_id, amount_cents):
    remaining = amount_cents
    rows = conn.execute(
        "SELECT id, quote_price_cents, quote_quantity, received_amount_cents "
        "FROM quotes WHERE customer_id=? AND deleted_at IS NULL "
        "AND status IN ('待确认','已报价','已出库') "
        "AND quote_price_cents*quote_quantity>received_amount_cents "
        "ORDER BY quote_date,id",
        (customer_id,),
    ).fetchall()
    available = sum(
        r["quote_price_cents"] * r["quote_quantity"] - r["received_amount_cents"]
        for r in rows
    )
    if amount_cents > available:
        raise ValueError("收款金额超过客户待收金额")
    for row in rows:
        pending = row["quote_price_cents"] * row["quote_quantity"] - row["received_amount_cents"]
        applied = min(remaining, pending)
        if applied:
            add_allocation(conn, payment_id, row["id"], applied)
            conn.execute(
                "UPDATE quotes SET received_amount_cents=received_amount_cents+? WHERE id=?",
                (applied, row["id"]),
            )
            sync_quote_payment_state(conn, row["id"])
            remaining -= applied
        if remaining == 0:
            break


def _void_payment_raw(conn, old_payment, reason):
    if old_payment["entry_kind"] != "payment":
        raise ValueError("冲销记录不能再次冲销")
    if conn.execute(
        "SELECT 1 FROM payments WHERE reversal_of_id=?", (old_payment["id"],)
    ).fetchone():
        raise ValueError("该记录已经冲销")
    reversal_id = insert_payment(
        conn,
        quote_id=old_payment["quote_id"],
        customer_id=old_payment["customer_id"],
        supplier_id=old_payment["supplier_id"],
        pay_type=old_payment["type"],
        amount_cents=old_payment["amount_cents"],
        pay_date=old_payment["pay_date"],
        method=old_payment["method"],
        remark=reason,
        entry_kind="reversal",
        reversal_of_id=old_payment["id"],
    )
    if old_payment["type"] == "receivable":
        for allocation in conn.execute(
            "SELECT quote_id,amount_cents FROM payment_allocations WHERE payment_id=?",
            (old_payment["id"],),
        ).fetchall():
            add_allocation(conn, reversal_id, allocation["quote_id"], -allocation["amount_cents"])
            conn.execute(
                "UPDATE quotes SET received_amount_cents=received_amount_cents-? WHERE id=?",
                (allocation["amount_cents"], allocation["quote_id"]),
            )
            sync_quote_payment_state(conn, allocation["quote_id"])
    elif old_payment["supplier_id"]:
        conn.execute(
            "UPDATE suppliers SET balance=balance+?, balance_cents=balance_cents+? WHERE id=?",
            (old_payment["amount"], old_payment["amount_cents"], old_payment["supplier_id"]),
        )
    audit(
        conn,
        "payments",
        old_payment["id"],
        "void",
        before=dict(old_payment),
        after={"reversal_id": reversal_id},
        reason=reason,
    )
    return reversal_id


def update_payment(payment_id, new_amount, new_pay_date, new_method, new_remark):
    """Compatibility edit: append a reversal and corrected replacement."""
    conn = get_connection()
    try:
        old_payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not old_payment:
            return False, "记录不存在"
        _void_payment_raw(conn, old_payment, "更正原流水")
        new_cents = yuan_to_cents(new_amount)
        replacement_id = insert_payment(
            conn,
            quote_id=old_payment["quote_id"],
            customer_id=old_payment["customer_id"],
            supplier_id=old_payment["supplier_id"],
            pay_type=old_payment["type"],
            amount_cents=new_cents,
            pay_date=new_pay_date,
            method=new_method,
            remark=new_remark,
            supersedes_id=payment_id,
        )
        if old_payment["type"] == "receivable":
            if old_payment["quote_id"]:
                add_allocation(conn, replacement_id, old_payment["quote_id"], new_cents)
                conn.execute(
                    "UPDATE quotes SET received_amount_cents=received_amount_cents+? WHERE id=?",
                    (new_cents, old_payment["quote_id"]),
                )
                sync_quote_payment_state(conn, old_payment["quote_id"])
            else:
                _allocate_customer_raw(
                    conn, replacement_id, old_payment["customer_id"], new_cents
                )
        elif old_payment["supplier_id"]:
            conn.execute(
                "UPDATE suppliers SET balance=balance-?, balance_cents=balance_cents-? WHERE id=?",
                (new_amount, new_cents, old_payment["supplier_id"]),
            )
        audit(
            conn,
            "payments",
            replacement_id,
            "correct",
            after={"supersedes_id": payment_id, "amount_cents": new_cents},
            reason="兼容接口更正",
        )
        conn.commit()
        return True, "更正成功"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_payment(payment_id):
    """Compatibility delete: append an immutable reversal entry."""
    conn = get_connection()
    try:
        old_payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not old_payment:
            return False, "记录不存在", {}
        _void_payment_raw(conn, old_payment, "用户作废流水")
        affected = {
            "quote_id": old_payment["quote_id"],
            "supplier_id": old_payment["supplier_id"],
            "customer_id": old_payment["customer_id"],
        }
        conn.commit()
        return True, "删除成功", affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 价税覆盖层 ----------

def calc_tax_adjusted_profit(purchase_price, quote_price, quantity, tax_rate, purchase_tax_inclusive, quote_tax_inclusive):
    """计算税后利润，tax_rate 为 None 或 0 时返回原始利润"""
    if tax_rate is None or tax_rate == 0:
        return (quote_price - purchase_price) * quantity
    purchase_excl = purchase_price / (1 + tax_rate) if purchase_tax_inclusive else purchase_price
    quote_excl = quote_price / (1 + tax_rate) if quote_tax_inclusive else quote_price
    return (quote_excl - purchase_excl) * quantity


def get_customer_default_tax_rate(customer_id):
    """获取客户的默认税率"""
    conn = get_connection()
    row = conn.execute("SELECT default_tax_rate FROM customers WHERE id=?", (customer_id,)).fetchone()
    conn.close()
    return row["default_tax_rate"] if row else None
