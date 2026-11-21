"""v1.16 finance-aware dialogs with cent-accurate amount input."""

from __future__ import annotations

from PyQt6.QtCore import QDate, QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.models.finance_queries import (
    get_finance_setup_state,
    list_finance_categories,
    list_financial_accounts,
)
from src.models.queries import list_customers, list_suppliers
from src.services.finance_service import DEFAULT_ACCOUNT_NAMES
from src.services.party_service import SupplierService
from src.utils.money import cents_to_yuan, format_whole_yuan, format_yuan, yuan_to_cents
from src.services.simple_finance_service import flow_account_id


def _restore_last_account(combo: QComboBox) -> None:
    account_id = QSettings(
        "DiaohuoAssistant",
        "DiaohuoAssistant",
    ).value("finance/last_account_id", 0, type=int)
    index = combo.findData(account_id)
    if index >= 0:
        combo.setCurrentIndex(index)


def _remember_account(combo: QComboBox) -> None:
    account_id = combo.currentData()
    if account_id is not None:
        QSettings(
            "DiaohuoAssistant",
            "DiaohuoAssistant",
        ).setValue("finance/last_account_id", account_id)


def _money_spin(*, allow_negative: bool = False) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(2)
    spin.setRange(
        -999_999_999.99 if allow_negative else 0.01,
        999_999_999.99,
    )
    spin.setPrefix("¥ ")
    spin.setSingleStep(1.0)
    return spin


class PaymentDialog(QDialog):
    def __init__(
        self,
        parent=None,
        title="收付款",
        pay_type="receivable",
        quote=None,
        preview_pending=None,
        *,
        cent_precision=False,
        db_path=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        if quote:
            total = (quote.get("quote_price_cents") or 0) * (
                quote.get("quote_quantity") or 1
            )
            received = quote.get("received_amount_cents") or 0
            layout.addRow(
                QLabel(
                    f"客户：{quote.get('customer_name','')}　"
                    f"总额：{format_whole_yuan(total)}　待收：{format_whole_yuan(total-received)}"
                )
            )
        elif preview_pending is not None:
            cents = yuan_to_cents(preview_pending)
            label = QLabel(f"当前往来余额：{format_whole_yuan(cents)}")
            label.setToolTip(format_yuan(cents))
            layout.addRow(label)
        if cent_precision:
            self.amount_spin = _money_spin()
        else:
            self.amount_spin = QSpinBox()
            self.amount_spin.setRange(0, 999_999_999)
            self.amount_spin.setPrefix("¥ ")
            self.amount_spin.selectAll()
        layout.addRow("金额：", self.amount_spin)
        self.account_id = flow_account_id(db_path)
        self.method = "业务收支"
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        layout.addRow("日期：", self.date_edit)
        self.remark_edit = QLineEdit()
        layout.addRow("备注：", self.remark_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self):
        if self.amount_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "金额必须大于 0")
            return
        self.accept()

    def get_data(self):
        return {
            "amount": self.amount_spin.value(),
            "account_id": self.account_id,
            "method": self.method,
            "pay_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "remark": self.remark_edit.text().strip(),
        }


class PaymentEditDialog(PaymentDialog):
    def __init__(self, parent=None, payment=None, *, db_path=None):
        super().__init__(
            parent,
            title="更正收付款",
            pay_type=(payment or {}).get("type", "receivable"),
            cent_precision=bool((payment or {}).get("amount_cents", 0) % 100),
            db_path=db_path,
        )
        payment = payment or {}
        self.amount_spin.setValue(
            cents_to_yuan(payment["amount_cents"]) if payment.get("amount_cents", 0) % 100
            else payment.get("amount_cents", 0) // 100
        )
        self.account_id = payment.get("account_id") or self.account_id
        self.method = payment.get("method") or self.method
        value = QDate.fromString(payment.get("pay_date", ""), "yyyy-MM-dd")
        if value.isValid():
            self.date_edit.setDate(value)
        self.remark_edit.setText(payment.get("remark", "") or "")


class BatchDialog(QDialog):
    def __init__(self, parent=None, *, db_path=None):
        super().__init__(parent)
        self.db_path = db_path
        self.setWindowTitle("新增库存批次")
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        self.price_spin = _money_spin()
        self.price_spin.setMinimum(0)
        self.price_spin.setValue(0)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 9999)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.supplier_combo = QComboBox()
        self.supplier_combo.setEditable(True)
        for supplier in list_suppliers(db_path=db_path):
            self.supplier_combo.addItem(supplier["name"], supplier["id"])
        self.settlement_combo = QComboBox()
        self.settlement_combo.addItem("赊购", "credit")
        self.settlement_combo.addItem("立即付款", "paid")
        self.account_id = flow_account_id(db_path)
        self.remark_edit = QLineEdit()
        self.sn_edit = QLineEdit()
        layout.addRow("采购单价：", self.price_spin)
        layout.addRow("数量：", self.quantity_spin)
        layout.addRow("入库日期：", self.date_edit)
        layout.addRow("供应商：", self.supplier_combo)
        layout.addRow("结算方式：", self.settlement_combo)
        layout.addRow("备注：", self.remark_edit)
        layout.addRow("序列号：", self.sn_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self):
        if (
            self.settlement_combo.currentData() == "credit"
            and not self.supplier_combo.currentText().strip()
        ):
            QMessageBox.warning(self, "提示", "赊购必须选择供应商")
            return
        self.accept()

    def get_data(self):
        supplier_name = self.supplier_combo.currentText().strip()
        supplier_id = self.supplier_combo.currentData()
        if not supplier_id and supplier_name:
            supplier_id = SupplierService(self.db_path).create(name=supplier_name)
        return {
            "price": self.price_spin.value(),
            "quantity": self.quantity_spin.value(),
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "remark": self.remark_edit.text().strip(),
            "supplier_id": supplier_id,
            "sn_list": self.sn_edit.text().strip(),
            "settlement_mode": self.settlement_combo.currentData(),
            "account_id": self.account_id,
        }


class ReturnDialog(QDialog):
    def __init__(self, title: str, parent=None, *, max_quantity=1, db_path=None, allow_restock=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QFormLayout(self)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, max(1, max_quantity))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.restock_check = QCheckBox("退回库存")
        self.restock_check.setChecked(True)
        self.restock_check.setVisible(allow_restock)
        self.refund_spin = _money_spin()
        self.refund_spin.setMinimum(0)
        self.refund_spin.setValue(0)
        self.account_id = flow_account_id(db_path)
        self.reason_edit = QLineEdit()
        layout.addRow("退货数量：", self.quantity_spin)
        layout.addRow("日期：", self.date_edit)
        layout.addRow(self.restock_check)
        layout.addRow("实际退款：", self.refund_spin)
        layout.addRow(QLabel("退款填 0 表示保留往来余额，不立即退款。"))
        layout.addRow("原因：", self.reason_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self):
        if not self.reason_edit.text().strip():
            QMessageBox.warning(self, "提示", "必须填写退货原因")
            return
        self.accept()

    def get_data(self):
        return {
            "quantity": self.quantity_spin.value(),
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "restock": self.restock_check.isChecked(),
            "refund": self.refund_spin.value(),
            "account_id": self.account_id,
            "reason": self.reason_edit.text().strip(),
        }
