"""Regression coverage for return ownership, state boundaries, actual sales, and reconciliation."""

from __future__ import annotations

import json

import pytest

from src.models.connection import connect
from src.models.finance_queries import get_profit_report
from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, get_customer_history, get_customer_stats
from src.services import database_service as recovery
from src.services.exceptions import InvalidTransitionError, ValidationError
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService
from src.services.return_service import ReturnService
from src.utils.json_export import export_all_to_json


def _setup(path):
    migrate_database(path)
    product = ProductService(path).create(series="REV-005-008", cpu="i7")
    customer = CustomerService(path).create(name="客户")
    supplier = SupplierService(path).create(name="供应商")
    FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "现金", "opening_balance_cents": 0}]
    )
    return product, customer, supplier


def _quote(path, product, customer, supplier, *, cost=300_000, quantity=1, sn_list="", price=400_000):
    batch = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=cost,
        quantity=quantity, sn_list=sn_list, date="2026-08-01",
    )
    quote = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=price,
        quote_quantity=quantity, quote_date="2026-08-01",
    )
    return batch, quote


def test_single_sn_return_auto_binds_and_releases_only_that_original_shipment(tmp_path):
    path = tmp_path / "single-sn.db"
    product, customer, supplier = _setup(path)
    batch, quote = _quote(path, product, customer, supplier, sn_list="SN-001")
    InventoryService(path).ship_quote(quote, "SN-001", shipped_date="2026-08-02")

    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-03", restock=True, reason="唯一来源",
    )
    conn = connect(path, read_only=True)
    try:
        assert conn.execute(
            "SELECT sn_list FROM sales_return_allocations"
        ).fetchone()[0] == "SN-001"
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (batch,)).fetchone()[0] == 1
    finally:
        conn.close()
    resale = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=400_000,
        quote_quantity=1, quote_date="2026-08-04",
    )
    InventoryService(path).ship_quote(resale, "SN-001", shipped_date="2026-08-04")
    ReturnService(path).return_sale(
        resale, quantity=1, return_date="2026-08-05", restock=True, reason="第二轮唯一来源",
    )
    third_sale = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=400_000,
        quote_quantity=1, quote_date="2026-08-06",
    )
    InventoryService(path).ship_quote(third_sale, "SN-001", shipped_date="2026-08-06")
    assert ReconciliationService(path).run().is_clean


def test_partial_return_of_multi_sn_shipment_requires_explicit_sn(tmp_path):
    path = tmp_path / "partial-sn.db"
    product, customer, supplier = _setup(path)
    _, quote = _quote(path, product, customer, supplier, quantity=2, sn_list="SN-001,SN-002")
    InventoryService(path).ship_quote(quote, "SN-001,SN-002", shipped_date="2026-08-02")
    before = export_backup_data(path)
    with pytest.raises(ValidationError, match="SN"):
        ReturnService(path).return_sale(
            quote, quantity=1, return_date="2026-08-03", restock=True, reason="部分退货",
        )
    assert export_backup_data(path) == before


def test_nonrestock_sn_return_does_not_make_the_device_sellable(tmp_path):
    path = tmp_path / "nonrestock-sn.db"
    product, customer, supplier = _setup(path)
    batch, quote = _quote(path, product, customer, supplier, sn_list="SN-001")
    InventoryService(path).ship_quote(quote, "SN-001", shipped_date="2026-08-02")
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-03", restock=False, reason="不回库",
    )
    replacement = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=400_000,
        quote_quantity=1, quote_date="2026-08-04",
    )
    with pytest.raises(ValidationError, match="SN"):
        InventoryService(path).ship_quote(replacement, "SN-001", shipped_date="2026-08-04")


def test_snless_history_can_return_without_inventing_a_device_identity(tmp_path):
    path = tmp_path / "snless-history.db"
    product, customer, supplier = _setup(path)
    batch, quote = _quote(path, product, customer, supplier)
    InventoryService(path).ship_quote(quote, shipped_date="2026-08-02")
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-03", restock=True, reason="历史无SN",
    )
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("SELECT sn_list FROM sales_return_allocations").fetchone()[0] == ""
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (batch,)).fetchone()[0] == 1
    finally:
        conn.close()
    assert ReconciliationService(path).run().is_clean


def test_general_transition_cannot_bypass_shipment_or_receipt_transaction(tmp_path):
    path = tmp_path / "transition.db"
    product, customer, supplier = _setup(path)
    batch, quote = _quote(path, product, customer, supplier)
    orders = OrderService(path)
    orders.transition(quote, "已报价")
    before = export_backup_data(path)
    with pytest.raises(InvalidTransitionError, match="业务事务"):
        orders.transition(quote, "已出库")
    assert export_backup_data(path) == before
    InventoryService(path).ship_quote(quote, shipped_date="2026-08-02")
    assert connect(path, read_only=True).execute(
        "SELECT remaining FROM batches WHERE id=?", (batch,)
    ).fetchone()[0] == 0
    before_receipt = export_backup_data(path)
    with pytest.raises(InvalidTransitionError, match="业务事务"):
        orders.transition(quote, "已收款")
    assert export_backup_data(path) == before_receipt


