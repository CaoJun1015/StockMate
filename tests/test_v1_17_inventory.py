"""v1.17 inventory subledger, allocation, migration, and return tests."""

from __future__ import annotations

from src.models.connection import connect
from src.models.migrations import migrate_database
from src.models.queries import list_shipment_allocations
from src.services.exceptions import ValidationError
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService
from src.services.return_service import ReturnService


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
