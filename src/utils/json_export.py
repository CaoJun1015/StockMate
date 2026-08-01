"""
JSON 数据导入导出模块：用于全量数据备份和迁移
"""

import json
import os
from datetime import datetime
from src.models.repositories import yuan_to_cents
from src.version import APP_VERSION


def export_all_to_json(db_module, output_path=None):
    """
    导出所有数据为 JSON 格式
    
    db_module: 数据库模块，包含各种获取数据的函数
    output_path: 输出路径，默认桌面
    """
    if not output_path:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(desktop, f"调货助手备份_{timestamp}.json")
    
    data = {
        "version": f"v{APP_VERSION}",
        "schema_version": 2,
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "products": _get_table("products"),
            "suppliers": _get_all_suppliers(db_module),
            "batches": _get_all_batches(db_module),
            "customers": _get_table("customers"),
            "quotes": _get_all_quotes(db_module),
            "payments": _get_all_payments(db_module),
            "payment_allocations": _get_table("payment_allocations"),
            "audit_events": _get_table("audit_events"),
        }
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return output_path


def _get_all_suppliers(db_module):
    """获取所有上游数据"""
    from src.models.database import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_all_batches(db_module):
    """获取所有批次数据"""
    from src.models.database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT b.*, p.series FROM batches b JOIN products p ON b.product_id = p.id ORDER BY b.id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_all_quotes(db_module):
    """获取所有报价记录"""
    from src.models.database import get_connection
    conn = get_connection()
    sql = """
        SELECT q.*, p.series, p.cpu, p.ram, p.storage, p.gpu, p.screen,
               b.purchase_price, c.name as customer_name
        FROM quotes q
        JOIN batches b ON q.batch_id = b.id
        JOIN products p ON b.product_id = p.id
        LEFT JOIN customers c ON q.customer_id = c.id
        ORDER BY q.id
    """
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_all_payments(db_module):
    """获取所有收付款记录"""
    from src.models.database import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT * FROM payments ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_table(table_name):
    allowed = {"products", "customers", "payment_allocations", "audit_events"}
    if table_name not in allowed:
        raise ValueError("不支持导出的表")
    from src.models.database import get_connection
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table_name} ORDER BY id")]
    finally:
        conn.close()


