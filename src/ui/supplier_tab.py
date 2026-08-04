"""供应商管理 Tab"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QLineEdit, QGroupBox, QMessageBox, QAbstractItemView,
)

from src.models.database import (
    add_operation_log, get_all_suppliers, search_suppliers,
)
from src.models.queries import (
    get_supplier,
    get_supplier_purchase_history,
    get_supplier_reference_counts,
)
from src.services.exceptions import ServiceError
from src.services.party_service import SupplierService
from src.ui.dialogs import CustomerDialog


class SupplierTab(QWidget):
    """供应商管理 Tab，嵌入 MainWindow"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.supplier_table = None
        self.supplier_search = None
        self.supplier_stats_label = None
        self.supplier_history_table = None
        self.supplier_service = SupplierService()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 新增上游")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self.on_add_supplier)
        del_btn = QPushButton("删除上游")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self.on_delete_supplier)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()

        self.supplier_search = QLineEdit()
        self.supplier_search.setObjectName("globalSearch")
        self.supplier_search.setPlaceholderText("搜索上游...")
        self.supplier_search.textChanged.connect(self.refresh_supplier_list)
        btn_row.addWidget(self.supplier_search)
        layout.addLayout(btn_row)

        self.supplier_table = QTableWidget()
        self.supplier_table.setAlternatingRowColors(True)
        self.supplier_table.setColumnCount(6)
        self.supplier_table.setHorizontalHeaderLabels(["ID", "名称", "微信", "QQ", "电话", "备注"])
        self.supplier_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.supplier_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.supplier_table.setColumnHidden(0, True)
        self.supplier_table.horizontalHeader().setStretchLastSection(True)
        self.supplier_table.cellClicked.connect(self.on_supplier_cell_clicked)
        self.supplier_table.doubleClicked.connect(self.on_edit_supplier_from_table)
        layout.addWidget(self.supplier_table)

        history_group = QGroupBox("采购历史")
        history_layout = QVBoxLayout(history_group)

        self.supplier_stats_label = QLabel("请选择上游查看采购历史")
        self.supplier_stats_label.setObjectName("summaryLabel")
        history_layout.addWidget(self.supplier_stats_label)

        self.supplier_history_table = QTableWidget()
        self.supplier_history_table.setAlternatingRowColors(True)
        self.supplier_history_table.setColumnCount(7)
        self.supplier_history_table.setHorizontalHeaderLabels(["入库日期", "机型", "CPU", "数量", "购入价", "总金额", "备注"])
        self.supplier_history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.supplier_history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.supplier_history_table.horizontalHeader().setStretchLastSection(True)
        history_layout.addWidget(self.supplier_history_table)

        layout.addWidget(history_group)

    def on_supplier_cell_clicked(self, row, col):
        if row < 0:
            self.supplier_stats_label.setText("请选择上游查看采购历史")
            self.supplier_history_table.setRowCount(0)
            return

        sid = int(self.supplier_table.item(row, 0).text())
        supplier_name = self.supplier_table.item(row, 1).text()

        rows = get_supplier_purchase_history(sid)

        total_amount = 0
        self.supplier_history_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            price = r.get("purchase_price", 0) or 0
            quantity = r.get("quantity", 0) or 0
            total_amount += price * quantity
            self.supplier_history_table.setItem(i, 0, QTableWidgetItem(r.get("date") or ""))
            self.supplier_history_table.setItem(i, 1, QTableWidgetItem(r.get("series") or ""))
            self.supplier_history_table.setItem(i, 2, QTableWidgetItem(r.get("cpu") or ""))
            self.supplier_history_table.setItem(i, 3, QTableWidgetItem(str(quantity)))
            self.supplier_history_table.setItem(i, 4, QTableWidgetItem(f"¥{price:.0f}"))
            self.supplier_history_table.setItem(i, 5, QTableWidgetItem(f"¥{price * quantity:.0f}"))
            self.supplier_history_table.setItem(i, 6, QTableWidgetItem(r.get("remark") or ""))
        self.supplier_history_table.resizeColumnsToContents()

        self.supplier_stats_label.setText(
            f"上游: {supplier_name} | 总批次数: {len(rows)} | 总金额: ¥{total_amount:.0f}"
        )

    def refresh_supplier_list(self):
        keyword = self.supplier_search.text().strip()
        suppliers = search_suppliers(keyword) if keyword else get_all_suppliers()
        self.supplier_table.setRowCount(len(suppliers))
        for i, s in enumerate(suppliers):
            self.supplier_table.setItem(i, 0, QTableWidgetItem(str(s["id"])))
            self.supplier_table.setItem(i, 1, QTableWidgetItem(s["name"]))
            self.supplier_table.setItem(i, 2, QTableWidgetItem(s.get("wechat", "")))
            self.supplier_table.setItem(i, 3, QTableWidgetItem(s.get("qq", "")))
            self.supplier_table.setItem(i, 4, QTableWidgetItem(s.get("phone", "")))
            self.supplier_table.setItem(i, 5, QTableWidgetItem(s.get("note", "")))
        self.supplier_table.resizeColumnsToContents()

    def on_add_supplier(self):
        dlg = CustomerDialog(self)
        dlg.setWindowTitle("新增上游")
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                QMessageBox.warning(self, "提示", "上游名称不能为空")
                return
            try:
                supplier_id = self.supplier_service.create(**data)
            except ServiceError as exc:
                QMessageBox.warning(self, "新增失败", str(exc))
                return
            add_operation_log("新增供应商", "suppliers", supplier_id, f"名称={data['name']}")
            self.refresh_supplier_list()

    def on_edit_supplier_from_table(self):
        row = self.supplier_table.currentRow()
        if row < 0:
            return
        sid = int(self.supplier_table.item(row, 0).text())
        supplier = get_supplier(sid)
        if not supplier:
            QMessageBox.warning(self, "提示", "上游不存在或已删除")
            self.refresh_supplier_list()
            return
        current = {
            "name": supplier["name"] or "",
            "wechat": supplier["wechat"] or "",
            "qq": supplier["qq"] or "",
            "phone": supplier["phone"] or "",
            "note": supplier["note"] or "",
        }
        dlg = CustomerDialog(self, current)
        dlg.setWindowTitle("编辑上游")
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                return
            try:
                self.supplier_service.update(sid, **data)
            except ServiceError as exc:
                QMessageBox.warning(self, "编辑失败", str(exc))
                return
            add_operation_log("编辑供应商", "suppliers", sid, f"名称={data['name']}")
            self.refresh_supplier_list()

    def on_delete_supplier(self):
        row = self.supplier_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个上游")
            return
        sid = int(self.supplier_table.item(row, 0).text())
        name = self.supplier_table.item(row, 1).text()
        references = get_supplier_reference_counts(sid)
        batch_count = references["batches"]
        payment_count = references["payments"]
        if batch_count > 0 or payment_count > 0:
            reply = QMessageBox.question(
                self, "确认删除",
                f"上游「{name}」有 {batch_count} 条关联批次和 {payment_count} 条付款记录。\n\n"
                "删除后上游将从工作列表隐藏，采购与付款历史仍会保留。\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        else:
            reply = QMessageBox.question(
                self, "确认删除", f"确定删除上游「{name}」？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.supplier_service.delete(sid)
            except ServiceError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return
            add_operation_log("删除供应商", "suppliers", sid, f"名称={name}")
            self.refresh_supplier_list()
            self.main.record_tab.refresh_records()
