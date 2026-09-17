"""Debounced list searching and stable full-result exports."""

from __future__ import annotations

from types import SimpleNamespace

import src.ui.record_tab as record_tab_module
from src.ui.record_tab import RecordTab


def test_record_search_debounces_rapid_and_unchanged_input(qapp, qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(record_tab_module, "search_quotes", lambda *args: calls.append(args) or [])
    tab = RecordTab(SimpleNamespace())
    qtbot.addWidget(tab)
    tab.record_search.setText("a")
    tab.record_search.setText("ab")
    tab.record_search.setText("中文")
    assert calls == []
    qtbot.wait(300)
    assert len(calls) == 1
    tab._refresh_debounced_search()
    assert len(calls) == 1
    tab.record_search.clear()
    qtbot.wait(300)
    assert len(calls) == 2


def test_pagination_keeps_full_filtered_result_for_totals_and_export(qapp, qtbot, monkeypatch):
    rows = [
        {
            "id": index, "quote_date": "2026-09-01", "customer_name": "客户",
            "series": "机型", "cpu": "i7", "ram": "", "storage": "", "gpu": "",
            "supplier_name": "", "purchase_price_cents": 1, "quote_quantity": 1,
            "quote_price_cents": 2, "status": "已出库" if not index % 2 else "已报价",
            "received_amount_cents": 0, "sn_list": "", "batch_sn_list": "", "remark": "",
            "batch_remark": "", "paid": "否", "returned_quantity": 0,
            "net_total_cents": 2, "tax_rate": None, "purchase_tax_inclusive": 0,
            "quote_tax_inclusive": 0,
        }
        for index in range(501)
    ]
    monkeypatch.setattr(record_tab_module, "search_quotes", lambda *args: rows)
    captured = []
    monkeypatch.setattr(record_tab_module, "export_quotes_to_excel", lambda values: captured.append(values) or "out.xlsx")
    monkeypatch.setattr(record_tab_module.QMessageBox, "information", lambda *args: None)
    tab = RecordTab(SimpleNamespace())
    qtbot.addWidget(tab)
    tab.refresh_records()
    assert (tab.record_table.rowCount(), tab.page_label.text()) == (250, "第 1/3 页（共 501 条）")
    tab._change_page(1)
    assert (tab.record_table.rowCount(), tab.record_table.item(0, 0).text()) == (250, "250")
    tab._change_page(1)
    assert tab.record_table.rowCount() == 1
    tab.status_filter.setCurrentText("已出库")
    tab.on_export_records_excel()
    assert len(captured[0]) == 251
