"""v1.17 inventory subledger, allocation, migration, and return tests."""

from __future__ import annotations

import json

import pytest

from src.models.connection import connect
from src.models.migrations import migrate_database
from src.models.queries import list_shipment_allocations, export_backup_data
from src.services.exceptions import ValidationError, DataConflictError
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService
from src.services.return_service import ReturnService
from src.utils.json_export import export_all_to_json, import_from_json


def _flow(path):
    migrate_database(path)
    product = ProductService(path).create(series="跨批次测试", cpu="i7")
    customer = CustomerService(path).create(name="客户")
    supplier = SupplierService(path).create(name="供应商")
    FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "微信", "opening_balance_cents": 0}]
    )
    first = InventoryService(path).receive_batch(
        product_id=product, purchase_price_cents=300_000, quantity=2,
        date="2026-08-01", supplier_id=supplier, sn_list="SN0001,SN0002",
    )
    second = InventoryService(path).receive_batch(
        product_id=product, purchase_price_cents=320_000, quantity=2,
        date="2026-08-02", supplier_id=supplier, sn_list="SN0003,SN0004",
    )
    quote = OrderService(path).create_quote(
        batch_id=first, customer_id=customer, quote_price_cents=400_000,
        quote_quantity=3, quote_date="2026-08-03",
    )
    return product, first, second, quote


def test_cross_batch_shipment_has_exact_cost_and_movements(tmp_path):
    path = tmp_path / "cross.db"
    _, first, second, quote = _flow(path)
    InventoryService(path).ship_quote(
        quote,
        shipped_date="2026-08-04",
        allocations=[
            {"batch_id": first, "quantity": 2, "sn_list": "SN0001,SN0002"},
            {"batch_id": second, "quantity": 1, "sn_list": "SN0003"},
        ],
    )
    conn = connect(path, read_only=True)
    try:
        snapshot = conn.execute(
            "SELECT * FROM shipment_snapshots WHERE quote_id=?", (quote,)
        ).fetchone()
        assert snapshot["cost_cents"] == 920_000
        assert snapshot["unit_cost_cents"] is None
        assert conn.execute(
            "SELECT SUM(cost_cents) FROM shipment_allocations WHERE shipment_snapshot_id=?",
            (snapshot["id"],),
        ).fetchone()[0] == 920_000
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (first,)).fetchone()[0] == 0
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (second,)).fetchone()[0] == 1
    finally:
        conn.close()
    assert ReconciliationService(path).run().is_clean


def test_invalid_allocation_rolls_back_everything(tmp_path):
    path = tmp_path / "rollback.db"
    _, first, second, quote = _flow(path)
    try:
        InventoryService(path).ship_quote(
            quote,
            allocations=[
                {"batch_id": first, "quantity": 1},
                {"batch_id": second, "quantity": 1},
            ],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid allocation total should fail")
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (first,)).fetchone()[0] == 2
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (second,)).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM shipment_snapshots").fetchone()[0] == 0
    finally:
        conn.close()


def test_cross_batch_return_restocks_original_allocation(tmp_path):
    path = tmp_path / "return.db"
    _, first, second, quote = _flow(path)
    InventoryService(path).ship_quote(
        quote,
        allocations=[
            {"batch_id": first, "quantity": 2, "sn_list": "SN0001,SN0002"},
            {"batch_id": second, "quantity": 1, "sn_list": "SN0003"},
        ],
    )
    allocations = list_shipment_allocations(quote, path)
    second_allocation = next(row for row in allocations if row["batch_id"] == second)
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-05", restock=True,
        reason="测试退货",
        restock_allocations=[{
            "shipment_allocation_id": second_allocation["id"],
            "quantity": 1,
            "sn_list": "SN0003",
        }],
    )
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (first,)).fetchone()[0] == 0
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (second,)).fetchone()[0] == 2
        movement = conn.execute(
            """SELECT * FROM inventory_movements
               WHERE movement_type='sales_return'"""
        ).fetchone()
        assert movement["batch_id"] == second
        assert movement["unit_cost_cents"] == 320_000
    finally:
        conn.close()
    assert ReconciliationService(path).run().is_clean


