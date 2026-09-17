"""Behaviour coverage for continuous shipment scanning and allocation."""

from __future__ import annotations

import pytest

from src.models.queries import get_quote_detail, list_batches
from src.models.migrations import migrate_database
from src.services.finance_service import FinanceService
from src.services.exceptions import InsufficientStockError, ValidationError
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.product_service import ProductService
from src.ui.dialogs import ShipmentDialog


def _dialog_data(path):
    migrate_database(path)
    FinanceService(path).initialize_finance("2026-09-01", [{"name": "现金", "opening_balance_cents": 0}])
    product = ProductService(path).create(series="扫码机型")
    other = ProductService(path).create(series="其他机型")
    customer = CustomerService(path).create(name="扫码客户")
    supplier = SupplierService(path).create(name="扫码供应商")
    inventory = InventoryService(path)
    first = inventory.receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=1,
        quantity=1, sn_list="SCAN-A", date="2026-09-17",
    )
    second = inventory.receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=2,
        quantity=1, sn_list="SCAN-B", date="2026-09-17",
    )
    inventory.receive_batch(
        product_id=other, supplier_id=supplier, purchase_price_cents=3,
        quantity=1, sn_list="SCAN-OTHER", date="2026-09-17",
    )
    quote = OrderService(path).create_quote(
        batch_id=first, customer_id=customer, quote_price_cents=10,
        quote_quantity=2, quote_date="2026-09-17",
    )
    return inventory, quote, product, first, second


def test_continuous_scan_routes_unique_sn_without_accepting_dialog(qapp, tmp_path):
    inventory, quote_id, product, first, second = _dialog_data(tmp_path / "scan.db")
    dialog = ShipmentDialog(
        quote=get_quote_detail(quote_id, tmp_path / "scan.db"),
        batches=list_batches(product, tmp_path / "scan.db"),
        db_path=tmp_path / "scan.db",
    )
    # The test deliberately uses the real Enter signal a barcode scanner emits.
    dialog.scan_edit.setText("SCAN-A")
    dialog.scan_edit.returnPressed.emit()
    dialog.paste_edit.setPlainText("SCAN-B\nSCAN-A")
    dialog._consume_paste_input()

    allocations = dialog.get_data()["allocations"]
    assert {(item["batch_id"], item["sn_list"]) for item in allocations} == {
        (first, "SCAN-A"), (second, "SCAN-B")
    }
    assert dialog.result() == 0
    assert "重复扫码" in dialog.scan_summary.text()

    # Editing a serial number is authoritative for that allocation quantity.
    next(edit for batch, _, edit in dialog.allocation_rows if batch["id"] == second).clear()
    assert dialog.get_data()["allocations"] == [{"batch_id": first, "quantity": 1, "sn_list": "SCAN-A"}]
    dialog._consume_scans("SCAN-OTHER UNKNOWN-SN")
    assert "其他机型" in dialog.scan_summary.text()
    assert "未知 SN" in dialog.scan_summary.text()


def test_service_rechecks_cross_batch_stock_and_duplicate_submission_atomically(tmp_path):
    inventory, quote_id, product, first, second = _dialog_data(tmp_path / "service.db")
    with pytest.raises((InsufficientStockError, ValidationError)):
        inventory.ship_quote(quote_id, allocations=[
            {"batch_id": first, "quantity": 2, "sn_list": "SCAN-A,SCAN-B"},
        ])
    assert next(row for row in list_batches(product, tmp_path / "service.db") if row["id"] == first)["remaining"] == 1
    inventory.ship_quote(quote_id, allocations=[
        {"batch_id": first, "quantity": 1, "sn_list": "SCAN-A"},
        {"batch_id": second, "quantity": 1, "sn_list": "SCAN-B"},
    ])
    with pytest.raises((ValidationError, Exception), match="不允许出库|SN"):
        inventory.ship_quote(quote_id, allocations=[
            {"batch_id": first, "quantity": 1, "sn_list": "SCAN-A"},
            {"batch_id": second, "quantity": 1, "sn_list": "SCAN-B"},
        ])
