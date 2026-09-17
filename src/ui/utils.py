"""工具函数"""
import os
import sys
import re
import traceback
from contextlib import contextmanager
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidgetItem


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


def _global_excepthook(exc_type, exc_value, exc_tb):
    """全局异常钩子：将未捕获异常写入 crash.log 并显示错误对话框"""
    from PyQt6.QtWidgets import QMessageBox
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    crash_msg = "".join(tb_lines)
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] CRASH\n")
            f.write(crash_msg)
    except Exception:
        print(crash_msg, file=sys.stderr)
    QMessageBox.critical(
        None, "程序异常",
        f"程序发生未处理的异常:\n\n{exc_value}\n\n详细信息已写入 crash.log"
    )
