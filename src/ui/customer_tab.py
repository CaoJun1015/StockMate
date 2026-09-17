"""客户管理 Tab"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QLineEdit, QGroupBox, QMessageBox, QAbstractItemView,
)

from src.models.queries import (
    get_customer,
    get_customer_reference_counts,
    list_customers,
)
from PyQt6.QtCore import QTimer
from src.models.finance_queries import get_customer_actual_performance
from src.services.exceptions import ServiceError
from src.services.party_service import CustomerService
from src.ui.dialogs import CustomerDialog
from src.ui.utils import refreshing_table
from src.utils.money import format_yuan


class CustomerTab(QWidget):
    """客户管理 Tab，嵌入 MainWindow"""
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.customer_table = None
        self.customer_search = None
        self.customer_stats_label = None
        self.customer_history_table = None
        self.customer_service = CustomerService()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_customer_list)
        self._build_ui()
    
    def _build_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 新增客户")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self.on_add_customer)
        del_btn = QPushButton("删除客户")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self.on_delete_customer)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()

        self.customer_search = QLineEdit()
        self.customer_search.setObjectName("globalSearch")
        self.customer_search.setPlaceholderText("搜索客户...")
        self.customer_search.textChanged.connect(lambda: self._search_timer.start())
        btn_row.addWidget(self.customer_search)
        layout.addLayout(btn_row)

        self.customer_table = QTableWidget()
        self.customer_table.setAlternatingRowColors(True)
        self.customer_table.setColumnCount(6)
        self.customer_table.setHorizontalHeaderLabels(["ID", "名称", "微信", "QQ", "电话", "备注"])
        self.customer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.customer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.customer_table.setColumnHidden(0, True)
        self.customer_table.horizontalHeader().setStretchLastSection(True)
        self.customer_table.doubleClicked.connect(self.on_edit_customer_from_table)
        self.customer_table.cellClicked.connect(self.on_customer_cell_clicked)
        layout.addWidget(self.customer_table)

        history_group = QGroupBox("实际成交历史")
        history_layout = QVBoxLayout(history_group)

        self.customer_stats_label = QLabel("请选择客户查看实际成交历史")
        self.customer_stats_label.setObjectName("summaryLabel")
        history_layout.addWidget(self.customer_stats_label)

        self.customer_history_table = QTableWidget()
        self.customer_history_table.setAlternatingRowColors(True)
        self.customer_history_table.setColumnCount(8)
        self.customer_history_table.setHorizontalHeaderLabels(["业务日期", "机型", "CPU", "净数量", "实际成本", "实际销售", "实际毛利", "备注"])
        self.customer_history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.customer_history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.customer_history_table.horizontalHeader().setStretchLastSection(True)
        self.customer_history_table.setColumnWidth(7, 120)
        history_layout.addWidget(self.customer_history_table)

        layout.addWidget(history_group)
    
    def refresh_customer_list(self):
        keyword = self.customer_search.text().strip()
        customers = list_customers(keyword)
        with refreshing_table(self.customer_table, key_column=0):
            self.customer_table.setRowCount(len(customers))
            for i, c in enumerate(customers):
                self.customer_table.setItem(i, 0, QTableWidgetItem(str(c["id"])))
                self.customer_table.setItem(i, 1, QTableWidgetItem(c["name"]))
                self.customer_table.setItem(i, 2, QTableWidgetItem(c.get("wechat", "")))
                self.customer_table.setItem(i, 3, QTableWidgetItem(c.get("qq", "")))
                self.customer_table.setItem(i, 4, QTableWidgetItem(c.get("phone", "")))
                self.customer_table.setItem(i, 5, QTableWidgetItem(c.get("note", "")))
            self.customer_table.resizeColumnsToContents()

    def on_add_customer(self):
        dlg = CustomerDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                QMessageBox.warning(self, "提示", "客户名称不能为空")
                return
            try:
                customer_id = self.customer_service.create(**data)
            except ServiceError as exc:
                QMessageBox.warning(self, "新增失败", str(exc))
                return
            self.refresh_customer_list()

    def on_customer_cell_clicked(self, row, col):
        if row < 0:
            self.customer_stats_label.setText("请选择客户查看实际成交历史")
            self.customer_history_table.setRowCount(0)
            return
        
        cid = int(self.customer_table.item(row, 0).text())
        customer_name = self.customer_table.item(row, 1).text()
        
        performance = get_customer_actual_performance(cid)
        quotes = performance["history"]
        
        self.customer_stats_label.setText(
            f"客户: {customer_name} | 实际出库: {performance['total_quotes']}单 | "
            f"净销售: {format_yuan(performance['total_amount_cents'])} | "
            f"实际毛利: {format_yuan(performance['total_profit_cents'])}"
            + (" | 账本启用前历史不完整，未计入实际口径" if not performance["history_complete"] else "")
        )
        
        with refreshing_table(self.customer_history_table):
            self.customer_history_table.setRowCount(len(quotes))
            for i, q in enumerate(quotes):
                purchase_price = q.get("actual_cost_cents", 0) or 0
                quote_price = q.get("actual_sales_cents", 0) or 0
                quantity = q.get("actual_quantity", 0) or 0
                profit = q.get("actual_profit_cents", 0) or 0
                self.customer_history_table.setItem(i, 0, QTableWidgetItem(q.get("business_date", "")))
                self.customer_history_table.setItem(i, 1, QTableWidgetItem(q.get("series", "")))
                self.customer_history_table.setItem(i, 2, QTableWidgetItem(q.get("cpu", "")))
                self.customer_history_table.setItem(i, 3, QTableWidgetItem(str(quantity)))
                self.customer_history_table.setItem(i, 4, QTableWidgetItem(format_yuan(purchase_price)))
                self.customer_history_table.setItem(i, 5, QTableWidgetItem(format_yuan(quote_price)))
                self.customer_history_table.setItem(i, 6, QTableWidgetItem(format_yuan(profit)))
                self.customer_history_table.setItem(i, 7, QTableWidgetItem(q.get("remark", "")))
            self.customer_history_table.resizeColumnsToContents()

    def on_edit_customer_from_table(self):
        row = self.customer_table.currentRow()
        if row < 0:
            return
        cid = int(self.customer_table.item(row, 0).text())
        row_data = get_customer(cid)
        if not row_data:
            QMessageBox.warning(self, "提示", "客户不存在或已删除")
            self.refresh_customer_list()
            return
        current = {
            "name": row_data["name"] or "",
            "wechat": row_data["wechat"] or "",
            "qq": row_data["qq"] or "",
            "phone": row_data["phone"] or "",
            "note": row_data["note"] or "",
            "default_tax_rate": row_data["default_tax_rate"],
        }
        dlg = CustomerDialog(self, current)
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                return
            try:
                self.customer_service.update(cid, **data)
            except ServiceError as exc:
                QMessageBox.warning(self, "编辑失败", str(exc))
                return
            self.refresh_customer_list()

    def on_delete_customer(self):
        row = self.customer_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个客户")
            return
        cid = int(self.customer_table.item(row, 0).text())
        name = self.customer_table.item(row, 1).text()
        references = get_customer_reference_counts(cid)
        quote_count = references["quotes"]
        payment_count = references["payments"]
        if quote_count > 0 or payment_count > 0:
            reply = QMessageBox.question(
                self, "确认删除",
                f"客户「{name}」有 {quote_count} 条报价和 {payment_count} 条收款记录。\n\n"
                "删除后客户将从工作列表隐藏，历史报价和账务仍会保留。\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        else:
            reply = QMessageBox.question(
                self, "确认删除", f"确定删除客户「{name}」？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.customer_service.delete(cid)
            except ServiceError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return
            self.refresh_customer_list()
            self.main.record_tab.refresh_records()
