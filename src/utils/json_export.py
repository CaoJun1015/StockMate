"""Versioned JSON backup import/export using canonical integer-cent fields."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.connection import transaction
from src.models.queries import export_backup_data
from src.models.repositories import import_record, update_import_payment_links
from src.utils.money import yuan_to_cents
from src.version import APP_VERSION


def export_all_to_json(output_path=None, db_path=None):
    if not output_path:
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = desktop / f"调货助手备份_{timestamp}.json"
    output_path = Path(output_path)
    document = {
        "version": f"v{APP_VERSION}",
        "schema_version": 3,
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "money_unit": "cents",
        "data": export_backup_data(db_path),
    }
    with output_path.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
    return str(output_path)


def _money_cents(record: dict[str, Any], cents_key: str, yuan_key: str) -> int:
    value = record.get(cents_key)
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{cents_key} 必须是整数分")
        return value
    return yuan_to_cents(record.get(yuan_key, 0), strict=True)


def _remap(value, mapping):
    return mapping.get(value) if value is not None else None


def import_from_json(json_path, db_path=None):
    try:
        with Path(json_path).open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if "version" not in document or not isinstance(document.get("data"), dict):
            return False, "无效的备份文件格式", {}

        payload = document["data"]
        tables = ("products", "suppliers", "customers", "batches", "quotes", "payments")
        stats = {table: 0 for table in tables}
        maps: dict[str, dict[int, int]] = {table: {} for table in tables}

        with transaction(db_path, immediate=True) as conn:
            for product in payload.get("products", []):
                new_id = import_record(conn, "products", dict(product))
                maps["products"][product["id"]] = new_id
                stats["products"] += 1

            for supplier in payload.get("suppliers", []):
                row = dict(supplier)
                row["balance_cents"] = _money_cents(row, "balance_cents", "balance")
                new_id = import_record(conn, "suppliers", row)
                maps["suppliers"][supplier["id"]] = new_id
                stats["suppliers"] += 1

            for customer in payload.get("customers", []):
                row = dict(customer)
                row["balance_cents"] = _money_cents(row, "balance_cents", "balance")
                new_id = import_record(conn, "customers", row)
                maps["customers"][customer["id"]] = new_id
                stats["customers"] += 1

            for batch in payload.get("batches", []):
                row = dict(batch)
                row["product_id"] = _remap(row.get("product_id"), maps["products"])
                row["supplier_id"] = _remap(row.get("supplier_id"), maps["suppliers"])
                row["purchase_price_cents"] = _money_cents(
                    row, "purchase_price_cents", "purchase_price"
                )
                if row["product_id"] is None:
                    raise ValueError(f"批次#{batch.get('id')} 缺少对应机型")
                new_id = import_record(conn, "batches", row)
                maps["batches"][batch["id"]] = new_id
                stats["batches"] += 1

            for quote in payload.get("quotes", []):
                row = dict(quote)
                row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
                row["customer_id"] = _remap(row.get("customer_id"), maps["customers"])
                row["quote_price_cents"] = _money_cents(
                    row, "quote_price_cents", "quote_price"
                )
                row["received_amount_cents"] = _money_cents(
                    row, "received_amount_cents", "received_amount"
                )
                if row["batch_id"] is None:
                    raise ValueError(f"报价#{quote.get('id')} 缺少对应批次")
                new_id = import_record(conn, "quotes", row)
                maps["quotes"][quote["id"]] = new_id
                stats["quotes"] += 1

            pending_links: list[tuple[int, int | None, int | None]] = []
            for payment in payload.get("payments", []):
                row = dict(payment)
                old_reversal = row.pop("reversal_of_id", None)
                old_supersedes = row.pop("supersedes_id", None)
                row["quote_id"] = _remap(row.get("quote_id"), maps["quotes"])
                row["customer_id"] = _remap(row.get("customer_id"), maps["customers"])
                row["supplier_id"] = _remap(row.get("supplier_id"), maps["suppliers"])
                row["amount_cents"] = _money_cents(row, "amount_cents", "amount")
                new_id = import_record(conn, "payments", row)
                maps["payments"][payment["id"]] = new_id
                pending_links.append((new_id, old_reversal, old_supersedes))
                stats["payments"] += 1

            for payment_id, old_reversal, old_supersedes in pending_links:
                update_import_payment_links(
                    conn,
                    payment_id,
                    reversal_of_id=_remap(old_reversal, maps["payments"]),
                    supersedes_id=_remap(old_supersedes, maps["payments"]),
                )

            for allocation in payload.get("payment_allocations", []):
                row = dict(allocation)
                row["payment_id"] = _remap(row.get("payment_id"), maps["payments"])
                row["quote_id"] = _remap(row.get("quote_id"), maps["quotes"])
                if row["payment_id"] is None or row["quote_id"] is None:
                    raise ValueError(f"付款分配#{allocation.get('id')} 关联记录缺失")
                import_record(conn, "payment_allocations", row)

            for event in payload.get("audit_events", []):
                row = dict(event)
                entity_map = maps.get(row.get("entity_type"))
                if entity_map is not None:
                    row["entity_id"] = _remap(row.get("entity_id"), entity_map)
                import_record(conn, "audit_events", row)

        return True, "导入成功", stats
    except FileNotFoundError:
        return False, "文件不存在", {}
    except json.JSONDecodeError:
        return False, "文件格式错误，不是有效的 JSON 文件", {}
    except Exception as exc:
        return False, f"导入失败: {exc}", {}


def validate_json_file(json_path):
    try:
        with Path(json_path).open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if "version" not in document or not isinstance(document.get("data"), dict):
            return False, "无效的备份文件格式", {}
        payload = document["data"]
        stats = {
            table: len(payload.get(table, []))
            for table in ("products", "suppliers", "batches", "customers", "quotes", "payments")
        }
        return True, "有效的备份文件", stats
    except FileNotFoundError:
        return False, "文件不存在", {}
    except json.JSONDecodeError:
        return False, "文件格式错误", {}
    except Exception as exc:
        return False, f"验证失败: {exc}", {}
