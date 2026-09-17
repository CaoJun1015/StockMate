"""Release-blocking regressions that cross the REV-001 to REV-008 boundaries."""

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import Qt

from src.models.connection import connect, create_backup
from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, list_shipment_allocations
from src.services import database_service as recovery
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService
from src.services.return_service import ReturnService
from src.ui.finance_tab import FinanceTab


def _setup(path):
    migrate_database(path)
    product = ProductService(path).create(series="REV-009", cpu="i7")
    customer = CustomerService(path).create(name="连续客户")
    supplier = SupplierService(path).create(name="连续供应商")
    FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "现金", "opening_balance_cents": 0}]
    )
    account = next(
        row["id"] for row in export_backup_data(path)["ledger_accounts"] if not row["is_system"]
    )
    return product, customer, supplier, account


def _quote(
    path, product, customer, supplier, *, batch_quantity=1, quantity=1,
    price=100_000, sn_list="", date="2026-08-01",
):
    batch = InventoryService(path).receive_batch(
        product_id=product,
        supplier_id=supplier,
        purchase_price_cents=70_000,
        quantity=batch_quantity,
        sn_list=sn_list,
        date=date,
    )
    quote = OrderService(path).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=price,
        quote_quantity=quantity,
        quote_date=date,
    )
    return batch, quote


def test_receipt_refund_restore_and_multi_round_sn_resale(tmp_path):
    source = tmp_path / "source.db"
    product, customer, supplier, account = _setup(source)
    batch, first_quote = _quote(source, product, customer, supplier, sn_list="SN-009")
    InventoryService(source).ship_quote(first_quote, "SN-009", shipped_date="2026-08-02")
    first_payment = PaymentService(source).receive_customer_payment(
        customer, 100_000, "2026-08-03", account_id=account
    )
    ReturnService(source).return_sale(
        first_quote,
        quantity=1,
        return_date="2026-08-04",
        restock=True,
        cash_refund_cents=100_000,
        refund_account_id=account,
        reason="全额退款后恢复",
    )
    backup = create_backup(source, prefix="rev009", retain=False)
    assert backup and backup.path.exists() and backup.sha256

    restored = tmp_path / "restored.db"
    recovery.restore_database(backup.path, restored)
    second_quote = OrderService(restored).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=110_000,
        quote_quantity=1,
        quote_date="2026-09-01",
    )
    InventoryService(restored).ship_quote(second_quote, "SN-009", shipped_date="2026-09-01")
    ReturnService(restored).return_sale(
        second_quote, quantity=1, return_date="2026-09-02", restock=True, reason="第二轮退货"
    )
    third_quote = OrderService(restored).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=120_000,
        quote_quantity=1,
        quote_date="2026-09-03",
    )
    InventoryService(restored).ship_quote(third_quote, "SN-009", shipped_date="2026-09-03")

    conn = connect(restored, read_only=True)
    try:
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (batch,)).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT received_amount_cents, status FROM quotes WHERE id=?", (second_quote,)
        ).fetchone()) == (0, "已全退")
        assert [tuple(row) for row in conn.execute(
            "SELECT quote_id, amount_cents FROM payment_allocations WHERE payment_id=?",
            (first_payment.payment_id,),
        ).fetchall()] == [
            (first_quote, 100_000),
            (first_quote, -100_000),
        ]
        assert conn.execute(
            "SELECT COUNT(*) FROM sales_return_allocations WHERE sn_list='SN-009'"
        ).fetchone()[0] == 2
    finally:
        conn.close()
    assert ReconciliationService(restored).run().is_clean


