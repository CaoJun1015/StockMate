"""Focused checks for the packaging/UI closeout behavior."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QMessageBox, QTableWidgetItem


def test_crash_log_is_in_user_data_and_rotates(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAOHUO_DATA_DIR", str(tmp_path / "user-data"))
    from src.ui.utils import _write_crash_log

    first = _write_crash_log(ValueError, ValueError("first"), None)
    assert first == tmp_path / "user-data" / "logs" / "crash.log"
    assert "应用版本" in first.read_text(encoding="utf-8")

    first.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    _write_crash_log(RuntimeError, RuntimeError("second"), None)
    assert (first.parent / "crash.log.1").stat().st_size > 2 * 1024 * 1024
    assert "异常消息：second" in first.read_text(encoding="utf-8")


def test_frozen_data_dir_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.delenv("DIAOHUO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    from src.models.connection import get_data_dir

    assert get_data_dir() == (tmp_path / "LocalAppData" / "StockMate").resolve()


def test_record_actions_follow_selected_status(qapp):
    from main import MainWindow

    window = MainWindow()
    try:
        tab = window.record_tab
        tab._all_quotes = [{
            "id": 42,
            "quote_price_cents": 10000,
            "quote_quantity": 2,
            "returned_revenue_cents": 3000,
            "net_total_cents": 17000,
            "received_amount_cents": 5000,
        }]
        tab.record_table.setRowCount(1)
        values = ["42", "2026-09-22", "张三", "X1", "", "", "", "", "", "", "2", "100", "已出库", "50", "", "", "否"]
        for column, value in enumerate(values):
            tab.record_table.setItem(0, column, QTableWidgetItem(value))
        tab.record_table.selectRow(0)
        tab._update_action_states()

        assert tab.receive_btn.isEnabled()
        assert tab.return_btn.isEnabled()
        assert tab.confirm_record_btn.isEnabled() is False
        assert "#42" in tab.selection_label.text()
        assert "退货后净应收 ¥170" in tab.selection_label.text()
    finally:
        window.close()


def test_about_menu_reports_development_status(qapp, monkeypatch):
    from main import MainWindow

    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, text: messages.append((title, text)),
    )
    window = MainWindow()
    try:
        window.on_about()
    finally:
        window.close()
    assert messages
    assert "开发版" in messages[0][1]
    assert "schema 版本：7" in messages[0][1]