def import_from_json(json_path, db_module):
    """
    从 JSON 文件导入数据
    
    返回: (success: bool, message: str, stats: dict)
    """
    try:
        with open(json_path, "r", encoding="utf-8") as stream:
            document = json.load(stream)
        if "version" not in document or "data" not in document:
            return False, "无效的备份文件格式", {}

        payload = document["data"]
        stats = {
            key: 0 for key in ("products", "suppliers", "batches", "customers", "quotes", "payments")
        }
        maps = {key: {} for key in ("products", "suppliers", "batches", "customers", "quotes", "payments")}
        conn = db_module.get_connection()
        try:
            conn.execute("BEGIN")

            for product in payload.get("products", []):
                cursor = conn.execute(
                    "INSERT INTO products(series,cpu,ram,storage,gpu,screen,note,"
                    "deleted_at,deleted_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        product.get("series", ""),
                        product.get("cpu", ""),
                        product.get("ram", ""),
                        product.get("storage", ""),
                        product.get("gpu", ""),
                        product.get("screen", ""),
                        product.get("note", ""),
                        product.get("deleted_at"),
                        product.get("deleted_reason"),
                    ),
                )
                maps["products"][product["id"]] = cursor.lastrowid
                stats["products"] += 1

            for supplier in payload.get("suppliers", []):
                balance_cents = supplier.get("balance_cents")
                if balance_cents is None:
                    balance_cents = yuan_to_cents(supplier.get("balance", 0))
                cursor = conn.execute(
                    "INSERT INTO suppliers(name,wechat,qq,phone,note,balance,balance_cents,"
                    "deleted_at,deleted_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        supplier.get("name", ""),
                        supplier.get("wechat", ""),
                        supplier.get("qq", ""),
                        supplier.get("phone", ""),
                        supplier.get("note", ""),
                        balance_cents / 100,
                        balance_cents,
                        supplier.get("deleted_at"),
                        supplier.get("deleted_reason"),
                    ),
                )
                maps["suppliers"][supplier["id"]] = cursor.lastrowid
                stats["suppliers"] += 1

            for customer in payload.get("customers", []):
                balance_cents = customer.get("balance_cents")
                if balance_cents is None:
                    balance_cents = yuan_to_cents(customer.get("balance", 0))
                cursor = conn.execute(
                    "INSERT INTO customers(name,wechat,qq,phone,note,balance,balance_cents,"
                    "default_tax_rate,deleted_at,deleted_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        customer.get("name", ""),
                        customer.get("wechat", ""),
                        customer.get("qq", ""),
                        customer.get("phone", ""),
                        customer.get("note", ""),
                        balance_cents / 100,
                        balance_cents,
                        customer.get("default_tax_rate"),
                        customer.get("deleted_at"),
                        customer.get("deleted_reason"),
                    ),
                )
                maps["customers"][customer["id"]] = cursor.lastrowid
                stats["customers"] += 1

            for batch in payload.get("batches", []):
                product_id = maps["products"].get(batch.get("product_id"))
                if not product_id:
                    raise ValueError(f"批次#{batch.get('id')} 缺少对应机型")
                price_cents = batch.get("purchase_price_cents")
                if price_cents is None:
                    price_cents = yuan_to_cents(batch.get("purchase_price", 0))
                cursor = conn.execute(
                    "INSERT INTO batches(product_id,purchase_price,purchase_price_cents,"
                    "quantity,remaining,date,remark,supplier_id,sn_list,deleted_at,deleted_reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        product_id,
                        price_cents / 100,
                        price_cents,
                        batch.get("quantity", 0),
                        batch.get("remaining", 0),
                        batch.get("date", ""),
                        batch.get("remark", ""),
                        maps["suppliers"].get(batch.get("supplier_id")),
                        batch.get("sn_list", ""),
                        batch.get("deleted_at"),
                        batch.get("deleted_reason"),
                    ),
                )
                maps["batches"][batch["id"]] = cursor.lastrowid
                stats["batches"] += 1

            for quote in payload.get("quotes", []):
                batch_id = maps["batches"].get(quote.get("batch_id"))
                if not batch_id:
                    raise ValueError(f"报价#{quote.get('id')} 缺少对应批次")
                price_cents = quote.get("quote_price_cents")
                received_cents = quote.get("received_amount_cents")
                if price_cents is None:
                    price_cents = yuan_to_cents(quote.get("quote_price", 0))
                if received_cents is None:
                    received_cents = yuan_to_cents(quote.get("received_amount", 0))
                cursor = conn.execute(
                    "INSERT INTO quotes(batch_id,customer_id,quote_price,quote_price_cents,"
                    "quote_quantity,quote_date,remark,paid,status,received_amount,"
                    "received_amount_cents,sn_list,tax_rate,purchase_tax_inclusive,"
                    "quote_tax_inclusive,deleted_at,deleted_reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch_id,
                        maps["customers"].get(quote.get("customer_id")),
                        price_cents / 100,
                        price_cents,
                        quote.get("quote_quantity", 1),
                        quote.get("quote_date", ""),
                        quote.get("remark", ""),
                        quote.get("paid", "否"),
                        quote.get("status", "待确认"),
                        received_cents / 100,
                        received_cents,
                        quote.get("sn_list", ""),
                        quote.get("tax_rate"),
                        quote.get("purchase_tax_inclusive", 0),
                        quote.get("quote_tax_inclusive", 0),
                        quote.get("deleted_at"),
                        quote.get("deleted_reason"),
                    ),
                )
                maps["quotes"][quote["id"]] = cursor.lastrowid
                stats["quotes"] += 1

            payment_documents = payload.get("payments", [])
            for payment in payment_documents:
                amount_cents = payment.get("amount_cents")
                if amount_cents is None:
                    amount_cents = yuan_to_cents(payment.get("amount", 0))
                cursor = conn.execute(
                    "INSERT INTO payments(quote_id,customer_id,supplier_id,type,amount,"
                    "amount_cents,entry_kind,pay_date,method,remark) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        maps["quotes"].get(payment.get("quote_id")),
                        maps["customers"].get(payment.get("customer_id")),
                        maps["suppliers"].get(payment.get("supplier_id")),
                        payment.get("type", "receivable"),
                        amount_cents / 100,
                        amount_cents,
                        payment.get("entry_kind", "payment"),
                        payment.get("pay_date", ""),
                        payment.get("method", ""),
                        payment.get("remark", ""),
                    ),
                )
                maps["payments"][payment["id"]] = cursor.lastrowid
                stats["payments"] += 1

            for payment in payment_documents:
                conn.execute(
                    "UPDATE payments SET reversal_of_id=?,supersedes_id=? WHERE id=?",
                    (
                        maps["payments"].get(payment.get("reversal_of_id")),
                        maps["payments"].get(payment.get("supersedes_id")),
                        maps["payments"][payment["id"]],
                    ),
                )

            allocations = payload.get("payment_allocations", [])
            for allocation in allocations:
                payment_id = maps["payments"].get(allocation.get("payment_id"))
                quote_id = maps["quotes"].get(allocation.get("quote_id"))
                if not payment_id or not quote_id:
                    raise ValueError("收款分配引用了不存在的流水或报价")
                conn.execute(
                    "INSERT INTO payment_allocations(payment_id,quote_id,amount_cents) "
                    "VALUES (?,?,?)",
                    (payment_id, quote_id, allocation.get("amount_cents", 0)),
                )

            entity_maps = {
                "products": maps["products"],
                "batches": maps["batches"],
                "customers": maps["customers"],
                "suppliers": maps["suppliers"],
                "quotes": maps["quotes"],
                "payments": maps["payments"],
            }
            for event in payload.get("audit_events", []):
                entity_id = entity_maps.get(event.get("entity_type"), {}).get(
                    event.get("entity_id")
                )
                conn.execute(
                    "INSERT INTO audit_events(entity_type,entity_id,action,before_json,"
                    "after_json,reason,created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        event.get("entity_type", ""),
                        entity_id,
                        event.get("action", ""),
                        event.get("before_json"),
                        event.get("after_json"),
                        event.get("reason", ""),
                        event.get("created_at"),
                    ),
                )

            conn.commit()
            return True, "导入成功", stats
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except FileNotFoundError:
        return False, "文件不存在", {}
    except json.JSONDecodeError:
        return False, "文件格式错误，不是有效的JSON文件", {}
    except Exception as e:
        return False, f"导入失败: {str(e)}", {}


