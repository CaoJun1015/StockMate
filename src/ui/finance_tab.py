"""Finance tab backed by read queries and the immutable PaymentService."""

from __future__ import annotations

from PyQt6.QtCore import QDate, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.models.database import get_all_customers, get_all_suppliers, get_payment_by_id
from src.models.queries import get_payables, get_payment_flow, get_receivables
from src.models.repositories import yuan_to_cents
from src.services.exceptions import ServiceError
from src.services.payment_service import PaymentService
from src.ui.dialogs import PaymentDialog, PaymentEditDialog


class FinanceTab(QWidget):
    data_changed = pyqtSignal()

    def __init__(self, parent=None, *, db_path=None):
        super().__init__(parent)
        self.db_path = db_path
        self.payment_service = PaymentService(db_path)
        self._build_ui()

    def _table(self, headers):
        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        top = QSplitter()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("应收款项（客户欠款）"))
        self.receivable_table = self._table(["客户", "欠款金额", "订单数", "联系方式", "操作"])
        self.receivable_table.setMinimumHeight(200)
        left_layout.addWidget(self.receivable_table)
        self.refresh_receivable_btn = QPushButton("刷新")
        self.refresh_receivable_btn.setObjectName("ghostBtn")
        self.refresh_receivable_btn.clicked.connect(self.refresh)
        left_layout.addWidget(self.refresh_receivable_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("应付款项（欠上游款）"))
        self.payable_table = self._table(["上游", "欠款金额", "批次", "联系方式", "操作"])
        self.payable_table.setMinimumHeight(200)
        right_layout.addWidget(self.payable_table)
        self.refresh_payable_btn = QPushButton("刷新")
        self.refresh_payable_btn.setObjectName("ghostBtn")
        self.refresh_payable_btn.clicked.connect(self.refresh)
        right_layout.addWidget(self.refresh_payable_btn)

        top.addWidget(left)
        top.addWidget(right)
        top.setSizes([400, 400])
        layout.addWidget(top, stretch=3)

        flow_group = QGroupBox("收付款流水记录")
        flow_group.setMaximumHeight(280)
        flow_layout = QVBoxLayout(flow_group)
        filters = QHBoxLayout()

        filters.addWidget(QLabel("类型:"))
        self.payment_type_filter = QComboBox()
        self.payment_type_filter.addItems(["全部", "收款", "付款"])
        self.payment_type_filter.currentTextChanged.connect(self.refresh_payment_flow)
        filters.addWidget(self.payment_type_filter)

        filters.addWidget(QLabel("对象:"))
        self.payment_object_filter = QComboBox()
        self.payment_object_filter.setEditable(True)
        self.payment_object_filter.currentTextChanged.connect(self.refresh_payment_flow)
        filters.addWidget(self.payment_object_filter)

        filters.addWidget(QLabel("日期从:"))
        self.payment_date_from = QDateEdit()
        self.payment_date_from.setCalendarPopup(True)
        self.payment_date_from.setDate(QDate.currentDate().addMonths(-3))
        self.payment_date_from.dateChanged.connect(self.refresh_payment_flow)
        filters.addWidget(self.payment_date_from)

        filters.addWidget(QLabel("至:"))
        self.payment_date_to = QDateEdit()
        self.payment_date_to.setCalendarPopup(True)
        self.payment_date_to.setDate(QDate.currentDate())
        self.payment_date_to.dateChanged.connect(self.refresh_payment_flow)
        filters.addWidget(self.payment_date_to)

        self.refresh_flow_btn = QPushButton("刷新")
        self.refresh_flow_btn.setObjectName("ghostBtn")
        self.refresh_flow_btn.clicked.connect(self.refresh_payment_flow)
        filters.addWidget(self.refresh_flow_btn)
        filters.addStretch()
        flow_layout.addLayout(filters)

        self.payment_flow_table = self._table(
            ["ID", "日期", "类型/状态", "金额", "方式", "关联对象", "备注", "操作"]
        )
        self.payment_flow_table.setColumnHidden(0, True)
        flow_layout.addWidget(self.payment_flow_table)
        self.payment_flow_stats = QLabel()
        self.payment_flow_stats.setObjectName("summaryLabel")
        flow_layout.addWidget(self.payment_flow_stats)
        layout.addWidget(flow_group, stretch=1)

    def refresh(self):
        self._refresh_receivables()
        self._refresh_payables()
        self._load_payment_object_filter()
        self.refresh_payment_flow()

    def _refresh_receivables(self):
        rows = get_receivables(self.db_path)
        self.receivable_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self.receivable_table.setItem(index, 0, QTableWidgetItem(row["name"]))
            amount = QTableWidgetItem(f"¥{row['debt']:.0f}")
            amount.setForeground(QColor("#D32F2F"))
            self.receivable_table.setItem(index, 1, amount)
            self.receivable_table.setItem(index, 2, QTableWidgetItem(str(row["order_count"])))
            contact = " | ".join(filter(None, [row["wechat"] or "", row["phone"] or ""]))
            self.receivable_table.setItem(index, 3, QTableWidgetItem(contact))
            button = QPushButton("收款")
            button.setObjectName("tableActionPrimary")
            button.clicked.connect(lambda _, cid=row["id"], debt=row["debt"]: self._receive(cid, debt))
            self.receivable_table.setCellWidget(index, 4, button)
        self.receivable_table.resizeColumnsToContents()

    def _refresh_payables(self):
        rows = get_payables(self.db_path)
        self.payable_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self.payable_table.setItem(index, 0, QTableWidgetItem(row["name"]))
            amount = QTableWidgetItem(f"¥{row['debt']:.0f}")
            amount.setForeground(QColor("#F57C00"))
            self.payable_table.setItem(index, 1, amount)
            self.payable_table.setItem(index, 2, QTableWidgetItem(str(row["batch_count"])))
            contact = " | ".join(filter(None, [row["wechat"] or "", row["phone"] or ""]))
            self.payable_table.setItem(index, 3, QTableWidgetItem(contact))
            button = QPushButton("付款")
            button.setObjectName("tableActionOrange")
            button.clicked.connect(lambda _, sid=row["id"]: self._pay(sid))
            self.payable_table.setCellWidget(index, 4, button)
        self.payable_table.resizeColumnsToContents()

    def _load_payment_object_filter(self):
        selected = self.payment_object_filter.currentData()
        self.payment_object_filter.blockSignals(True)
        self.payment_object_filter.clear()
        self.payment_object_filter.addItem("全部对象", None)
        for customer in get_all_customers():
            self.payment_object_filter.addItem(
                f"客户: {customer['name']}", ("customer", customer["id"])
            )
        for supplier in get_all_suppliers():
            self.payment_object_filter.addItem(
                f"上游: {supplier['name']}", ("supplier", supplier["id"])
            )
        index = self.payment_object_filter.findData(selected)
        self.payment_object_filter.setCurrentIndex(max(index, 0))
        self.payment_object_filter.blockSignals(False)

    def refresh_payment_flow(self):
        type_text = self.payment_type_filter.currentText()
        pay_type = {"收款": "receivable", "付款": "payable"}.get(type_text)
        object_data = self.payment_object_filter.currentData()
        customer_id = object_data[1] if object_data and object_data[0] == "customer" else None
        supplier_id = object_data[1] if object_data and object_data[0] == "supplier" else None
        payments = get_payment_flow(
            pay_type,
            customer_id,
            supplier_id,
            self.payment_date_from.date().toString("yyyy-MM-dd"),
            self.payment_date_to.date().toString("yyyy-MM-dd"),
            self.db_path,
        )
        self.payment_flow_table.setRowCount(len(payments))
        total_receive = total_pay = 0
        for index, payment in enumerate(payments):
            self.payment_flow_table.setItem(index, 0, QTableWidgetItem(str(payment["id"])))
            self.payment_flow_table.setItem(index, 1, QTableWidgetItem(payment["pay_date"] or ""))
            label = "收款" if payment["type"] == "receivable" else "付款"
            label += f" · {payment['ledger_status']}"
            self.payment_flow_table.setItem(index, 2, QTableWidgetItem(label))
            signed = -1 if payment["entry_kind"] == "reversal" else 1
            amount = signed * (payment["amount"] or 0)
            self.payment_flow_table.setItem(index, 3, QTableWidgetItem(f"¥{amount:.0f}"))
            self.payment_flow_table.setItem(index, 4, QTableWidgetItem(payment["method"] or ""))
            name = payment["customer_name"] or payment["supplier_name"] or ""
            self.payment_flow_table.setItem(index, 5, QTableWidgetItem(name))
            self.payment_flow_table.setItem(index, 6, QTableWidgetItem(payment["remark"] or ""))

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            if payment["ledger_status"] in ("正常", "更正"):
                edit = QPushButton("更正")
                edit.setObjectName("tableActionPrimary")
                edit.clicked.connect(lambda _, pid=payment["id"]: self._correct(pid))
                action_layout.addWidget(edit)
                void = QPushButton("作废")
                void.setObjectName("tableActionDanger")
                void.clicked.connect(lambda _, pid=payment["id"]: self._void(pid))
                action_layout.addWidget(void)
            self.payment_flow_table.setCellWidget(index, 7, actions)

            if payment["type"] == "receivable":
                total_receive += amount
            else:
                total_pay += amount
        self.payment_flow_table.resizeColumnsToContents()
        self.payment_flow_stats.setText(
            f"共 {len(payments)} 条记录 | 收款净额: ¥{total_receive:.0f} | 付款净额: ¥{total_pay:.0f}"
        )

    def _receive(self, customer_id, pending):
        dialog = PaymentDialog(self, title="收款", pay_type="receivable", preview_pending=pending)
        if not dialog.exec():
            return
        data = dialog.get_data()
        try:
            self.payment_service.receive_customer_payment(
                customer_id,
                yuan_to_cents(data["amount"]),
                data["pay_date"],
                data["method"],
                data["remark"],
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "收款失败", str(exc))
            return
        self._changed("收款已记录")

    def _pay(self, supplier_id):
        dialog = PaymentDialog(self, title="付款", pay_type="payable")
        if not dialog.exec():
            return
        data = dialog.get_data()
        try:
            self.payment_service.record_supplier_payment(
                supplier_id,
                yuan_to_cents(data["amount"]),
                data["pay_date"],
                data["method"],
                data["remark"],
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "付款失败", str(exc))
            return
        self._changed("付款已记录")

    def _void(self, payment_id):
        reason, ok = QInputDialog.getText(self, "作废流水", "请输入作废原因:")
        if not ok:
            return
        try:
            self.payment_service.void_payment(payment_id, reason)
        except ServiceError as exc:
            QMessageBox.warning(self, "作废失败", str(exc))
            return
        self._changed("流水已作废")

    def _correct(self, payment_id):
        payment = get_payment_by_id(payment_id)
        if not payment:
            QMessageBox.warning(self, "更正失败", "流水不存在")
            return
        dialog = PaymentEditDialog(self, payment)
        if not dialog.exec():
            return
        data = dialog.get_data()
        reason, ok = QInputDialog.getText(self, "更正流水", "请输入更正原因:")
        if not ok:
            return
        try:
            self.payment_service.correct_payment(
                payment_id,
                amount_cents=yuan_to_cents(data["amount"]),
                pay_date=data["pay_date"],
                method=data["method"],
                remark=data["remark"],
                reason=reason,
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "更正失败", str(exc))
            return
        self._changed("流水已更正")

    def _changed(self, message):
        QMessageBox.information(self, "成功", message)
        self.refresh()
        self.data_changed.emit()

