"""Versioned JSON backup import/export using canonical integer-cent fields."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.finance_repository import (
    clear_import_defaults,
    update_import_finance_settings,
    update_import_ledger_links,
)
from src.models.queries import export_backup_data, BACKUP_TABLES
from src.models.schema import SCHEMA_VERSION
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
        "schema_version": SCHEMA_VERSION,
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
    if value is None:
        return None
    if value not in mapping:
        raise ValueError(f"关联记录缺失：#{value}")
    return mapping[value]


def read_json_backup(json_path):
    with Path(json_path).open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict) or "version" not in document or not isinstance(document.get("data"), dict):
        raise ValueError("无效的备份文件格式")
    version = document.get("schema_version", 0)
    if type(version) is not int or version < 0 or version > SCHEMA_VERSION:
        raise ValueError("备份版本高于程序支持的版本或版本格式无效")
    required = {"products", "suppliers", "customers", "batches", "quotes", "payments"}
    if version >= 4:
        required = set(BACKUP_TABLES) - {"operation_logs", "price_snapshots", "price_snapshot_items"}
        if version < 5:
            required -= {"shipment_allocations", "inventory_movements"}
        if version < 6:
            required -= {"sales_return_allocations"}
        if version < 7:
            required -= {"payment_refund_allocations"}
    missing = required - document["data"].keys()
    if missing:
        raise ValueError("备份缺少必要数据表：" + "、".join(sorted(missing)))
    if version >= 4 and (len(document["data"]["finance_settings"]) != 1
                         or document["data"]["finance_settings"][0].get("id") != 1):
        raise ValueError("备份缺少有效财务设置")
    for table, rows in document["data"].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{table} 必须是记录列表")
        ids = [row.get("id") for row in rows]
        if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"{table} 包含无效或重复记录 ID")
    return document


def populate_json_database(conn, document):
    """Populate an empty database; transaction and validation belong to the service."""
    payload = document["data"]
    tables = ("products", "suppliers", "customers", "batches", "quotes", "payments")
    stats = {table: 0 for table in tables}
    map_tables = (
        *tables, "ledger_accounts", "finance_categories", "ledger_entries",
        "shipment_snapshots", "shipment_allocations", "sales_returns", "purchase_returns",
        "payment_refund_allocations",
    )
    maps: dict[str, dict[int, int]] = {table: {} for table in map_tables}

    clear_import_defaults(conn, accounts="ledger_accounts" in payload,
                          categories="finance_categories" in payload)
    for account in payload.get("ledger_accounts", []):
        new_id = import_record(conn, "ledger_accounts", dict(account))
        maps["ledger_accounts"][account["id"]] = new_id
    for category in payload.get("finance_categories", []):
        new_id = import_record(conn, "finance_categories", dict(category))
        maps["finance_categories"][category["id"]] = new_id

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
        row["account_id"] = _remap(
            row.get("account_id"), maps["ledger_accounts"]
        )
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

    pending_entry_links: list[tuple[int, int | None, int | None]] = []
    for entry in payload.get("ledger_entries", []):
        row = dict(entry)
        old_reversal = row.pop("reversal_of_id", None)
        old_supersedes = row.pop("supersedes_id", None)
        new_id = import_record(conn, "ledger_entries", row)
        maps["ledger_entries"][entry["id"]] = new_id
        pending_entry_links.append((new_id, old_reversal, old_supersedes))
    for entry_id, old_reversal, old_supersedes in pending_entry_links:
        update_import_ledger_links(
            conn,
            entry_id,
            reversal_of_id=_remap(
                old_reversal,
                maps["ledger_entries"],
            ),
            supersedes_id=_remap(
                old_supersedes,
                maps["ledger_entries"],
            ),
        )

    for line in payload.get("ledger_lines", []):
        row = dict(line)
        row["entry_id"] = _remap(
            row.get("entry_id"), maps["ledger_entries"]
        )
        row["account_id"] = _remap(
            row.get("account_id"), maps["ledger_accounts"]
        )
        row["customer_id"] = _remap(
            row.get("customer_id"), maps["customers"]
        )
        row["supplier_id"] = _remap(
            row.get("supplier_id"), maps["suppliers"]
        )
        row["quote_id"] = _remap(row.get("quote_id"), maps["quotes"])
        row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
        row["category_id"] = _remap(
            row.get("category_id"), maps["finance_categories"]
        )
        import_record(conn, "ledger_lines", row)

    for snapshot in payload.get("shipment_snapshots", []):
        row = dict(snapshot)
        row["quote_id"] = _remap(row.get("quote_id"), maps["quotes"])
        row["ledger_entry_id"] = _remap(
            row.get("ledger_entry_id"), maps["ledger_entries"]
        )
        new_id = import_record(conn, "shipment_snapshots", row)
        maps["shipment_snapshots"][snapshot["id"]] = new_id

    for allocation in payload.get("shipment_allocations", []):
        row = dict(allocation)
        row["shipment_snapshot_id"] = _remap(
            row.get("shipment_snapshot_id"), maps["shipment_snapshots"]
        )
        row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
        new_id = import_record(conn, "shipment_allocations", row)
        maps["shipment_allocations"][allocation["id"]] = new_id

    for allocation in payload.get("supplier_payment_allocations", []):
        row = dict(allocation)
        row["payment_id"] = _remap(
            row.get("payment_id"), maps["payments"]
        )
        row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
        import_record(conn, "supplier_payment_allocations", row)

    for record in payload.get("sales_returns", []):
        row = dict(record)
        row["quote_id"] = _remap(row.get("quote_id"), maps["quotes"])
        row["account_id"] = _remap(
            row.get("account_id"), maps["ledger_accounts"]
        )
        row["ledger_entry_id"] = _remap(
            row.get("ledger_entry_id"), maps["ledger_entries"]
        )
        new_id = import_record(conn, "sales_returns", row)
        maps["sales_returns"][record["id"]] = new_id

    for record in payload.get("purchase_returns", []):
        row = dict(record)
        row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
        row["supplier_id"] = _remap(
            row.get("supplier_id"), maps["suppliers"]
        )
        row["account_id"] = _remap(
            row.get("account_id"), maps["ledger_accounts"]
        )
        row["ledger_entry_id"] = _remap(
            row.get("ledger_entry_id"), maps["ledger_entries"]
        )
        new_id = import_record(conn, "purchase_returns", row)
        maps["purchase_returns"][record["id"]] = new_id

    for allocation in payload.get("payment_refund_allocations", []):
        row = dict(allocation)
        row["payment_id"] = _remap(row.get("payment_id"), maps["payments"])
        row["sales_return_id"] = _remap(
            row.get("sales_return_id"), maps["sales_returns"]
        )
        row["purchase_return_id"] = _remap(
            row.get("purchase_return_id"), maps["purchase_returns"]
        )
        import_record(conn, "payment_refund_allocations", row)

    for allocation in payload.get("sales_return_allocations", []):
        row = dict(allocation)
        row["sales_return_id"] = _remap(row.get("sales_return_id"), maps["sales_returns"])
        row["shipment_allocation_id"] = _remap(
            row.get("shipment_allocation_id"), maps["shipment_allocations"]
        )
        import_record(conn, "sales_return_allocations", row)

    for movement in payload.get("inventory_movements", []):
        row = dict(movement)
        row["product_id"] = _remap(row.get("product_id"), maps["products"])
        row["batch_id"] = _remap(row.get("batch_id"), maps["batches"])
        row["shipment_allocation_id"] = _remap(
            row.get("shipment_allocation_id"), maps["shipment_allocations"]
        )
        row["ledger_entry_id"] = _remap(
            row.get("ledger_entry_id"), maps["ledger_entries"]
        )
        source_table = {
            "batch": "batches",
            "quote": "quotes",
            "sales_return": "sales_returns",
            "purchase_return": "purchase_returns",
        }.get(row.get("source_type"), row.get("source_type"))
        source_map = maps.get(source_table)
        if source_map is not None and row.get("source_id") is not None:
            try:
                old_source_id = int(row["source_id"])
            except (TypeError, ValueError):
                old_source_id = None
            if old_source_id is not None:
                mapped_source = _remap(old_source_id, source_map)
                row["source_id"] = (
                    str(mapped_source) if mapped_source is not None else None
                )
        # Full restore preserves IDs and event keys; repeated restores do not invent events.
        import_record(conn, "inventory_movements", row)

    settings = payload.get("finance_settings", [])
    if settings:
        setting = settings[0]
        update_import_finance_settings(
            conn,
            enabled_at=setting.get("enabled_at"),
            initialized_at=setting.get("initialized_at"),
            created_at=setting.get("created_at"),
        )

    for event in payload.get("audit_events", []):
        row = dict(event)
        entity_map = maps.get(row.get("entity_type"))
        if entity_map is not None:
            row["entity_id"] = _remap(row.get("entity_id"), entity_map)
        if row.get("entity_type") == "sales_returns" and row.get("after_json"):
            detail = json.loads(row["after_json"])
            for item in detail.get("allocations", []):
                item["shipment_allocation_id"] = _remap(
                    item.get("shipment_allocation_id"), maps["shipment_allocations"]
                )
                item["batch_id"] = _remap(item.get("batch_id"), maps["batches"])
            row["after_json"] = json.dumps(detail, ensure_ascii=False)
        import_record(conn, "audit_events", row)

    for table in ("operation_logs", "price_snapshots", "price_snapshot_items"):
        for record in payload.get(table, []):
            import_record(conn, table, dict(record))
    return stats


def import_from_json(json_path, db_path=None):
    """Compatibility API for explicit, empty staging databases only."""
    from src.services.database_service import import_json_into_empty_database
    try:
        stats = import_json_into_empty_database(json_path, db_path)
        return True, "导入成功", stats
    except Exception as exc:
        return False, f"导入失败: {exc}", {}


def validate_json_file(json_path):
    try:
        document = read_json_backup(json_path)
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
