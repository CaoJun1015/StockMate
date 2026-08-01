"""v1.13 migration, services, cents, and history-protection tests."""

import shutil
import sqlite3

import pytest

from src.models.connection import connect, create_backup
from src.models.migrations import DatabaseMigrationError, migrate_database
from src.models.schema import SCHEMA_VERSION
from src.models.queries import get_payment_flow
from src.services.exceptions import InvalidTransitionError, ValidationError
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.payment_service import PaymentService
from src.utils.json_export import export_all_to_json, import_from_json


def _fresh_database(path):
    migrate_database(path)
    return connect(path)


def _seed_order(path, *, quantity=3, quote_price_cents=550_000):
    conn = _fresh_database(path)
    try:
        product_id = conn.execute(
            "INSERT INTO products(series) VALUES ('Y7000P')"
        ).lastrowid
        customer_id = conn.execute(
            "INSERT INTO customers(name) VALUES ('测试客户')"
        ).lastrowid
        supplier_id = conn.execute(
            "INSERT INTO suppliers(name) VALUES ('测试上游')"
        ).lastrowid
        batch_id = conn.execute(
            "INSERT INTO batches(product_id,purchase_price,purchase_price_cents,"
            "quantity,remaining,date,supplier_id) VALUES (?,?,?,?,?,?,?)",
            (product_id, 5000, 500_000, 10, 10, "2026-08-01", supplier_id),
        ).lastrowid
        quote_id = conn.execute(
            "INSERT INTO quotes(batch_id,customer_id,quote_price,quote_price_cents,"
            "quote_quantity,quote_date,paid,status,received_amount,received_amount_cents) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                customer_id,
                quote_price_cents / 100,
                quote_price_cents,
                quantity,
                "2026-08-01",
                "否",
                "已报价",
                0,
                0,
            ),
        ).lastrowid
        conn.commit()
        return product_id, customer_id, supplier_id, batch_id, quote_id
    finally:
        conn.close()


def test_fresh_database_is_schema_v2(tmp_path):
    db_path = tmp_path / "fresh.db"
    assert migrate_database(db_path) is None
    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='payment_allocations'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='audit_events'"
        ).fetchone()
    finally:
        conn.close()


def test_unknown_future_schema_is_rejected(tmp_path):
    db_path = tmp_path / "future.db"
    migrate_database(db_path)
    conn = connect(db_path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseMigrationError, match="高于程序支持"):
        migrate_database(db_path)


