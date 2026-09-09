"""v1.14 boundary, reconciliation, and restore tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.models.connection import (
    DatabaseRestoreError,
    connect,
    create_backup,
)
from src.services.database_service import restore_database
from src.models.migrations import migrate_database
from src.models.queries import get_customer, get_supplier
from src.services.exceptions import InvalidTransitionError
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService


ROOT = Path(__file__).parents[1]


def _attribute_calls(path: Path, attribute: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


def test_ui_has_no_direct_sql_or_connection_access():
    ui_files = list((ROOT / "src" / "ui").glob("*.py")) + [ROOT / "src" / "main.py"]
    violations = []
    for path in ui_files:
        source = path.read_text(encoding="utf-8-sig")
        if "src.models.database" in source:
            violations.append(f"{path.name}: compatibility facade import")
        if "get_connection" in source:
            violations.append(f"{path.name}: get_connection")
        for line in _attribute_calls(path, "execute"):
            violations.append(f"{path.name}:{line} execute")
    assert violations == []


def test_services_use_repositories_instead_of_sql():
    violations = []
    for path in (ROOT / "src" / "services").glob("*.py"):
        for line in _attribute_calls(path, "execute"):
            violations.append(f"{path.name}:{line}")
    assert violations == []


def test_party_services_write_and_queries_read(tmp_path):
    db_path = tmp_path / "parties.db"
    migrate_database(db_path)

    customer_id = CustomerService(db_path).create(
        name="  测试客户  ",
        wechat="wx-old",
        default_tax_rate=0.13,
    )
    CustomerService(db_path).update(
        customer_id,
        name="测试客户新名",
        wechat="wx-new",
        note="重点客户",
        default_tax_rate=0.06,
    )
    customer = get_customer(customer_id, db_path)
    assert customer["name"] == "测试客户新名"
    assert customer["wechat"] == "wx-new"
    assert customer["default_tax_rate"] == pytest.approx(0.06)

    supplier_id = SupplierService(db_path).create(name="测试上游", phone="13800000000")
    SupplierService(db_path).update(
        supplier_id,
        name="测试上游新名",
        phone="13900000000",
        note="长期合作",
    )
    supplier = get_supplier(supplier_id, db_path)
    assert supplier["name"] == "测试上游新名"
    assert supplier["note"] == "长期合作"

    CustomerService(db_path).delete(customer_id)
    SupplierService(db_path).delete(supplier_id)
    assert get_customer(customer_id, db_path) is None
    assert get_supplier(supplier_id, db_path) is None


def _seed_reconciled_flow(db_path: Path):
    migrate_database(db_path)
    conn = connect(db_path)
    try:
        product_id = conn.execute(
            "INSERT INTO products(series) VALUES ('Y9000P')"
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    customer_id = CustomerService(db_path).create(name="对账客户")
    supplier_id = SupplierService(db_path).create(name="对账上游")
    account_id = FinanceService(db_path).initialize_finance(
        "2026-08-04",
        [{"name": "测试账户", "opening_balance_cents": 0}],
    )[0]
    batch_id = InventoryService(db_path).receive_batch(
        product_id=product_id,
        purchase_price_cents=500_000,
        quantity=2,
        date="2026-08-04",
        supplier_id=supplier_id,
    )
    quote_id = OrderService(db_path).create_quote(
        batch_id=batch_id,
        customer_id=customer_id,
        quote_price_cents=550_000,
        quote_quantity=1,
        quote_date="2026-08-04",
    )
    OrderService(db_path).transition(quote_id, "已报价")
    InventoryService(db_path).ship_quote(quote_id, "SN-V114")
    PaymentService(db_path).receive_customer_payment(
        customer_id,
        550_000,
        "2026-08-04",
        "转账",
        account_id=account_id,
    )
    PaymentService(db_path).record_supplier_payment(
        supplier_id,
        1_000_000,
        "2026-08-04",
        "转账",
        account_id=account_id,
    )
    return batch_id


def test_automatic_reconciliation_passes_consistent_flow(tmp_path):
    db_path = tmp_path / "reconciled.db"
    _seed_reconciled_flow(db_path)
    report = ReconciliationService(db_path).run()
    assert report.is_clean, report.format_text()
    assert report.metrics["table_counts"]["payments"] == 2


def test_automatic_reconciliation_detects_inventory_conflict(tmp_path):
    db_path = tmp_path / "inventory-conflict.db"
    batch_id = _seed_reconciled_flow(db_path)
    conn = connect(db_path)
    try:
        conn.execute("UPDATE batches SET remaining=quantity WHERE id=?", (batch_id,))
        conn.commit()
    finally:
        conn.close()
    report = ReconciliationService(db_path).run()
    assert not report.is_clean
    assert any(issue.code == "INVENTORY_BALANCE" for issue in report.issues)


def test_verified_backup_restore_preserves_safety_copy(tmp_path):
    db_path = tmp_path / "restore.db"
    migrate_database(db_path)
    conn = connect(db_path)
    conn.execute("INSERT INTO products(series) VALUES ('备份内机型')")
    conn.commit()
    conn.close()
    backup = create_backup(db_path, prefix="manual_test", retain=False)
    assert backup is not None

    conn = connect(db_path)
    conn.execute("INSERT INTO products(series) VALUES ('恢复前新增机型')")
    conn.commit()
    conn.close()

    result = restore_database(backup.path, db_path)
    assert result.safety_backup is not None
    assert result.safety_backup.path.exists()

    conn = connect(db_path, read_only=True)
    try:
        names = [row[0] for row in conn.execute("SELECT series FROM products ORDER BY id")]
        assert names == ["备份内机型"]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()

    safety = connect(result.safety_backup.path, read_only=True)
    try:
        assert safety.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
    finally:
        safety.close()


def test_invalid_restore_source_does_not_touch_current_database(tmp_path):
    db_path = tmp_path / "current.db"
    migrate_database(db_path)
    conn = connect(db_path)
    conn.execute("INSERT INTO products(series) VALUES ('当前数据')")
    conn.commit()
    conn.close()
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"not a sqlite database")

    with pytest.raises(DatabaseRestoreError):
        restore_database(invalid, db_path)

    conn = connect(db_path, read_only=True)
    try:
        assert conn.execute("SELECT series FROM products").fetchone()[0] == "当前数据"
    finally:
        conn.close()


def test_future_version_backup_is_rejected_before_safety_backup(tmp_path):
    db_path = tmp_path / "current.db"
    backup_path = tmp_path / "future.db"
    migrate_database(db_path)
    migrate_database(backup_path)
    conn = connect(backup_path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseRestoreError, match="高于程序支持"):
        restore_database(backup_path, db_path)

    assert not (tmp_path / "backup").exists()


def test_shipped_history_blocks_product_and_batch_deletion(tmp_path):
    db_path = tmp_path / "delete-guard.db"
    batch_id = _seed_reconciled_flow(db_path)
    conn = connect(db_path, read_only=True)
    try:
        product_id = conn.execute(
            "SELECT product_id FROM batches WHERE id=?",
            (batch_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    with pytest.raises(InvalidTransitionError, match="不能删除"):
        InventoryService(db_path).delete_batch(batch_id)
    with pytest.raises(InvalidTransitionError, match="不能删除"):
        ProductService(db_path).delete(product_id)

    conn = connect(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT deleted_at FROM batches WHERE id=?",
            (batch_id,),
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT deleted_at FROM products WHERE id=?",
            (product_id,),
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_finance_enabled_batch_delete_requires_purchase_return(tmp_path):
    db_path = tmp_path / "draft-delete.db"
    migrate_database(db_path)
    product_id = ProductService(db_path).create(series="可删除机型")
    supplier_id = SupplierService(db_path).create(name="可删除上游")
    customer_id = CustomerService(db_path).create(name="草稿客户")
    FinanceService(db_path).initialize_finance(
        "2026-08-04",
        [{"name": "测试账户", "opening_balance_cents": 0}],
    )
    batch_id = InventoryService(db_path).receive_batch(
        product_id=product_id,
        purchase_price_cents=300_000,
        quantity=2,
        date="2026-08-04",
        supplier_id=supplier_id,
    )
    quote_id = OrderService(db_path).create_quote(
        batch_id=batch_id,
        customer_id=customer_id,
        quote_price_cents=350_000,
        quote_quantity=1,
        quote_date="2026-08-04",
    )

    with pytest.raises(InvalidTransitionError, match="采购退货"):
        InventoryService(db_path).delete_batch(batch_id)

    conn = connect(db_path, read_only=True)
    try:
        assert conn.execute(
            "SELECT deleted_at FROM batches WHERE id=?",
            (batch_id,),
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT deleted_at FROM quotes WHERE id=?",
            (quote_id,),
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT balance_cents FROM suppliers WHERE id=?",
            (supplier_id,),
        ).fetchone()[0] == 600_000
    finally:
        conn.close()
