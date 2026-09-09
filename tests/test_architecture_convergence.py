"""Cross-entry invariants, actual monthly reports and full-restore safety."""
import json
from pathlib import Path

import pytest

from src.models.connection import connect, DatabaseRestoreError, get_database_path
from src.models.migrations import migrate_database
from src.models.queries import export_backup_data, list_shipment_allocations
from src.models.finance_queries import get_finance_dashboard
from src.services import database_service as recovery
from src.services.exceptions import InvalidTransitionError
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.return_service import ReturnService
from src.utils.json_export import export_all_to_json, import_from_json
from src.utils.monthly_report import get_monthly_report, format_report_text
from tests.test_v1_17_inventory import _flow


def _shipped(path, shipped_date="2026-08-04"):
    product, first, second, quote = _flow(path)
    InventoryService(path).ship_quote(quote, shipped_date=shipped_date, allocations=[
        {"batch_id": first, "quantity": 2, "sn_list": "SN0001,SN0002"},
        {"batch_id": second, "quantity": 1, "sn_list": "SN0003"},
    ])
    return product, first, second, quote


@pytest.mark.parametrize("history", ["draft", "shipped", "deleted", "empty-stock"])
def test_finance_deletion_protection_all_entries(tmp_path, history):
    path = tmp_path / "delete.db"
    product, first, second, quote = _shipped(path) if history == "shipped" else _flow(path)
    if history in ("deleted", "empty-stock"):
        conn = connect(path)
        if history == "deleted":
            conn.execute("UPDATE batches SET deleted_at='2026-08-04'")
        else:
            conn.execute("UPDATE batches SET remaining=0")
        conn.commit()
        conn.close()
    before = export_backup_data(path)
    if history != "deleted":
        with pytest.raises(InvalidTransitionError):
            InventoryService(path).delete_batch(first)
    with pytest.raises(InvalidTransitionError):
        ProductService(path).delete(product)
    assert export_backup_data(path) == before
    empty = ProductService(path).create(series="unused catalog")
    ProductService(path).delete(empty)


def test_prefinance_multi_batch_checks_before_writes(tmp_path):
    path = tmp_path / "legacy-delete.db"
    product, first, second, quote = _flow(path)
    conn = connect(path)
    conn.execute("UPDATE finance_settings SET enabled_at=NULL")
    conn.execute("UPDATE quotes SET batch_id=?,status='已报价' WHERE id=?", (second, quote))
    conn.commit()
    conn.close()
    before = export_backup_data(path)
    with pytest.raises(InvalidTransitionError):
        ProductService(path).delete(product)
    assert export_backup_data(path) == before
    with pytest.raises(InvalidTransitionError):
        InventoryService(path).delete_batch(second)
    conn = connect(path)
    conn.execute("UPDATE quotes SET status='待确认'")
    conn.commit()
    conn.close()
    ProductService(path).delete(product)
    assert all(row["deleted_at"] for row in export_backup_data(path)["batches"])


def test_monthly_uses_actual_dates_costs_and_excludes_quotes(tmp_path):
    path = tmp_path / "month.db"
    _, first, _, quote = _flow(path)
    OrderService(path).transition(quote, "已报价")
    assert get_monthly_report(2026, 8, path)["total_revenue_cents"] == 0
    assert get_monthly_report(2026, 8, path)["slow_movers"]
    rows = export_backup_data(path)["batches"]
    InventoryService(path).ship_quote(quote, shipped_date="2026-09-01", allocations=[
        {"batch_id": first, "quantity": 2}, {"batch_id": rows[1]["id"], "quantity": 1},
    ])
    assert get_monthly_report(2026, 8, path)["order_count"] == 0
    report = get_monthly_report(2026, 9, path)
    assert report["total_revenue_cents"] == 1_200_000
    assert report["total_profit_cents"] == 280_000
    assert report["order_count"] == 1
    assert report["top_products"][0]["sale_count"] == 3
    assert not report["slow_movers"]
    assert report["total_profit_cents"] == get_finance_dashboard("2026-09-01", "2026-09-30", path)["gross_profit_cents"]
    assert "collection_rate" not in report


