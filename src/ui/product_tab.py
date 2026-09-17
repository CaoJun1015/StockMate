"""产品管理 Tab"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton,
    QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt

from src.models.queries import list_products
from src.services.exceptions import ServiceError
from src.services.product_service import ProductService
from src.ui.dialogs import ProductEditDialog
from src.ui.utils import NumericTableWidgetItem, refreshing_table


class ProductTab(QWidget):
    """产品管理 Tab，嵌入 MainWindow"""
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.current_product_id = None
        self.product_table = None
        self.add_product_btn = None
        self.edit_product_btn = None
        self.del_product_btn = None
        self.refresh_product_list_btn = None
        self.product_service = ProductService()
        self._build_ui()
    
    def _build_ui(self):
        """构建产品管理 Tab 的 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        self.add_product_btn = QPushButton("+ 新增机型")
        self.add_product_btn.setObjectName("primaryBtn")
        self.add_product_btn.clicked.connect(self.on_add_product)
        self.edit_product_btn = QPushButton("编辑")
        self.edit_product_btn.setObjectName("ghostBtn")
        self.edit_product_btn.clicked.connect(self.on_edit_product)
        self.del_product_btn = QPushButton("删除")
        self.del_product_btn.setObjectName("dangerBtn")
        self.del_product_btn.clicked.connect(self.on_delete_product)
        btn_row.addWidget(self.add_product_btn)
        btn_row.addWidget(self.edit_product_btn)
        btn_row.addWidget(self.del_product_btn)
        self.refresh_product_list_btn = QPushButton("刷新")
        self.refresh_product_list_btn.setObjectName("ghostBtn")
        self.refresh_product_list_btn.clicked.connect(lambda: self.refresh_product_list(self.main.search_edit.text().strip()))
        btn_row.addWidget(self.refresh_product_list_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.product_table = QTableWidget()
        self.product_table.setAlternatingRowColors(True)
        self.product_table.setColumnCount(9)
        self.product_table.setHorizontalHeaderLabels(["ID", "系列", "CPU", "内存", "硬盘", "显卡", "屏幕", "备注", "库存"])
        self.product_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.product_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.product_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.product_table.horizontalHeader().setStretchLastSection(True)
        self.product_table.setColumnHidden(0, True)
        self.product_table.itemSelectionChanged.connect(self.on_product_selected)
        layout.addWidget(self.product_table)

    def refresh_product_list(self, keyword=None):
        products = list_products(keyword or "")

        with refreshing_table(self.product_table, key_column=0):
            self.product_table.setRowCount(len(products))
            for i, p in enumerate(products):
                self.product_table.setItem(i, 0, QTableWidgetItem(str(p["id"])))
                self.product_table.setItem(i, 1, QTableWidgetItem(p.get("series", "")))
                self.product_table.setItem(i, 2, QTableWidgetItem(p.get("cpu", "")))
                self.product_table.setItem(i, 3, QTableWidgetItem(p.get("ram", "")))
                self.product_table.setItem(i, 4, QTableWidgetItem(p.get("storage", "")))
                self.product_table.setItem(i, 5, QTableWidgetItem(p.get("gpu", "")))
                self.product_table.setItem(i, 6, QTableWidgetItem(p.get("screen", "")))
                self.product_table.setItem(i, 7, QTableWidgetItem(p.get("note", "")))
                remaining = p.get("total_remaining", 0)
                self.product_table.setItem(i, 8, NumericTableWidgetItem(remaining, remaining))

            self.product_table.resizeColumnsToContents()
            self.product_table.setColumnWidth(1, 180)
        self.main.status_label.setText(f"共 {len(products)} 条机型")

    def on_search(self, text):
        self.refresh_product_list(text.strip())

    def on_product_selected(self):
        row = self.product_table.currentRow()
        if row < 0:
            return
        pid = int(self.product_table.item(row, 0).text())
        series = self.product_table.item(row, 1).text()
        cpu = self.product_table.item(row, 2).text()
        ram = self.product_table.item(row, 3).text()
        storage = self.product_table.item(row, 4).text()
        gpu = self.product_table.item(row, 5).text()
        screen = self.product_table.item(row, 6).text()
        note_item = self.product_table.item(row, 7)
        note = note_item.text() if note_item else ""
        self.main.quote_panel.load_product(pid, series, cpu, ram, storage, gpu, screen, note)

    def on_add_product(self):
        dlg = ProductEditDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if not data["series"]:
                QMessageBox.warning(self, "提示", "系列名称不能为空")
                return
            try:
                self.product_service.create(**data)
            except ServiceError as exc:
                QMessageBox.warning(self, "新增失败", str(exc))
                return
            self.refresh_product_list(self.main.search_edit.text().strip())

    def on_edit_product(self):
        row = self.product_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个机型")
            return
        pid = int(self.product_table.item(row, 0).text())
        note_item = self.product_table.item(row, 7)
        current = {
            "series": self.product_table.item(row, 1).text(),
            "cpu": self.product_table.item(row, 2).text(),
            "ram": self.product_table.item(row, 3).text(),
            "storage": self.product_table.item(row, 4).text(),
            "gpu": self.product_table.item(row, 5).text(),
            "screen": self.product_table.item(row, 6).text(),
            "note": note_item.text() if note_item else "",
        }
        dlg = ProductEditDialog(self, current)
        if dlg.exec():
            data = dlg.get_data()
            if not data["series"]:
                QMessageBox.warning(self, "提示", "系列名称不能为空")
                return
            try:
                self.product_service.update(pid, **data)
            except ServiceError as exc:
                QMessageBox.warning(self, "编辑失败", str(exc))
                return
            self.refresh_product_list(self.main.search_edit.text().strip())

    def on_delete_product(self):
        row = self.product_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个机型")
            return
        pid = int(self.product_table.item(row, 0).text())
        series = self.product_table.item(row, 1).text()
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除机型「{series}」及其所有库存记录？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.product_service.delete(pid)
            except ServiceError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return
            self.refresh_product_list(self.main.search_edit.text().strip())