def test_v4_gap_becomes_explicit_migration_adjustment(tmp_path):
    path = tmp_path / "legacy-gap.db"
    migrate_database(path)
    conn = connect(path)
    try:
        product = conn.execute("INSERT INTO products(series) VALUES ('旧库存')").lastrowid
        batch = conn.execute(
            """INSERT INTO batches(product_id,purchase_price_cents,quantity,remaining,date)
               VALUES (?,?,?,?,?)""",
            (product, 100_000, 10, 4, "2026-01-01"),
        ).lastrowid
        conn.execute("DELETE FROM inventory_movements")
        conn.execute("DELETE FROM schema_migrations WHERE version=5")
        conn.execute("PRAGMA user_version=4")
        conn.commit()
    finally:
        conn.close()
    migrate_database(path)
    conn = connect(path, read_only=True)
    try:
        adjustment = conn.execute(
            """SELECT quantity_delta FROM inventory_movements
               WHERE batch_id=? AND movement_type='migration_adjustment'""",
            (batch,),
        ).fetchone()
        assert adjustment[0] == -6
        assert conn.execute(
            "SELECT SUM(quantity_delta) FROM inventory_movements WHERE batch_id=?",
            (batch,),
        ).fetchone()[0] == 4
    finally:
        conn.close()


# Regression coverage for return quantities and serialized stock ownership.


def _shipped_flow(path):
    _, first, second, quote = _flow(path)
    InventoryService(path).ship_quote(quote, allocations=[
        {"batch_id": first, "quantity": 2, "sn_list": "SN0001,SN0002"},
        {"batch_id": second, "quantity": 1, "sn_list": "SN0003"},
    ])
    return first, second, quote, list_shipment_allocations(quote, path)


def _return(path, quote, allocation, sns, *, quantity=1, restock=True):
    return ReturnService(path).return_sale(
        quote, quantity=quantity, return_date="2026-08-05", restock=restock,
        reason="regression", restock_allocations=[{
            "shipment_allocation_id": allocation, "quantity": quantity, "sn_list": sns,
        }],
    )


def test_resold_returned_sn_stays_occupied(tmp_path):
    path = tmp_path / "resale.db"
    first, _, quote, rows = _shipped_flow(path)
    _return(path, quote, rows[0]["id"], "SN0001,SN0002", quantity=2)
    customer = export_backup_data(path)["quotes"][0]["customer_id"]
    def new_quote():
        return OrderService(path).create_quote(
            batch_id=first, customer_id=customer, quote_price_cents=400_000,
            quote_quantity=1, quote_date="2026-08-06",
        )
    resale = new_quote()
    InventoryService(path).ship_quote(resale, "SN0001")
    duplicate = new_quote()
    before = export_backup_data(path)
    with pytest.raises(ValidationError, match="SN"):
        InventoryService(path).ship_quote(duplicate, "SN0001")
    assert export_backup_data(path) == before
    # The SN can legitimately be returned and resold for another cycle.
    _return(path, resale, list_shipment_allocations(resale, path)[0]["id"], "SN0001")
    InventoryService(path).ship_quote(duplicate, "SN0001")
    assert ReconciliationService(path).run().is_clean


@pytest.mark.parametrize("restock", [True, False])
def test_reject_previously_returned_sn_atomically(tmp_path, restock):
    path = tmp_path / "repeat.db"
    _, _, quote, rows = _shipped_flow(path)
    _return(path, quote, rows[0]["id"], "SN0001", restock=restock)
    before = export_backup_data(path)
    with pytest.raises(ValidationError, match="SN"):
        _return(path, quote, rows[0]["id"], "SN0001")
    assert export_backup_data(path) == before
    _return(path, quote, rows[0]["id"], "SN0002")
    assert ReconciliationService(path).run().is_clean


