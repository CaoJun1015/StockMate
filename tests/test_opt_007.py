"""Read-only serial-number lifecycle evidence."""

from __future__ import annotations

from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, get_sn_lifecycle, list_shipment_allocations
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.product_service import ProductService
from src.services.return_service import ReturnService
from src.ui.dialogs import SNLifecycleDialog


def _lifecycle_sale(path):
    migrate_database(path)
    FinanceService(path).initialize_finance("2026-09-01", [{"name": "现金", "opening_balance_cents": 0}])
    product = ProductService(path).create(series="生命周期机型")
    customer = CustomerService(path).create(name="生命周期客户")
    supplier = SupplierService(path).create(name="生命周期供应商")
    inventory = InventoryService(path)
    batch = inventory.receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=10_000,
        quantity=1, sn_list="LIFE-001", date="2026-09-01",
    )
    quote = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=20_000,
        quote_quantity=1, quote_date="2026-09-01",
    )
    inventory.ship_quote(quote, "LIFE-001", shipped_date="2026-09-02")
    return inventory, batch, quote, customer


def test_sn_lifecycle_tracks_restock_resale_and_stays_read_only(qapp, tmp_path):
    path = tmp_path / "lifecycle.db"
    inventory, batch, first_quote, customer = _lifecycle_sale(path)
    before = export_backup_data(path)
    first = get_sn_lifecycle("LIFE-001", path)
    assert export_backup_data(path) == before
    assert first["status"] == "已出库"
    assert [event["kind"] for event in first["events"]] == ["入库", "出库"]

    allocation = list_shipment_allocations(first_quote, path)[0]
    ReturnService(path).return_sale(
        first_quote, quantity=1, return_date="2026-09-03", restock=True,
        reason="第一次退货", restock_allocations=[{
            "shipment_allocation_id": allocation["id"], "quantity": 1, "sn_list": "LIFE-001",
        }],
    )
    resale = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=21_000,
        quote_quantity=1, quote_date="2026-09-04",
    )
    inventory.ship_quote(resale, "LIFE-001", shipped_date="2026-09-04")
    lifecycle = get_sn_lifecycle("LIFE-001", path)
    assert lifecycle["status"] == "已出库"
    assert [event["kind"] for event in lifecycle["events"]] == ["入库", "出库", "销售退货", "出库"]
    assert {event["quote_id"] for event in lifecycle["events"] if event["quote_id"]} == {first_quote, resale}
    assert lifecycle["events"] == sorted(
        lifecycle["events"], key=lambda item: (item["business_date"], item["record_time"] or "", item["sequence"], item["event_id"])
    )
    dialog = SNLifecycleDialog(db_path=path)
    dialog.sn_edit.setText("LIFE-001")
    dialog._query()
    assert dialog.event_table.rowCount() == 4
    assert "已出库" in dialog.status_label.text()


def test_sn_lifecycle_reports_missing_and_duplicate_evidence_without_fabrication(tmp_path):
    path = tmp_path / "evidence.db"
    _, _, _, _ = _lifecycle_sale(path)
    assert get_sn_lifecycle("MISSING-001", path) == {
        "sn": "MISSING-001", "events": [], "status": "未找到", "evidence": []
    }
    # A duplicate batch source is evidence of a conflict, not a made-up device event.
    conn_path = path
    from src.models.connection import connect
    conn = connect(conn_path)
    try:
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        conn.execute(
            "INSERT INTO batches(product_id,purchase_price_cents,quantity,remaining,date,sn_list) VALUES (?,?,?,?,?,?)",
            (product_id, 1, 1, 1, "2026-09-05", "LIFE-001"),
        )
        conn.commit()
    finally:
        conn.close()
    lifecycle = get_sn_lifecycle("LIFE-001", path)
    assert lifecycle["status"] == "未知/异常"
    assert any("多个在库批次" in item for item in lifecycle["evidence"])
