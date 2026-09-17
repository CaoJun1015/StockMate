"""Return allowance and preview behaviour."""

from __future__ import annotations

from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, list_shipment_allocations
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.return_service import ReturnService
from src.ui.finance_dialogs import ReturnDialog


def _sale(path):
    migrate_database(path)
    account = FinanceService(path).initialize_finance(
        "2026-09-01", [{"name": "现金", "opening_balance_cents": 0}]
    )[0]
    product = ProductService(path).create(series="退货预览")
    customer = CustomerService(path).create(name="退货客户")
    supplier = SupplierService(path).create(name="退货供应商")
    inventory = InventoryService(path)
    first = inventory.receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=40_000,
        quantity=1, sn_list="RETURN-A", date="2026-09-01",
    )
    second = inventory.receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=60_000,
        quantity=1, sn_list="RETURN-B", date="2026-09-01",
    )
    quote = OrderService(path).create_quote(
        batch_id=first, customer_id=customer, quote_price_cents=100_000,
        quote_quantity=2, quote_date="2026-09-01",
    )
    inventory.ship_quote(quote, shipped_date="2026-09-02", allocations=[
        {"batch_id": first, "quantity": 1, "sn_list": "RETURN-A"},
        {"batch_id": second, "quantity": 1, "sn_list": "RETURN-B"},
    ])
    PaymentService(path).receive_customer_payment(customer, 200_000, "2026-09-03", account_id=account)
    return ReturnService(path), quote, account


def test_sale_return_preview_is_read_only_and_matches_commit(tmp_path):
    path = tmp_path / "preview.db"
    service, quote, account = _sale(path)
    allocation = list_shipment_allocations(quote, path)[0]
    request = dict(
        quantity=1, restock=True, refund_account_id=account, cash_refund_cents=50_000,
        restock_allocations=[{"shipment_allocation_id": allocation["id"], "quantity": 1, "sn_list": "RETURN-A"}],
    )
    before = export_backup_data(path)
    preview = service.preview_sale_return(quote, **request)
    assert export_backup_data(path) == before
    assert (preview["returnable_quantity"], preview["cost_cents"], preview["released_balance_cents"], preview["retained_balance_cents"]) == (2, 40_000, 100_000, 50_000)
    service.return_sale(quote, return_date="2026-09-04", reason="预览一致", **request)
    after = export_backup_data(path)
    returned = after["sales_returns"][0]
    assert (returned["revenue_cents"], returned["cost_cents"], returned["cash_refund_cents"]) == (
        preview["revenue_cents"], preview["cost_cents"], preview["cash_refund_cents"]
    )
    assert list_shipment_allocations(quote, path)[0]["returned_quantity"] == 1


def test_return_sn_rematch_clears_stale_rows_and_excludes_returned_sn(qapp, tmp_path, monkeypatch):
    path = tmp_path / "dialog.db"
    service, quote, account = _sale(path)
    rows = list_shipment_allocations(quote, path)
    dialog = ReturnDialog(
        "销售退货", max_quantity=2, allow_restock=True, allocations=rows,
        preview_callback=lambda data: service.preview_sale_return(
            quote, quantity=data["quantity"], restock=data["restock"],
            refund_account_id=account, cash_refund_cents=0,
            restock_allocations=data["restock_allocations"],
        ),
    )
    dialog.auto_sn_edit.setText("RETURN-A")
    dialog._match_return_sns()
    dialog.auto_sn_edit.setText("RETURN-B")
    dialog._match_return_sns()
    assert [spin.value() for _, spin, _ in dialog.allocation_rows] == [0, 1]
    service.return_sale(
        quote, quantity=1, return_date="2026-09-04", restock=False, reason="不回库",
        restock_allocations=[{"shipment_allocation_id": rows[0]["id"], "quantity": 1, "sn_list": "RETURN-A"}],
    )
    dialog = ReturnDialog("销售退货", max_quantity=1, allow_restock=True,
                          allocations=list_shipment_allocations(quote, path))
    monkeypatch.setattr("src.ui.finance_dialogs.QMessageBox.information", lambda *args: None)
    dialog.auto_sn_edit.setText("RETURN-A")
    dialog._match_return_sns()
    assert dialog.get_data()["restock_allocations"] == []
