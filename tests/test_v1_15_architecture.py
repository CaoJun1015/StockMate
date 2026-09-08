"""v1.15 integer-money, migration, JSON, service, and audit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.connection import connect, restore_database
from src.models.migrations import DatabaseMigrationError, migrate_database
from src.models.queries import (
    export_quotes,
    get_financial_audit_detail,
    get_payment_flow,
    list_financial_audit_events,
)
from src.models.schema import SCHEMA_VERSION
from src.services.finance_service import FinanceService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService, SupplierService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.reconciliation_service import ReconciliationService
from src.utils.json_export import export_all_to_json, import_from_json
from src.utils.excel_export import export_quotes_to_excel
from src.utils.follow_up import get_stale_quotes
from src.utils.monthly_report import get_monthly_report
from src.utils.price_diff import get_latest_snapshot, save_snapshot
from src.utils.quote_assist import get_quote_history


LEGACY_COLUMNS = {
    "batches": "purchase_price",
    "quotes": "quote_price",
    "quotes_received": "received_amount",
    "payments": "amount",
    "customers": "balance",
    "suppliers": "balance",
}


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _make_v2(path: Path, *, mismatch=False):
    migrate_database(path)
    conn = connect(path)
    try:
        for table, definition in (
            ("batches", "purchase_price REAL NOT NULL DEFAULT 0"),
            ("quotes", "quote_price REAL NOT NULL DEFAULT 0"),
            ("quotes", "received_amount REAL NOT NULL DEFAULT 0"),
            ("payments", "amount REAL NOT NULL DEFAULT 0"),
            ("customers", "balance REAL NOT NULL DEFAULT 0"),
            ("suppliers", "balance REAL NOT NULL DEFAULT 0"),
        ):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')
        conn.execute(
            "UPDATE batches SET purchase_price=purchase_price_cents/100.0"
        )
        conn.execute("UPDATE quotes SET quote_price=quote_price_cents/100.0")
        conn.execute(
            "UPDATE quotes SET received_amount=received_amount_cents/100.0"
        )
        conn.execute("UPDATE payments SET amount=amount_cents/100.0")
        conn.execute("UPDATE customers SET balance=balance_cents/100.0")
        conn.execute("UPDATE suppliers SET balance=balance_cents/100.0")
        if mismatch:
            product = conn.execute(
                "INSERT INTO products(series) VALUES ('mismatch')"
            ).lastrowid
            conn.execute(
                "INSERT INTO batches(product_id,purchase_price_cents,quantity,"
                "remaining,date,purchase_price) VALUES (?,?,?,?,?,?)",
                (product, 10000, 1, 1, "2026-08-04", 100.01),
            )
        conn.execute("DELETE FROM schema_migrations WHERE version=3")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    finally:
        conn.close()


def _seed_flow(path: Path):
    migrate_database(path)
    product = ProductService(path).create(series="Y7000P", cpu="i7")
    customer = CustomerService(path).create(name="审计客户")
    supplier = SupplierService(path).create(name="审计上游")
    FinanceService(path).initialize_finance(
        "2026-08-04",
        [{"name": "测试账户", "opening_balance_cents": 0}],
    )
    batch = InventoryService(path).receive_batch(
        product_id=product,
        purchase_price_cents=500_000,
        quantity=3,
        date="2026-08-04",
        supplier_id=supplier,
    )
    quote = OrderService(path).create_quote(
        batch_id=batch,
        customer_id=customer,
        quote_price_cents=550_000,
        quote_quantity=2,
        quote_date="2026-08-04",
    )
    InventoryService(path).ship_quote(quote, "SN001\nSN002")
    return product, customer, supplier, batch, quote


def _funds_account(path: Path) -> int:
    conn = connect(path, read_only=True)
    try:
        return int(
            conn.execute(
                "SELECT id FROM ledger_accounts "
                "WHERE is_system=0 AND is_active=1 ORDER BY id LIMIT 1"
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_fresh_database_is_schema_v4_without_real_money_columns(tmp_path):
    path = tmp_path / "fresh.db"
    assert migrate_database(path) is None
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        assert "purchase_price" not in _columns(conn, "batches")
        assert {"quote_price", "received_amount"}.isdisjoint(_columns(conn, "quotes"))
        assert "amount" not in _columns(conn, "payments")
        assert "balance" not in _columns(conn, "customers")
        assert "balance" not in _columns(conn, "suppliers")
    finally:
        conn.close()


def test_v2_migration_drops_real_columns_and_creates_hashed_backup(tmp_path):
    path = tmp_path / "v2.db"
    _make_v2(path)
    backup = migrate_database(path)
    assert backup and backup.path.exists() and len(backup.sha256) == 64
    assert backup.path.name.startswith("pre_migration_v1.17_")
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "purchase_price" not in _columns(conn, "batches")
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def test_v2_money_mismatch_rolls_back_and_reports_record(tmp_path):
    path = tmp_path / "bad.db"
    _make_v2(path, mismatch=True)
    with pytest.raises(DatabaseMigrationError, match=r"batches#\d+"):
        migrate_database(path)
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "purchase_price" in _columns(conn, "batches")
    finally:
        conn.close()


def test_version_zero_database_runs_v2_v3_and_v4_chain(tmp_path):
    path = tmp_path / "legacy.db"
    _make_v2(path)
    conn = connect(path)
    conn.execute("PRAGMA user_version=0")
    conn.commit()
    conn.close()
    migrate_database(path)
    conn = connect(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        versions = {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        assert {1, 2, 3, 4, 5}.issubset(versions)
    finally:
        conn.close()


def test_unknown_future_version_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    migrate_database(path)
    conn = connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseMigrationError, match="高于程序支持"):
        migrate_database(path)


def test_restoring_v2_backup_upgrades_it_to_v4(tmp_path):
    backup = tmp_path / "old-v2.db"
    _make_v2(backup)
    target = tmp_path / "current.db"
    migrate_database(target)
    restore_database(backup, target)
    conn = connect(target, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "amount" not in _columns(conn, "payments")
    finally:
        conn.close()


def test_service_flow_uses_cents_and_builds_fifo_audit_chain(tmp_path):
    path = tmp_path / "flow.db"
    _, customer, _, _, quote = _seed_flow(path)
    receipt = PaymentService(path).receive_customer_payment(
        customer,
        600_000,
        "2026-08-04",
        "微信",
        "首款",
        account_id=_funds_account(path),
    )
    conn = connect(path, read_only=True)
    try:
        quote_row = conn.execute(
            "SELECT received_amount_cents,status FROM quotes WHERE id=?", (quote,)
        ).fetchone()
        assert quote_row["received_amount_cents"] == 600_000
        assert quote_row["status"] == "已出库"
        assert conn.execute(
            "SELECT SUM(amount_cents) FROM payment_allocations WHERE payment_id=?",
            (receipt.payment_id,),
        ).fetchone()[0] == 600_000
    finally:
        conn.close()
    events = list_financial_audit_events(
        keyword="审计客户", date_from="2026-08-01", date_to="2099-01-01", db_path=path
    )
    receive = next(row for row in events if row["action"] == "receive" and row["entity_type"] == "payments")
    assert receive["amount_cents"] == 600_000
    detail = get_financial_audit_detail(receive["id"], path)
    assert detail and detail["allocations"][0]["quote_id"] == quote


def test_void_and_correction_are_immutable_and_visible_in_audit(tmp_path):
    path = tmp_path / "correct.db"
    _, customer, _, _, _ = _seed_flow(path)
    service = PaymentService(path)
    original = service.receive_customer_payment(
        customer,
        500_000,
        "2026-08-04",
        "微信",
        account_id=_funds_account(path),
    ).payment_id
    replacement = service.correct_payment(
        original,
        amount_cents=600_000,
        pay_date="2026-08-04",
        method="转账",
        remark="更正后",
        reason="录入错误",
    )
    flow = get_payment_flow(db_path=path)
    assert next(row for row in flow if row["id"] == original)["ledger_status"] == "已冲销"
    assert next(row for row in flow if row["id"] == replacement)["ledger_status"] == "更正"
    events = list_financial_audit_events(
        date_from="2026-08-01", date_to="2099-01-01", db_path=path
    )
    assert {"void", "correct"}.issubset({row["action"] for row in events})
    correction = next(row for row in events if row["action"] == "correct")
    detail = get_financial_audit_detail(correction["id"], path)
    assert detail and len(detail["payments"]) == 3


def test_supplier_payment_audit_keeps_deleted_party_name(tmp_path):
    path = tmp_path / "supplier.db"
    _, _, supplier, _, _ = _seed_flow(path)
    PaymentService(path).record_supplier_payment(
        supplier,
        100_000,
        "2026-08-04",
        "转账",
        account_id=_funds_account(path),
    )
    SupplierService(path).delete(supplier, "停止合作")
    events = list_financial_audit_events(
        keyword="审计上游", date_from="2026-08-01", date_to="2099-01-01", db_path=path
    )
    assert any(row["action"] == "pay" and row["object_name"] == "审计上游" for row in events)


def test_reconciliation_checks_integer_cent_types(tmp_path):
    path = tmp_path / "reconcile.db"
    _seed_flow(path)
    report = ReconciliationService(path).run()
    assert not any(issue.code == "CENTS_TYPE" for issue in report.issues)


def test_v115_json_is_cents_only_and_roundtrips(tmp_path):
    source = tmp_path / "source.db"
    _, customer, _, _, _ = _seed_flow(source)
    PaymentService(source).receive_customer_payment(
        customer,
        500_000,
        "2026-08-04",
        "微信",
        account_id=_funds_account(source),
    )
    exported = tmp_path / "backup.json"
    export_all_to_json(exported, source)
    document = json.loads(exported.read_text(encoding="utf-8"))
    assert document["schema_version"] == 6
    assert document["money_unit"] == "cents"
    assert "amount" not in document["data"]["payments"][0]
    assert "quote_price" not in document["data"]["quotes"][0]

    target = tmp_path / "target.db"
    migrate_database(target)
    success, _, stats = import_from_json(exported, target)
    assert success and stats["payments"] == 1
    conn = connect(target, read_only=True)
    try:
        assert conn.execute("SELECT amount_cents FROM payments").fetchone()[0] == 500_000
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 5
    finally:
        conn.close()


def test_legacy_json_money_import_and_subcent_rejection(tmp_path):
    target = tmp_path / "legacy-target.db"
    migrate_database(target)
    payload = {
        "version": "v1.04",
        "data": {
            "products": [{"id": 1, "series": "Legacy"}],
            "suppliers": [],
            "customers": [],
            "batches": [{
                "id": 1, "product_id": 1, "purchase_price": 1234.56,
                "quantity": 1, "remaining": 1, "date": "2024-01-01",
            }],
            "quotes": [],
            "payments": [],
        },
    }
    file = tmp_path / "legacy.json"
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert import_from_json(file, target)[0]
    conn = connect(target, read_only=True)
    assert conn.execute("SELECT purchase_price_cents FROM batches").fetchone()[0] == 123456
    conn.close()

    payload["data"]["batches"][0]["purchase_price"] = 1.001
    file.write_text(json.dumps(payload), encoding="utf-8")
    assert not import_from_json(file, target)[0]


def test_no_runtime_legacy_database_facade_or_imports():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src" / "models" / "database.py").exists()
    offenders = []
    for path in (root / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "models.database" in source or "models import database" in source:
            offenders.append(path)
    assert offenders == []


def test_runtime_sql_boundaries_and_no_legacy_money_sql():
    root = Path(__file__).resolve().parents[1] / "src"
    for folder in ("ui", "utils", "services"):
        for path in (root / folder).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert ".execute(" not in source, path
    forbidden_sql = (
        "q.quote_price,",
        "b.purchase_price,",
        "q.received_amount,",
        "p.amount,",
        "SET balance=",
    )
    for path in root.rglob("*.py"):
        if path.name in {"migrations.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for fragment in forbidden_sql:
            assert fragment not in source, (path, fragment)


def test_reporting_and_export_tools_read_cents_model(tmp_path):
    path = tmp_path / "reports.db"
    _seed_flow(path)
    report = get_monthly_report(2026, 8, path)
    assert report["total_revenue_cents"] == 1_100_000
    assert report["total_profit_cents"] == 100_000

    history = get_quote_history("Y7000P", db_path=path)
    assert history and history["avg_price"] == 5500
    stale = get_stale_quotes(stale_days=0, db_path=path)
    assert stale["total"] >= 1

    snapshot_id, count = save_snapshot(
        [{"series": "Y7000P", "cpu": "i7"}],
        "2026-08-04",
        path,
    )
    assert snapshot_id and count == 1
    assert get_latest_snapshot(db_path=path)["items"][0]["series"] == "Y7000P"

    output = tmp_path / "quotes.xlsx"
    export_quotes_to_excel(export_quotes(db_path=path), output)
    assert output.exists() and output.stat().st_size > 0


def test_finance_tab_exposes_audit_subpage(qapp, tmp_path):
    from src.ui.finance_tab import FinanceTab

    path = tmp_path / "ui.db"
    _seed_flow(path)
    tab = FinanceTab(db_path=path)
    tab.refresh()
    assert tab.section_tabs.count() == 5
    assert tab.section_tabs.tabText(4) == "流水审计"
    assert tab.audit_table.rowCount() >= 2