def test_reject_duplicate_sns_in_one_return(tmp_path):
    path = tmp_path / "same-request.db"
    _, _, quote, rows = _shipped_flow(path)
    before = export_backup_data(path)
    with pytest.raises(ValidationError, match="重复SN"):
        _return(path, quote, rows[0]["id"], "SN0001,SN0001", quantity=2)
    assert export_backup_data(path) == before


@pytest.mark.parametrize("restock", [True, False])
def test_nonrestock_return_consumes_allocation_allowance(tmp_path, restock):
    path = tmp_path / "allowance.db"
    _, _, quote, rows = _shipped_flow(path)
    _return(path, quote, rows[1]["id"], "", restock=False)
    assert list_shipment_allocations(quote, path)[1]["returned_quantity"] == 1
    before = export_backup_data(path)
    with pytest.raises(ValidationError, match="净出库数量"):
        _return(path, quote, rows[1]["id"], "", restock=restock)
    assert export_backup_data(path) == before
    _return(path, quote, rows[0]["id"], "SN0001")
    assert ReconciliationService(path).run().is_clean


@pytest.mark.parametrize("restock", [True, False])
def test_v5_migration_recovers_return_audit_and_is_idempotent(tmp_path, restock):
    path = tmp_path / "v5.db"
    _, _, quote, rows = _shipped_flow(path)
    _return(path, quote, rows[1]["id"], "SN0003", restock=restock)
    conn = connect(path)
    conn.execute("DROP TABLE sales_return_allocations")
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()
    migrate_database(path)
    before = export_backup_data(path)
    migrate_database(path)
    assert export_backup_data(path) == before
    assert list_shipment_allocations(quote, path)[1]["returned_quantity"] == 1
    with pytest.raises(ValidationError):
        _return(path, quote, rows[1]["id"], "SN0003")
    assert ReconciliationService(path).run().is_clean


@pytest.mark.parametrize("legacy", [False, True])
def test_json_restore_preserves_return_allocations_with_remapped_ids(tmp_path, legacy):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _, _, quote, rows = _shipped_flow(source)
    _return(source, quote, rows[1]["id"], "SN0003", restock=False)
    backup = tmp_path / "backup.json"
    export_all_to_json(backup, source)
    if legacy:
        doc = json.loads(backup.read_text(encoding="utf-8"))
        doc["schema_version"] = 5
        del doc["data"]["sales_return_allocations"]
        backup.write_text(json.dumps(doc), encoding="utf-8")
    migrate_database(target)
    # A fresh restored database may have previously consumed AUTOINCREMENT IDs.
    conn = connect(target)
    for table in ("products", "batches", "quotes", "shipment_allocations", "sales_returns"):
        conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,100)", (table,))
    conn.commit()
    conn.close()
    ok, message, _ = import_from_json(backup, target)
    assert ok, message
    conn = connect(target)
    imported_quote = conn.execute("SELECT MAX(id) FROM quotes").fetchone()[0]
    conn.close()
    allocations = list_shipment_allocations(imported_quote, target)
    assert allocations[1]["returned_quantity"] == 1
    with pytest.raises(ValidationError):
        _return(target, imported_quote, allocations[1]["id"], "SN0003")


def test_missing_legacy_cross_batch_return_history_blocks_further_returns(tmp_path):
    path = tmp_path / "missing-history.db"
    _, _, quote, rows = _shipped_flow(path)
    _return(path, quote, rows[1]["id"], "", restock=False)
    conn = connect(path)
    conn.execute("DELETE FROM sales_return_allocations")
    conn.execute("DELETE FROM audit_events WHERE entity_type='sales_returns'")
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()
    migrate_database(path)
    assert not ReconciliationService(path).run().is_clean
    with pytest.raises(DataConflictError, match="历史退货"):
        _return(path, quote, rows[0]["id"], "SN0001")