@pytest.mark.parametrize("restock,profit", [(True, -80_000), (False, -400_000)])
def test_cross_month_returns_keep_ledger_cost_policy(tmp_path, restock, profit):
    path = tmp_path / "return.db"
    _, _, second, quote = _shipped(path)
    allocation = next(r for r in list_shipment_allocations(quote, path) if r["batch_id"] == second)
    ReturnService(path).return_sale(quote, quantity=1, return_date="2026-09-02",
        restock=restock, reason="test", restock_allocations=[{
            "shipment_allocation_id": allocation["id"], "quantity": 1, "sn_list": "SN0003",
        }])
    report = get_monthly_report(2026, 9, path)
    assert report["order_count"] == 0
    assert report["total_revenue_cents"] == -400_000
    assert report["total_profit_cents"] == profit
    assert report["top_products"][0]["sale_count"] == -1
    assert get_monthly_report(2026, 8, path)["total_profit_cents"] == 280_000


def test_net_customer_cash_receipt_reversal_and_refund(tmp_path):
    path = tmp_path / "cash.db"
    _, _, second, quote = _shipped(path)
    data = export_backup_data(path)
    customer = data["customers"][0]["id"]
    account = next(a["id"] for a in data["ledger_accounts"] if not a["is_system"])
    payments = PaymentService(path)
    payment = payments.receive_customer_payment(customer, 1_300_000, "2026-08-06", account_id=account)
    assert get_monthly_report(2026, 8, path)["total_received_cents"] == 1_300_000
    payments.void_payment(payment.payment_id, "test", reversal_date="2026-09-01")
    assert get_monthly_report(2026, 9, path)["total_received_cents"] == -1_300_000
    payments.receive_customer_payment(customer, 1_200_000, "2026-09-02", account_id=account)
    allocation = next(r for r in list_shipment_allocations(quote, path) if r["batch_id"] == second)
    ReturnService(path).return_sale(quote, quantity=1, return_date="2026-09-03",
        restock=True, reason="test", refund_account_id=account, cash_refund_cents=400_000,
        restock_allocations=[{"shipment_allocation_id": allocation["id"], "quantity": 1}])
    assert get_monthly_report(2026, 9, path)["total_received_cents"] == -500_000
    assert get_monthly_report(2026, 8, path)["total_received_cents"] == 1_300_000


def test_month_boundaries_and_incomplete_history(tmp_path):
    path = tmp_path / "dates.db"
    _shipped(path, "2026-12-31")
    assert get_monthly_report(2026, 12, path)["order_count"] == 1
    january = get_monthly_report(2027, 1, path)
    assert january["order_count"] == 0 and january["revenue_change"] == -100
    assert "无完整账本数据" in format_report_text(get_monthly_report(2026, 7, path))
    conn = connect(path)
    conn.execute("UPDATE finance_settings SET enabled_at='2026-08-15'")
    conn.commit()
    conn.close()
    partial = get_monthly_report(2026, 8, path)
    assert partial["statistics_from"] == "2026-08-15"
    assert not partial["complete_month"] and partial["revenue_change"] is None
    assert "不可比" in format_report_text(partial)


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_full_restore_replaces_nonempty_database_and_is_repeatable(tmp_path, kind):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    migrate_database(target)
    ProductService(target).create(series="must be replaced")
    before = export_backup_data(target)
    backup = source
    if kind == "json":
        backup = tmp_path / "source.json"
        export_all_to_json(backup, source)
    with recovery.prepare_database_restore(backup, target, backup_format=kind) as prepared:
        staged = prepared.staged
        assert export_backup_data(target) == before
        result = recovery.apply_database_restore(prepared, target)
    assert not staged.exists()
    assert export_backup_data(result.safety_backup.path) == before
    restored = export_backup_data(target)
    assert restored == export_backup_data(source)
    recovery.restore_database(backup, target, backup_format=kind)
    assert export_backup_data(target) == restored
    assert len(restored["products"]) == 1
    assert recovery.reconcile_database(target).is_clean