def _import_product(product, db_module):
    """导入机型"""
    try:
        pid = db_module.add_product(
            series=product.get("series", ""),
            cpu=product.get("cpu", ""),
            ram=product.get("ram", ""),
            storage=product.get("storage", ""),
            gpu=product.get("gpu", ""),
            screen=product.get("screen", ""),
            note=product.get("note", ""),
        )
        return pid
    except:
        return None


def _import_supplier(supplier, db_module):
    """导入上游"""
    try:
        sid = db_module.add_supplier(
            name=supplier.get("name", ""),
            wechat=supplier.get("wechat", ""),
            qq=supplier.get("qq", ""),
            phone=supplier.get("phone", ""),
            note=supplier.get("note", ""),
        )
        return sid
    except:
        return None


def _import_batch(batch, product_id, supplier_id, db_module):
    """导入批次"""
    try:
        bid = db_module.add_batch(
            product_id=product_id,
            purchase_price=batch.get("purchase_price", 0),
            quantity=batch.get("quantity", 0),
            remaining=batch.get("remaining", 0),
            date_str=batch.get("date", ""),
            remark=batch.get("remark", ""),
            supplier_id=supplier_id,
            sn_list=batch.get("sn_list", ""),
        )
        return bid
    except:
        return None


def _import_customer(customer, db_module):
    """导入客户"""
    try:
        cid = db_module.add_customer(
            name=customer.get("name", ""),
            wechat=customer.get("wechat", ""),
            qq=customer.get("qq", ""),
            phone=customer.get("phone", ""),
            note=customer.get("note", ""),
        )
        return cid
    except:
        return None


def _import_quote(quote, batch_id, customer_id, db_module):
    """导入报价"""
    try:
        qid = db_module.add_quote(
            batch_id=batch_id,
            customer_id=customer_id,
            quote_price=quote.get("quote_price", 0),
            quote_quantity=quote.get("quote_quantity", 1),
            quote_date=quote.get("quote_date", ""),
            remark=quote.get("remark", ""),
            paid=quote.get("paid", ""),
            status=quote.get("status", "待确认"),
            received_amount=quote.get("received_amount", 0),
        )
        return qid
    except:
        return None


def _import_payment(payment, quote_id, customer_id, supplier_id, db_module):
    """导入收付款记录"""
    try:
        db_module.add_payment(
            quote_id=quote_id,
            customer_id=customer_id,
            supplier_id=supplier_id,
            pay_type=payment.get("type", "receivable"),
            amount=payment.get("amount", 0),
            pay_date=payment.get("pay_date", ""),
            method=payment.get("method", ""),
            remark=payment.get("remark", ""),
        )
    except:
        pass


def validate_json_file(json_path):
    """验证 JSON 文件格式"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "version" not in data or "data" not in data:
            return False, "无效的备份文件格式"
        
        stats = {
            "products": len(data["data"].get("products", [])),
            "suppliers": len(data["data"].get("suppliers", [])),
            "batches": len(data["data"].get("batches", [])),
            "customers": len(data["data"].get("customers", [])),
            "quotes": len(data["data"].get("quotes", [])),
            "payments": len(data["data"].get("payments", [])),
        }
        
        return True, "有效的备份文件", stats
    
    except FileNotFoundError:
        return False, "文件不存在", {}
    except json.JSONDecodeError:
        return False, "文件格式错误", {}
    except Exception as e:
        return False, f"验证失败: {str(e)}", {}
