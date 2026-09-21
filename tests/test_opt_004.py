"""Historical anomaly review and safe cache-repair regressions."""

from __future__ import annotations

import pytest

from src.models.connection import connect
from src.models.migrations import migrate_database
from src.models.queries import export_backup_data
from src.services.finance_service import FinanceService
from src.services.historical_repair_service import HistoricalRepairService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.return_service import ReturnService


def _anomalous_history(path):
    migrate_database(path)
    product = ProductService(path).create(series="OPT-004", cpu="i7")
    customer = CustomerService(path).create(name="历史客户")
    supplier = SupplierService(path).create(name="历史供应商")
    FinanceService(path).initialize_finance(
        "2026-08-01", [{"name": "现金", "opening_balance_cents": 0}]
    )
    account = next(row["id"] for row in export_backup_data(path)["ledger_accounts"] if not row["is_system"])
    batch = InventoryService(path).receive_batch(
        product_id=product, supplier_id=supplier, purchase_price_cents=70_000,
        quantity=1, sn_list="SN-004", date="2026-08-01",
    )
    quote = OrderService(path).create_quote(
        batch_id=batch, customer_id=customer, quote_price_cents=100_000,
        quote_quantity=1, quote_date="2026-08-01",
    )
    InventoryService(path).ship_quote(quote, "SN-004", shipped_date="2026-08-02")
    PaymentService(path).receive_customer_payment(customer, 100_000, "2026-08-03", account_id=account)
    ReturnService(path).return_sale(
        quote, quantity=1, return_date="2026-08-04", restock=True,
        cash_refund_cents=100_000, refund_account_id=account, reason="历史退款",
    )
    conn = connect(path)
    try:
        conn.execute("DELETE FROM payment_refund_allocations")
        conn.execute("UPDATE quotes SET received_amount_cents=1, paid='否', status='待确认' WHERE id=?", (quote,))
        conn.commit()
    finally:
        conn.close()
    return quote


def test_historical_audit_classifies_evidence_and_repairs_only_quote_cache(tmp_path):
    path = tmp_path / "history.db"
    quote = _anomalous_history(path)
    service = HistoricalRepairService(path)
    plan = service.audit()

    cache = next(item for item in plan.repairable if item.entity_id == quote)
    unresolved = next(item for item in plan.anomalies if item.code == "REFUND_SOURCE")
    assert cache.action == "refresh_quote_cache"
    assert cache.money_impact_cents == -1 and cache.inventory_impact.startswith("0")
    assert unresolved.classification == "unresolvable"
    assert "当前：" in plan.format_text() and "拟修改：" in plan.format_text()
    before = export_backup_data(path)

    result = service.apply(plan, reason="仅重算可验证报价缓存")
    assert result.backup.path.exists() and result.backup.sha256
    assert [row for row in result.applied if row.entity_id == quote]
    after = export_backup_data(path)
    assert after["ledger_entries"] == before["ledger_entries"]
    assert after["inventory_movements"] == before["inventory_movements"]
    conn = connect(path, read_only=True)
    try:
        assert tuple(conn.execute(
            "SELECT received_amount_cents, paid, status FROM quotes WHERE id=?", (quote,)
        ).fetchone()) == (0, "否", "已全退")
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='historical_repair' AND entity_id=?",
            (quote,),
        ).fetchone()[0] == 1
    finally:
        conn.close()
    assert any(issue.code == "REFUND_SOURCE" for issue in result.remaining_report.issues)
    assert not HistoricalRepairService(path).audit().repairable


def test_historical_repair_rolls_back_if_audit_write_fails(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    _anomalous_history(path)
    service = HistoricalRepairService(path)
    plan = service.audit()
    before = export_backup_data(path)

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr("src.services.historical_repair_service.audit", fail)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        service.apply(plan, reason="演练回滚")
    assert export_backup_data(path) == before


def test_historical_audit_leaves_over_net_and_missing_sn_evidence_for_review(tmp_path):
    path = tmp_path / "ambiguous.db"
    _anomalous_history(path)
    conn = connect(path)
    try:
        conn.execute("UPDATE payment_allocations SET amount_cents=200001 WHERE amount_cents>0")
        conn.execute("UPDATE sales_return_allocations SET sn_list='' WHERE restock_quantity>0")
        conn.commit()
    finally:
        conn.close()

    plan = HistoricalRepairService(path).audit()
    classifications = {(item.code, item.classification) for item in plan.anomalies}
    assert ("QUOTE_NET_ALLOCATION", "manual_review") in classifications
    assert ("RETURN_SN_OWNERSHIP", "unresolvable") in classifications
    assert ("REFUND_SOURCE", "unresolvable") in classifications