@pytest.mark.parametrize("damage", ["future", "missing-ref", "imbalance", "missing-table", "bad-json"])
def test_invalid_json_never_changes_target(tmp_path, damage):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    _flow(target)
    backup = tmp_path / "backup.json"
    export_all_to_json(backup, source)
    doc = json.loads(backup.read_text(encoding="utf-8"))
    if damage == "future": doc["schema_version"] = 99
    if damage == "missing-ref": doc["data"]["quotes"][0]["customer_id"] = 999
    if damage == "imbalance": doc["data"]["ledger_lines"][0]["debit_cents"] += 100
    if damage == "missing-table": del doc["data"]["inventory_movements"]
    backup.write_text("broken" if damage == "bad-json" else json.dumps(doc), encoding="utf-8")
    before = export_backup_data(target)
    with pytest.raises(DatabaseRestoreError):
        recovery.restore_database(backup, target, backup_format="json")
    assert export_backup_data(target) == before
    assert not (tmp_path / "backup").exists()


def test_json_merge_and_default_target_are_rejected(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    _flow(target)
    backup = tmp_path / "backup.json"
    export_all_to_json(backup, source)
    before = export_backup_data(target)
    ok, message, _ = import_from_json(backup, target)
    assert not ok and "非空" in message
    assert export_backup_data(target) == before
    assert not import_from_json(backup)[0]
    assert not import_from_json(backup, get_database_path())[0]


def test_restore_failure_after_copy_rolls_back(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    migrate_database(target)
    ProductService(target).create(series="original")
    before = export_backup_data(target)
    original = recovery._require_clean_database
    def fail_target(path):
        if Path(path) == target:
            raise RuntimeError("injected post-copy failure")
        return original(path)
    with recovery.prepare_database_restore(source, target) as prepared:
        monkeypatch.setattr(recovery, "_require_clean_database", fail_target)
        with pytest.raises(DatabaseRestoreError, match="已还原") as exc:
            recovery.apply_database_restore(prepared, target)
    assert exc.value.safety_backup.path.exists()
    assert export_backup_data(target) == before


def test_restore_rejects_business_anomaly_in_sqlite(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    migrate_database(target)
    conn = connect(source)
    conn.execute("UPDATE suppliers SET balance_cents=balance_cents+1")
    conn.commit()
    conn.close()
    before = export_backup_data(target)
    with pytest.raises(DatabaseRestoreError, match="SUPPLIER"):
        recovery.restore_database(source, target)
    assert export_backup_data(target) == before


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_v5_backup_rebuilds_return_allocations_before_restore(tmp_path, kind):
    source, target = tmp_path / "v5.db", tmp_path / "target.db"
    _, _, second, quote = _shipped(source)
    allocation = next(r for r in list_shipment_allocations(quote, source) if r["batch_id"] == second)
    ReturnService(source).return_sale(quote, quantity=1, return_date="2026-08-06",
        restock=False, reason="legacy", restock_allocations=[{
            "shipment_allocation_id": allocation["id"], "quantity": 1, "sn_list": "SN0003",
        }])
    backup = source
    if kind == "sqlite":
        conn = connect(source)
        conn.execute("DROP TABLE sales_return_allocations")
        conn.execute("PRAGMA user_version=5")
        conn.commit()
        conn.close()
    else:
        backup = tmp_path / "v5.json"
        export_all_to_json(backup, source)
        document = json.loads(backup.read_text(encoding="utf-8"))
        document["schema_version"] = 5
        del document["data"]["sales_return_allocations"]
        # Old exports did not include logs or price snapshots.
        for table in ("operation_logs", "price_snapshots", "price_snapshot_items"):
            del document["data"][table]
        backup.write_text(json.dumps(document), encoding="utf-8")
    recovery.restore_database(backup, target, backup_format=kind)
    assert list_shipment_allocations(quote, target)[1]["returned_quantity"] == 1
    assert recovery.reconcile_database(target).is_clean


def test_json_restore_preserves_payment_identity_for_later_void(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    data = export_backup_data(source)
    account = next(a["id"] for a in data["ledger_accounts"] if not a["is_system"])
    receipt = PaymentService(source).receive_customer_payment(
        data["customers"][0]["id"], 20_000, "2026-08-10", account_id=account)
    backup = tmp_path / "backup.json"
    export_all_to_json(backup, source)
    recovery.restore_database(backup, target, backup_format="json")
    PaymentService(target).void_payment(receipt.payment_id, "after restore", reversal_date="2026-09-01")
    assert get_monthly_report(2026, 9, target)["total_received_cents"] == -20_000
    assert recovery.reconcile_database(target).is_clean


def test_net_customer_cash_excludes_opening_transfer_adjustment(tmp_path):
    from src.services.finance_service import FinanceService
    path = tmp_path / "noncash.db"
    _flow(path)
    finance = FinanceService(path)
    first = next(a["id"] for a in export_backup_data(path)["ledger_accounts"] if not a["is_system"])
    second = finance.create_account("second", opening_balance_cents=500_000)
    finance.transfer(from_account_id=second, to_account_id=first, amount_cents=100_000,
                     entry_date="2026-08-05")
    finance.adjust_account(account_id=first, delta_cents=20_000,
                           entry_date="2026-08-06", reason="test")
    assert get_monthly_report(2026, 8, path)["total_received_cents"] == 0


def test_restore_backup_failure_never_replaces_target(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _shipped(source)
    _flow(target)
    before = export_backup_data(target)
    with recovery.prepare_database_restore(source, target) as prepared:
        def fail_backup(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(recovery, "create_backup", fail_backup)
        with pytest.raises(DatabaseRestoreError, match="备份失败"):
            recovery.apply_database_restore(prepared, target)
    assert export_backup_data(target) == before


def test_ui_restore_cancel_keeps_data_and_toolbar_routes(qapp, tmp_path, monkeypatch):
    from src.main import MainWindow
    from PyQt6.QtWidgets import QMessageBox
    source = tmp_path / "source.db"
    _shipped(source)
    window = MainWindow()
    try:
        before = export_backup_data()
        observed = []
        def reject(*args):
            assert not window.centralWidget().isEnabled()
            assert "不会合并" in args[2]
            observed.append(True)
            return QMessageBox.StandardButton.No
        monkeypatch.setattr(QMessageBox, "question", reject)
        window._restore_backup(str(source), "sqlite")
        assert observed and window.centralWidget().isEnabled()
        assert export_backup_data() == before
        called = []
        monkeypatch.setattr(window.record_tab, "on_follow_up", lambda: called.append("follow"))
        window.follow_up_btn.click()
        assert called == ["follow"]
        assert "JSON" in window.import_json_btn.text() and "恢复" in window.import_json_btn.text()
    finally:
        window.close()


def test_ui_restore_success_exits_after_confirmation(qapp, tmp_path, monkeypatch):
    from src.main import MainWindow
    from PyQt6.QtWidgets import QMessageBox, QApplication
    source = tmp_path / "source.db"
    _shipped(source)
    backup = tmp_path / "backup.json"
    export_all_to_json(backup, source)
    window = MainWindow()
    exits = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(QApplication, "quit", lambda *args: exits.append(True))
    try:
        window._restore_backup(str(backup), "json")
        assert exits == [True]
        assert export_backup_data() == export_backup_data(source)
    finally:
        window.close()