def test_partial_cross_batch_return_then_receipt_void_and_correction(tmp_path):
    path = tmp_path / "cross-batch.db"
    product, customer, supplier, account = _setup(path)
    first_batch, quote = _quote(
        path, product, customer, supplier, batch_quantity=1, quantity=2, date="2026-08-01"
    )
    second_batch = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=80_000,
        quantity=1, date="2026-08-02",
    )
    InventoryService(path).ship_quote(
        quote,
        shipped_date="2026-08-03",
        allocations=[
            {"batch_id": first_batch, "quantity": 1},
            {"batch_id": second_batch, "quantity": 1},
        ],
    )
    first_payment = PaymentService(path).receive_customer_payment(
        customer, 20_000, "2026-08-04", account_id=account
    )
    allocations = list_shipment_allocations(quote, path)
    returned_allocation = next(row for row in allocations if row["batch_id"] == second_batch)
    ReturnService(path).return_sale(
        quote,
        quantity=1,
        return_date="2026-09-01",
        restock=False,
        reason="跨月不回库",
        restock_allocations=[{
            "shipment_allocation_id": returned_allocation["id"], "quantity": 1, "sn_list": "",
        }],
    )
    second_payment = PaymentService(path).receive_customer_payment(
        customer, 80_000, "2026-09-02", account_id=account
    )
    reversal_id = PaymentService(path).void_payment(
        first_payment.payment_id, "第一笔冲销", reversal_date="2026-09-03"
    )
    replacement_id = PaymentService(path).correct_payment(
        second_payment.payment_id,
        amount_cents=90_000,
        pay_date="2026-09-04",
        account_id=account,
        remark="更正后金额",
        reason="跨月收款更正",
    )

    conn = connect(path, read_only=True)
    try:
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (first_batch,)).fetchone()[0] == 0
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (second_batch,)).fetchone()[0] == 0
        assert tuple(conn.execute(
            "SELECT received_amount_cents, status FROM quotes WHERE id=?", (quote,)
        ).fetchone()) == (90_000, "已出库")
        assert tuple(conn.execute(
            "SELECT shipment_allocation_id, quantity FROM sales_return_allocations"
        ).fetchone()) == (returned_allocation["id"], 1)
        assert tuple(conn.execute(
            "SELECT entry_kind, reversal_of_id, supersedes_id FROM payments WHERE id=?",
            (replacement_id,),
        ).fetchone()) == ("payment", None, second_payment.payment_id)
        assert conn.execute("SELECT entry_kind FROM payments WHERE id=?", (reversal_id,)).fetchone()[0] == "reversal"
    finally:
        conn.close()
    assert ReconciliationService(path).run().is_clean


def test_supplier_refund_cannot_be_reused_for_later_purchase(tmp_path):
    path = tmp_path / "supplier.db"
    product, _, supplier, account = _setup(path)
    first_batch = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=70_000, quantity=1,
        date="2026-08-01",
    )
    first_payment = PaymentService(path).record_supplier_payment(
        supplier, 70_000, "2026-08-02", account_id=account
    )
    ReturnService(path).return_purchase(
        first_batch,
        quantity=1,
        return_date="2026-08-03",
        cash_refund_cents=70_000,
        refund_account_id=account,
        reason="供应商全额退款",
    )
    second_batch = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=70_000, quantity=1,
        date="2026-09-01",
    )

    conn = connect(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM supplier_payment_allocations WHERE batch_id=?",
            (second_batch,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM payment_refund_allocations WHERE payment_id=?",
            (first_payment,),
        ).fetchone()[0] == 1
    finally:
        conn.close()
    assert ReconciliationService(path).run().is_clean


def test_sorted_receivable_action_targets_the_visible_customer(tmp_path, monkeypatch, qapp):
    path = tmp_path / "sorted-action.db"
    product, _, supplier, account = _setup(path)
    expected = {}
    for name, price in (("Zulu", 200_000), ("Alpha", 300_000)):
        customer = CustomerService(path).create(name=name)
        batch, quote = _quote(path, product, customer, supplier, price=price)
        InventoryService(path).ship_quote(quote, shipped_date="2026-08-02")
        expected[name] = customer

    tab = FinanceTab(db_path=path)
    captured = []
    monkeypatch.setattr(tab, "_receive", lambda customer_id, pending: captured.append((customer_id, pending)))
    tab.receivable_table.setSortingEnabled(True)
    tab.receivable_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    tab.refresh()
    assert [tab.receivable_table.item(row, 0).text() for row in range(2)] == ["Zulu", "Alpha"]
    for row in range(tab.receivable_table.rowCount()):
        tab.receivable_table.cellWidget(row, 6).click()
    assert captured == [(expected["Zulu"], 200_000), (expected["Alpha"], 300_000)]
    assert ReconciliationService(path).run().is_clean
