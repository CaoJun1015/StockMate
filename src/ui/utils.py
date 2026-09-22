"""工具函数"""
import sys
import re
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidgetItem

from src.models.connection import get_data_dir
from src.models.schema import SCHEMA_VERSION
from src.version import APP_VERSION


class NumericTableWidgetItem(QTableWidgetItem):
    """Keep formatted text while sorting by its integer value."""

    def __init__(self, text, value):
        super().__init__(str(text))
        self.sort_value = value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


@contextmanager
def refreshing_table(table, key_column=None):
    """Fill a sorted table atomically and restore its sort and valid selection."""
    header = table.horizontalHeader()
    sorting = table.isSortingEnabled()
    sort_shown = header.isSortIndicatorShown()
    sort_column = header.sortIndicatorSection()
    sort_order = header.sortIndicatorOrder()
    selected_key = None
    if key_column is not None and table.currentRow() >= 0:
        item = table.item(table.currentRow(), key_column)
        if item:
            selected_key = item.data(Qt.ItemDataRole.UserRole)
            if selected_key is None:
                selected_key = item.text()
    blocked = table.blockSignals(True)
    table.setSortingEnabled(False)
    try:
        yield
    finally:
        table.setSortingEnabled(sorting)
        if sorting and sort_shown:
            table.sortItems(sort_column, sort_order)
        table.blockSignals(blocked)
        if selected_key is not None:
            for row in range(table.rowCount()):
                item = table.item(row, key_column)
                value = item.data(Qt.ItemDataRole.UserRole) if item else None
                if value is None and item:
                    value = item.text()
                if value == selected_key:
                    table.selectRow(row)
                    break


def _validate_date(date_str):
    """校验日期格式为 YYYY-MM-DD，返回 (is_valid, error_message)"""
    if not date_str:
        return False, "日期不能为空"
    pattern = r"^\d{4}-\d{2}-\d{2}$"
    if not re.match(pattern, date_str):
        return False, f"日期格式错误: {date_str}，应为 YYYY-MM-DD"
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, f"无效日期: {date_str}"


def _write_crash_log(exc_type, exc_value, exc_tb) -> Path | None:
    """Write a bounded crash log in the user data directory."""
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    crash_msg = "".join(tb_lines)
    try:
        log_path = get_data_dir() / "logs" / "crash.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
            rotated = log_path.with_name("crash.log.1")
            if rotated.exists():
                rotated.unlink()
            log_path.replace(rotated)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n{'=' * 60}\n")
            stream.write(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            stream.write(f"应用版本：{APP_VERSION}\n")
            stream.write(f"schema 版本：{SCHEMA_VERSION}\n")
            stream.write(f"异常类型：{exc_type.__name__}\n")
            stream.write(f"异常消息：{exc_value}\n")
            stream.write("完整堆栈：\n")
            stream.write(crash_msg)
        return log_path
    except Exception:
        print(crash_msg, file=sys.stderr)
        return None


def _global_excepthook(exc_type, exc_value, exc_tb):
    """Write an uncaught exception to user data and show an error dialog."""
    from PyQt6.QtWidgets import QMessageBox
    log_path = _write_crash_log(exc_type, exc_value, exc_tb)
    log_hint = str(log_path) if log_path else "日志文件"
    QMessageBox.critical(
        None, "程序异常",
        f"程序发生未处理的异常：\n\n{exc_value}\n\n详细信息已写入 {log_hint}"
    )