def test_customer_actual_sales_and_profit_use_ledger_not_quotes_or_anchor_cost(tmp_path):
    path = tmp_path / "customer-actual.db"
    product, customer, supplier = _setup(path)
    first = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=3_000_000,
        quantity=2, date="2026-08-01",
    )
    quote = OrderService(path).create_quote(
        batch_id=first, customer_id=customer, quote_price_cents=4_000_000,
        quote_quantity=3, quote_date="2026-08-01",
    )
    second = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=3_200_000,
        quantity=1, date="2026-08-02", sn_list="",
    )
    InventoryService(path).ship_quote(quote, shipped_date="2026-08-03", allocations=[
        {"batch_id": first, "quantity": 2}, {"batch_id": second, "quantity": 1},
    ])
    _quote(path, product, customer, supplier, cost=1, price=9_000_000)  # An unshipped estimate.

    stats = get_customer_stats(customer, path)
    history = get_customer_history(customer, path)
    finance = get_profit_report(
        date_from="2026-08-01", date_to="2026-08-31", group_by="customer", db_path=path,
    )
    assert stats["total_quotes"] == 1
    assert stats["total_amount_cents"] == 12_000_000
    assert stats["total_profit_cents"] == 2_800_000
    assert [(row["actual_sales_cents"], row["actual_cost_cents"]) for row in history] == [
        (12_000_000, 9_200_000)
    ]
    assert [(row["sales_cents"], row["cogs_cents"]) for row in finance] == [(12_000_000, 9_200_000)]
    conn = connect(path, read_only=True)
    try:
        allocation = conn.execute(
            "SELECT id FROM shipment_allocations WHERE batch_id=?", (second,)
        ).fetchone()[0]
    finally:
        conn.close()
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-09-02", restock=False, reason="跨月不回库",
        restock_allocations=[{"shipment_allocation_id": allocation, "quantity": 1}],
    )
    stats = get_customer_stats(customer, path)
    assert (stats["total_amount_cents"], stats["total_profit_cents"]) == (8_000_000, -1_200_000)
    august = get_profit_report(
        date_from="2026-08-01", date_to="2026-08-31", group_by="customer", db_path=path,
    )[0]
    september = get_profit_report(
        date_from="2026-09-01", date_to="2026-09-30", group_by="customer", db_path=path,
    )[0]
    assert (august["sales_cents"] + september["sales_cents"],
            august["cogs_cents"] + september["cogs_cents"]) == (8_000_000, 9_200_000)


def test_reconciliation_checks_net_money_refund_and_sn_evidence_without_writing(tmp_path):
    path = tmp_path / "reconciliation-rules.db"
    product, customer, supplier = _setup(path)
    batch, quote = _quote(path, product, customer, supplier, quantity=2, sn_list="SN-001,SN-002")
    InventoryService(path).ship_quote(quote, "SN-001,SN-002", shipped_date="2026-08-02")
    account = next(
        row["id"] for row in export_backup_data(path)["ledger_accounts"] if not row["is_system"]
    )
    payment = PaymentService(path).receive_customer_payment(
        customer, 800_000, "2026-08-02", account_id=account,
    )
    conn = connect(path, read_only=True)
    try:
        allocation = conn.execute(
            "SELECT id FROM shipment_allocations WHERE batch_id=?", (batch,)
        ).fetchone()[0]
    finally:
        conn.close()
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-03", restock=True, reason="部分退款",
        refund_account_id=account, cash_refund_cents=100_000,
        restock_allocations=[{"shipment_allocation_id": allocation, "quantity": 1, "sn_list": "SN-001"}],
    )
    assert ReconciliationService(path).run().is_clean  # legal partial refund and return.

    conn = connect(path)
    conn.execute("UPDATE payment_allocations SET amount_cents=? WHERE payment_id=?", (700_001, payment.payment_id))
    conn.execute("UPDATE quotes SET received_amount_cents=? WHERE id=?", (400_001, quote))
    conn.execute("UPDATE sales_returns SET cash_refund_cents=? WHERE quote_id=?", (100_001, quote))
    conn.execute("UPDATE sales_return_allocations SET sn_list='' WHERE sales_return_id=(SELECT id FROM sales_returns WHERE quote_id=?)", (quote,))
    conn.commit()
    conn.close()
    before = export_backup_data(path)
    report = ReconciliationService(path).run()
    codes = {issue.code for issue in report.issues}
    expected_codes = {"CUSTOMER_PAYMENT_NET", "QUOTE_NET_ALLOCATION", "REFUND_SOURCE", "RETURN_SN_OWNERSHIP"}
    assert expected_codes <= codes
    assert all(issue.evidence and issue.recommendation for issue in report.issues if issue.code in expected_codes)
    assert export_backup_data(path) == before


def test_reconciliation_reports_business_evidence_and_restore_preflight_blocks_it(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    product, customer, supplier = _setup(source)
    _, quote = _quote(source, product, customer, supplier)
    conn = connect(source)
    conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote,))
    conn.commit()
    conn.close()
    report = ReconciliationService(source).run()
    issue = next(item for item in report.issues if item.code == "QUOTE_STATUS")
    assert issue.entity_id == quote and issue.difference_cents is None
    assert issue.evidence and issue.recommendation

    for kind in ("sqlite", "json"):
        backup = source
        if kind == "json":
            backup = tmp_path / "source.json"
            export_all_to_json(backup, source)
        with pytest.raises(Exception, match="QUOTE_STATUS"):
            recovery.restore_database(backup, target, backup_format=kind)
        assert not target.exists()