def test_failed_money_migration_rolls_back_completely(tmp_path):
    db_path = tmp_path / "rollback.db"
    *_, batch_id, _ = _seed_order(db_path)
    conn = connect(db_path)
    conn.execute(
        "UPDATE batches SET purchase_price=5000.001,purchase_price_cents=500000 "
        "WHERE id=?",
        (batch_id,),
    )
    conn.execute("PRAGMA user_version=0")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseMigrationError, match="超过两位小数") as error:
        migrate_database(db_path)
    assert error.value.backup and error.value.backup.path.exists()

    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        row = conn.execute(
            "SELECT purchase_price,purchase_price_cents FROM batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        assert tuple(row) == (5000.001, 500000)
    finally:
        conn.close()


def test_migration_is_idempotent_on_production_shape_copy(tmp_path):
    source = __import__("pathlib").Path(__file__).parents[1] / "data" / "diaohuo.db"
    copy = tmp_path / "copy.db"
    shutil.copy2(source, copy)
    migrate_database(copy)
    migrate_database(copy)
    conn = connect(copy)
    try:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 353
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 242
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 184
        assert conn.execute(
            "SELECT SUM(amount_cents) FROM payment_allocations"
        ).fetchone()[0] == 116_198_500
        assert conn.execute(
            "SELECT SUM(received_amount_cents) FROM quotes"
        ).fetchone()[0] == 116_198_500
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_sqlite_backup_includes_wal_and_has_hash(tmp_path):
    db_path = tmp_path / "source.db"
    conn = _fresh_database(db_path)
    conn.execute("INSERT INTO products(series) VALUES ('未检查点记录')")
    conn.commit()
    info = create_backup(db_path, prefix="test_backup", retain=False)
    conn.close()
    assert info and info.path.exists() and len(info.sha256) == 64
    backup = sqlite3.connect(info.path)
    try:
        assert backup.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        backup.close()


def test_inventory_ship_and_order_cancel_are_atomic(tmp_path):
    db_path = tmp_path / "flow.db"
    *_, batch_id, quote_id = _seed_order(db_path, quantity=2)
    InventoryService(db_path).ship_quote(quote_id, "SN1,SN2")
    conn = connect(db_path)
    assert conn.execute("SELECT remaining FROM batches WHERE id=?", (batch_id,)).fetchone()[0] == 8
    conn.close()
    OrderService(db_path).cancel_quote(quote_id, "客户取消")
    conn = connect(db_path)
    try:
        quote = conn.execute("SELECT status,sn_list FROM quotes WHERE id=?", (quote_id,)).fetchone()
        assert tuple(quote) == ("已取消", "")
        assert conn.execute("SELECT remaining FROM batches WHERE id=?", (batch_id,)).fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    finally:
        conn.close()


def test_payment_fifo_void_and_correction_preserve_ledger(tmp_path):
    db_path = tmp_path / "payments.db"
    _, customer_id, _, _, first_quote = _seed_order(db_path, quantity=1)
    conn = connect(db_path)
    batch_id = conn.execute("SELECT batch_id FROM quotes WHERE id=?", (first_quote,)).fetchone()[0]
    second_quote = conn.execute(
        "INSERT INTO quotes(batch_id,customer_id,quote_price,quote_price_cents,"
        "quote_quantity,quote_date,paid,status,received_amount,received_amount_cents) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (batch_id, customer_id, 6000, 600_000, 1, "2026-08-02", "否", "已报价", 0, 0),
    ).lastrowid
    conn.commit()
    conn.close()

    service = PaymentService(db_path)
    receipt = service.receive_customer_payment(
        customer_id, 700_000, "2026-08-03", "转账", "首款"
    )
    conn = connect(db_path)
    allocations = conn.execute(
        "SELECT quote_id,amount_cents FROM payment_allocations "
        "WHERE payment_id=? ORDER BY id",
        (receipt.payment_id,),
    ).fetchall()
    assert [tuple(row) for row in allocations] == [
        (first_quote, 550_000),
        (second_quote, 150_000),
    ]
    conn.close()

    service.void_payment(receipt.payment_id, "录入错误")
    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT SUM(received_amount_cents) FROM quotes"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM payments WHERE reversal_of_id=?",
            (receipt.payment_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()

    replacement = service.receive_customer_payment(
        customer_id, 600_000, "2026-08-04", "转账", "正确流水"
    ).payment_id
    corrected = service.correct_payment(
        replacement,
        amount_cents=500_000,
        pay_date="2026-08-04",
        method="转账",
        remark="更正金额",
        reason="金额录错",
    )
    flow = get_payment_flow(db_path=db_path)
    assert any(row["id"] == corrected and row["ledger_status"] == "更正" for row in flow)


def test_overpayment_and_illegal_delete_are_rejected(tmp_path):
    db_path = tmp_path / "validation.db"
    _, customer_id, _, _, quote_id = _seed_order(db_path, quantity=1)
    with pytest.raises(ValidationError, match="超过"):
        PaymentService(db_path).receive_customer_payment(
            customer_id, 600_000, "2026-08-03", "现金"
        )
    InventoryService(db_path).ship_quote(quote_id)
    with pytest.raises(InvalidTransitionError):
        OrderService(db_path).delete_quote(quote_id, "误删")


def test_v113_json_roundtrip_preserves_cents_allocations_and_audit(tmp_path, monkeypatch):
    import src.models.database as database

    source = tmp_path / "json-source.db"
    _, customer_id, _, _, quote_id = _seed_order(source, quantity=1)
    InventoryService(source).ship_quote(quote_id, "SN-JSON")
    PaymentService(source).receive_customer_payment(
        customer_id, 550_000, "2026-08-03", "转账", "JSON往返"
    )
    export_path = tmp_path / "backup.json"
    monkeypatch.setattr(database, "get_connection", lambda: connect(source))
    export_all_to_json(database, export_path)

    target = tmp_path / "json-target.db"
    migrate_database(target)
    monkeypatch.setattr(database, "get_connection", lambda: connect(target))
    success, _, stats = import_from_json(export_path, database)
    assert success is True
    assert stats["quotes"] == 1

    conn = connect(target)
    try:
        assert conn.execute("SELECT quote_price_cents FROM quotes").fetchone()[0] == 550_000
        assert conn.execute("SELECT amount_cents FROM payments").fetchone()[0] == 550_000
        assert conn.execute(
            "SELECT amount_cents FROM payment_allocations"
        ).fetchone()[0] == 550_000
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 2
    finally:
        conn.close()
