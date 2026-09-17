"""Regression tests for the first StockMate repair batch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from src.models.connection import connect, copy_database, create_backup, transaction
from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, get_receivables
from src.services.database_service import restore_database
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.return_service import ReturnService
from src.services.reconciliation_service import ReconciliationService
import src.services.return_service as return_service_module
from src.utils.json_export import export_all_to_json, import_from_json
from src.ui.customer_tab import CustomerTab
from src.ui.finance_tab import FinanceTab


def _seed(path, *, customer_name="客户A", supplier_name="供应商A"):
    migrate_database(path)
    with transaction(path) as conn:
        product = conn.execute(
            "INSERT INTO products(series,cpu) VALUES ('测试机型','i7')"
        ).lastrowid
        customer = conn.execute(
            "INSERT INTO customers(name) VALUES (?)", (customer_name,)
        ).lastrowid
        supplier = conn.execute(
            "INSERT INTO suppliers(name) VALUES (?)", (supplier_name,)
        ).lastrowid
    account = FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "微信", "opening_balance_cents": 1_000_000}]
    )[0]
    return product, customer, supplier, account


def _shipped_sale(path, *, product, customer, supplier, account, price=350_000,
                  quantity=1, sn_list=""):
    batch = InventoryService(path).receive_batch(
        product_id=product,
        purchase_price_cents=300_000,
        quantity=quantity,
        date="2026-08-01",
        supplier_id=supplier,
        sn_list=sn_list,
    )
    quote = OrderService(path).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=price,
        quote_quantity=quantity,
        quote_date="2026-08-01",
    )
    InventoryService(path).ship_quote(quote, sn_list=sn_list, shipped_date="2026-08-02")
    return batch, quote


@pytest.mark.parametrize("refund_cents, expected_new_receipt", [(350_000, 0), (100_000, 250_000)])
def test_customer_refund_reduces_reusable_advance(tmp_path, refund_cents, expected_new_receipt):
    path = tmp_path / f"customer-refund-{refund_cents}.db"
    product, customer, supplier, account = _seed(path)
    batch, quote = _shipped_sale(
        path, product=product, customer=customer, supplier=supplier, account=account
    )
    payment = PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-03", account_id=account
    )
    ReturnService(path).return_sale(
        quote,
        quantity=1,
        return_date="2026-08-04",
        restock=True,
        cash_refund_cents=refund_cents,
        refund_account_id=account,
        reason="退款回归",
    )

    new_quote = OrderService(path).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=350_000,
        quote_quantity=1,
        quote_date="2026-08-05",
    )
    InventoryService(path).ship_quote(new_quote, shipped_date="2026-08-06")

    conn = connect(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT received_amount_cents,status FROM quotes WHERE id=?", (new_quote,)
        ).fetchone()
        assert tuple(row) == (expected_new_receipt, "已收款" if expected_new_receipt == 350_000 else "已出库")
        assert conn.execute(
            "SELECT SUM(amount_cents) FROM payment_allocations WHERE payment_id=?",
            (payment.payment_id,),
        ).fetchone()[0] == expected_new_receipt
    finally:
        conn.close()


@pytest.mark.parametrize("refund_cents, expected_old_advance", [(300_000, 0), (100_000, 200_000)])
def test_supplier_refund_reduces_reusable_advance(tmp_path, refund_cents, expected_old_advance):
    path = tmp_path / f"supplier-refund-{refund_cents}.db"
    product, _, supplier, account = _seed(path)
    old_payment = PaymentService(path).record_supplier_payment(
        supplier, 300_000, "2026-08-01", account_id=account
    )
    old_batch = InventoryService(path).receive_batch(
        product_id=product,
        purchase_price_cents=300_000,
        quantity=1,
        date="2026-08-02",
        supplier_id=supplier,
    )
    ReturnService(path).return_purchase(
        old_batch,
        quantity=1,
        return_date="2026-08-03",
        cash_refund_cents=refund_cents,
        refund_account_id=account,
        reason="退款回归",
    )
    new_batch = InventoryService(path).receive_batch(
        product_id=product,
        purchase_price_cents=300_000,
        quantity=1,
        date="2026-08-04",
        supplier_id=supplier,
        settlement_mode="paid",
        account_id=account,
    )

    conn = connect(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT SUM(amount_cents) FROM supplier_payment_allocations WHERE payment_id=?",
            (old_payment,),
        ).fetchone()[0] == expected_old_advance
        assert conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM supplier_payment_allocations "
            "WHERE payment_id=? AND batch_id=?", (old_payment, new_batch),
        ).fetchone()[0] == expected_old_advance
        assert conn.execute(
            "SELECT COUNT(*) FROM payments WHERE supplier_id=? AND type='payable'",
            (supplier,),
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_returned_net_amount_controls_fifo_and_settlement(tmp_path):
    path = tmp_path / "net-fifo.db"
    product, customer, supplier, account = _seed(path)
    batch, old_quote = _shipped_sale(
        path, product=product, customer=customer, supplier=supplier, account=account,
        price=350_000, quantity=2,
    )
    ReturnService(path).return_sale(
        old_quote,
        quantity=1,
        return_date="2026-08-03",
        restock=True,
        reason="净额回归",
    )
    PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-04", account_id=account
    )
    new_quote = OrderService(path).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=350_000,
        quote_quantity=1,
        quote_date="2026-08-05",
    )
    InventoryService(path).ship_quote(new_quote, shipped_date="2026-08-06")
    second_payment = PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-07", account_id=account
    )

    conn = connect(path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT id,received_amount_cents,status FROM quotes ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (old_quote, 350_000, "已收款"),
            (new_quote, 350_000, "已收款"),
        ]
        assert conn.execute(
            "SELECT quote_id,amount_cents FROM payment_allocations "
            "WHERE payment_id=? AND amount_cents>0", (second_payment.payment_id,)
        ).fetchone()["quote_id"] == new_quote
    finally:
        conn.close()
    assert not get_receivables(path)


@pytest.mark.parametrize("has_sn", [False, True])
@pytest.mark.parametrize("action", ["void", "correct"])
def test_payment_reversal_keeps_shipped_fact_without_sn(tmp_path, has_sn, action):
    path = tmp_path / f"reversal-{has_sn}-{action}.db"
    product, customer, supplier, account = _seed(path)
    batch, quote = _shipped_sale(
        path,
        product=product,
        customer=customer,
        supplier=supplier,
        account=account,
        sn_list="SN-001" if has_sn else "",
    )
    payment = PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-03", account_id=account
    )
    if action == "void":
        PaymentService(path).void_payment(
            payment.payment_id, "冲销回归", reversal_date="2026-08-04"
        )
        expected_received = 0
    else:
        PaymentService(path).correct_payment(
            payment.payment_id,
            amount_cents=100_000,
            pay_date="2026-08-04",
            account_id=account,
            remark="更正回归",
            reason="金额更正",
        )
        expected_received = 100_000

    conn = connect(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT received_amount_cents,status FROM quotes WHERE id=?", (quote,)
        ).fetchone()
        assert tuple(row) == (expected_received, "已出库")
        assert conn.execute(
            "SELECT quantity FROM shipment_snapshots WHERE quote_id=?", (quote,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch,)
        ).fetchone()[0] == 0
    finally:
        conn.close()

    if action == "void":
        receipt = PaymentService(path).receive_customer_payment(
            customer, 350_000, "2026-08-05", account_id=account
        )
        assert receipt.allocated_cents == 350_000
    else:
        receipt = PaymentService(path).receive_customer_payment(
            customer, 250_000, "2026-08-05", account_id=account
        )
        assert receipt.allocated_cents == 250_000
    conn = connect(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT status,received_amount_cents FROM quotes WHERE id=?", (quote,)
        ).fetchone()[0:2] == ("已收款", 350_000)
    finally:
        conn.close()


def test_qt_sorted_refresh_keeps_customer_rows_and_finance_buttons(
    qapp, tmp_path, isolated_default_database, monkeypatch
):
    path = isolated_default_database
    with transaction(path) as conn:
        product = conn.execute("INSERT INTO products(series) VALUES ('排序机型')").lastrowid
        customers = {}
        for name, phone, price in (("Zulu", "111", 2), ("Alpha", "222", 10), ("Beta", "333", 100)):
            customers[name] = conn.execute(
                "INSERT INTO customers(name,phone) VALUES (?,?)", (name, phone)
            ).lastrowid
    account = FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "微信", "opening_balance_cents": 1_000_000}]
    )[0]
    supplier = SupplierService(path).create(name="排序供应商")
    for name, price in (("Zulu", 2), ("Alpha", 10), ("Beta", 100)):
        batch = InventoryService(path).receive_batch(
            product_id=product, purchase_price_cents=1, quantity=1,
            date="2026-08-01", supplier_id=supplier,
        )
        quote = OrderService(path).create_quote(
            batch_id=batch, customer_id=customers[name], quote_price_cents=price,
            quote_quantity=1, quote_date="2026-08-01",
        )
        InventoryService(path).ship_quote(quote, shipped_date="2026-08-02")

    monkeypatch.setenv("DIAOHUO_DATA_DIR", str(path.parent))
    tab = CustomerTab(SimpleNamespace())
    tab.customer_table.setSortingEnabled(True)
    tab.customer_table.sortItems(1, Qt.SortOrder.DescendingOrder)
    tab.refresh_customer_list()
    assert [
        (tab.customer_table.item(row, 0).text(), tab.customer_table.item(row, 1).text(), tab.customer_table.item(row, 4).text())
        for row in range(tab.customer_table.rowCount())
    ] == [(str(customers["Zulu"]), "Zulu", "111"), (str(customers["Beta"]), "Beta", "333"), (str(customers["Alpha"]), "Alpha", "222")]

    captured = []
    finance = FinanceTab(db_path=path)
    monkeypatch.setattr(finance, "_receive", lambda customer_id, pending: captured.append((customer_id, pending)))
    finance.receivable_table.setSortingEnabled(True)
    finance.receivable_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    finance.refresh()
    assert [finance.receivable_table.item(row, 0).text() for row in range(3)] == ["Zulu", "Beta", "Alpha"]
    finance.receivable_table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [finance.receivable_table.item(row, 0).text() for row in range(3)] == ["Zulu", "Alpha", "Beta"]
    for row in range(finance.receivable_table.rowCount()):
        finance.receivable_table.cellWidget(row, 6).click()
    assert {customer_id for customer_id, _ in captured} == set(customers.values())


def test_refund_write_failure_rolls_back_everything(tmp_path, monkeypatch):
    path = tmp_path / "refund-rollback.db"
    product, customer, supplier, account = _seed(path)
    _, quote = _shipped_sale(
        path, product=product, customer=customer, supplier=supplier, account=account
    )
    PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-03", account_id=account
    )
    before = export_backup_data(path)

    def fail(*args, **kwargs):
        raise RuntimeError("注入退款来源写入失败")

    monkeypatch.setattr(return_service_module, "insert_payment_refund_allocation", fail)
    with pytest.raises(RuntimeError, match="退款来源写入失败"):
        ReturnService(path).return_sale(
            quote,
            quantity=1,
            return_date="2026-08-04",
            restock=True,
            cash_refund_cents=100_000,
            refund_account_id=account,
            reason="回滚回归",
        )
    assert export_backup_data(path) == before


def test_old_schema_and_json_sqlite_backups_preserve_or_report_refund_sources(tmp_path):
    path = tmp_path / "source.db"
    product, customer, supplier, account = _seed(path)
    batch, quote = _shipped_sale(
        path, product=product, customer=customer, supplier=supplier, account=account
    )
    PaymentService(path).receive_customer_payment(
        customer, 350_000, "2026-08-03", account_id=account
    )
    ReturnService(path).return_sale(
        quote,
        quantity=1,
        return_date="2026-08-04",
        restock=True,
        cash_refund_cents=100_000,
        refund_account_id=account,
        reason="备份回归",
    )
    assert ReconciliationService(path).run().is_clean

    json_path = tmp_path / "backup.json"
    export_all_to_json(json_path, path)
    json_target = tmp_path / "json-target.db"
    migrate_database(json_target)
    ok, message, _ = import_from_json(json_path, json_target)
    assert ok, message
    assert len(export_backup_data(json_target)["payment_refund_allocations"]) == 1
    assert ReconciliationService(json_target).run().is_clean

    sqlite_backup = create_backup(path, prefix="test_backup", retain=False)
    sqlite_target = tmp_path / "sqlite-target.db"
    migrate_database(sqlite_target)
    restore_database(sqlite_backup.path, sqlite_target)
    assert export_backup_data(sqlite_target)["payment_refund_allocations"] == export_backup_data(path)["payment_refund_allocations"]
    assert ReconciliationService(sqlite_target).run().is_clean

    legacy = tmp_path / "legacy-v6.db"
    migrate_database(legacy)
    with transaction(legacy) as conn:
        conn.execute("DROP TABLE payment_refund_allocations")
        conn.execute("DELETE FROM schema_migrations WHERE version=7")
        conn.execute("PRAGMA user_version=6")
    migrate_database(legacy)
    conn = connect(legacy, read_only=True)
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='payment_refund_allocations'"
        ).fetchone()
    finally:
        conn.close()

    unresolved = tmp_path / "legacy-refund.db"
    copy_database(path, unresolved)
    conn = connect(unresolved)
    try:
        conn.execute("DROP TABLE payment_refund_allocations")
        conn.execute("DELETE FROM schema_migrations WHERE version=7")
        conn.execute("PRAGMA user_version=6")
        conn.commit()
    finally:
        conn.close()
    migrate_database(unresolved)
    report = ReconciliationService(unresolved).run()
    assert any(issue.code == "REFUND_SOURCE" for issue in report.issues)
