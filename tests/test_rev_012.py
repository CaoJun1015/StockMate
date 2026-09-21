"""Qt worker lifecycle and safe export failure handling."""

from __future__ import annotations

import time

from openpyxl import Workbook
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QLabel, QMessageBox, QWidget

from src.main import MainWindow
import src.utils.json_export as json_export
from src.ui.background_tasks import has_background_tasks, start_background_task
from src.utils.excel_export import _save_workbook_atomically


def test_background_task_keeps_qt_responsive_and_rejects_duplicate(qapp, qtbot):
    owner = QWidget()
    label = QLabel("waiting", owner)
    qtbot.addWidget(owner)
    responsive = []
    QTimer.singleShot(100, lambda: responsive.append(True))
    assert start_background_task(
        owner, "slow", lambda: (time.sleep(2), "done")[1],
        lambda value: label.setText(value), lambda message: label.setText(message),
    )
    assert not start_background_task(owner, "slow", lambda: "again", lambda _: None, lambda _: None)
    qtbot.wait(250)
    assert responsive == [True]
    assert has_background_tasks(owner)
    qtbot.waitUntil(lambda: label.text() == "done", timeout=3_000)
    qtbot.waitUntil(lambda: not has_background_tasks(owner), timeout=1_000)


def test_background_task_reports_failure_and_atomic_exports_preserve_existing_file(qapp, qtbot, tmp_path, monkeypatch):
    owner = QWidget()
    qtbot.addWidget(owner)
    errors = []
    assert start_background_task(
        owner, "fail", lambda: (_ for _ in ()).throw(RuntimeError("expected failure")),
        lambda _: None, errors.append,
    )
    qtbot.waitUntil(lambda: errors == ["expected failure"], timeout=1_000)

    target = tmp_path / "existing.xlsx"
    target.write_bytes(b"known-good")
    workbook = Workbook()
    monkeypatch.setattr(workbook, "save", lambda _: (_ for _ in ()).throw(OSError("write failed")))
    try:
        _save_workbook_atomically(workbook, target)
    except OSError:
        pass
    assert target.read_bytes() == b"known-good"

    json_target = tmp_path / "existing.json"
    json_target.write_text("known-good", encoding="utf-8")
    monkeypatch.setattr(json_export.json, "dump", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")))
    try:
        json_export.export_all_to_json(json_target)
    except OSError:
        pass
    assert json_target.read_text(encoding="utf-8") == "known-good"


def test_main_window_waits_for_running_export_before_closing(qapp, qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    assert start_background_task(window, "slow-close", lambda: time.sleep(0.2), lambda _: None, lambda _: None)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    qtbot.waitUntil(lambda: not has_background_tasks(window), timeout=1_000)
    window.close()
