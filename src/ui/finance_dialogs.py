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
    QPushButton,
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
from src.utils.money import cents_to_yuan, format_yuan


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
    spin.setDecimals(0)
    spin.setRange(
        -999_999_999 if allow_negative else 0,
        999_999_999,
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
        db_path=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        if quote:
            total = quote.get("net_total_cents")
            if total is None:
                total = (quote.get("quote_price_cents") or 0) * (
                    quote.get("quote_quantity") or 1
                )
            received = quote.get("received_amount_cents") or 0
            layout.addRow(
                QLabel(
                    f"客户：{quote.get('customer_name','')}　"
                    f"总额：{format_yuan(total)}　待收：{format_yuan(total-received)}"
                )
            )
        elif preview_pending is not None:
            layout.addRow(QLabel(f"当前往来余额：¥ {preview_pending:.0f}"))
        self.amount_spin = _money_spin()
        layout.addRow("金额：", self.amount_spin)
        self.account_combo = QComboBox()
        for account in list_financial_accounts(db_path=db_path):
            self.account_combo.addItem(account["name"], account["id"])
        _restore_last_account(self.account_combo)
        layout.addRow("资金账户：", self.account_combo)
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
        if self.account_combo.currentData() is None:
            QMessageBox.warning(self, "提示", "请选择资金账户")
            return
        _remember_account(self.account_combo)
        self.accept()

    def get_data(self):
        return {
            "amount": self.amount_spin.value(),
            "account_id": self.account_combo.currentData(),
            "method": self.account_combo.currentText(),
            "pay_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "remark": self.remark_edit.text().strip(),
        }


class PaymentEditDialog(PaymentDialog):
    def __init__(self, parent=None, payment=None, *, db_path=None):
        super().__init__(
            parent,
            title="更正收付款",
            pay_type=(payment or {}).get("type", "receivable"),
            db_path=db_path,
        )
        payment = payment or {}
        self.amount_spin.setValue(
            float(cents_to_yuan(payment.get("amount_cents", 0)))
        )
        index = self.account_combo.findData(payment.get("account_id"))
        if index >= 0:
            self.account_combo.setCurrentIndex(index)
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
        self.account_combo = QComboBox()
        self.account_combo.addItem("—", None)
        for account in list_financial_accounts(db_path=db_path):
            self.account_combo.addItem(account["name"], account["id"])
        _restore_last_account(self.account_combo)
        self.remark_edit = QLineEdit()
        self.sn_edit = QLineEdit()
        layout.addRow("采购单价：", self.price_spin)
        layout.addRow("数量：", self.quantity_spin)
        layout.addRow("入库日期：", self.date_edit)
        layout.addRow("供应商：", self.supplier_combo)
        layout.addRow("结算方式：", self.settlement_combo)
        layout.addRow("付款账户：", self.account_combo)
        layout.addRow("备注：", self.remark_edit)
        layout.addRow("序列号：", self.sn_edit)
        self.settlement_combo.currentIndexChanged.connect(self._sync_account)
        self._sync_account()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _sync_account(self):
        paid = self.settlement_combo.currentData() == "paid"
        self.account_combo.setEnabled(paid)

    def _accept(self):
        if (
            self.settlement_combo.currentData() == "paid"
            and self.account_combo.currentData() is None
        ):
            QMessageBox.warning(self, "提示", "立即付款必须选择资金账户")
            return
        if (
            self.settlement_combo.currentData() == "credit"
            and not self.supplier_combo.currentText().strip()
        ):
            QMessageBox.warning(self, "提示", "赊购必须选择供应商")
            return
        if self.settlement_combo.currentData() == "paid":
            _remember_account(self.account_combo)
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
            "account_id": self.account_combo.currentData(),
        }


class FinanceSetupDialog(QDialog):
    def __init__(self, parent=None, *, db_path=None):
        super().__init__(parent)
        self.setWindowTitle("启用经营记账")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        state = get_finance_setup_state(db_path)
        layout.addWidget(
            QLabel(
                "启用后，入库、出库和收付款将自动记账。\n"
                f"期初应收：{format_yuan(state['opening_receivable_cents'])}；"
                f"期初应付：{format_yuan(state['opening_payable_cents'])}；"
                f"期初库存：{format_yuan(state['opening_inventory_cents'])}"
            )
        )
        form = QFormLayout()
        self.enabled_date = QDateEdit(QDate.currentDate())
        self.enabled_date.setCalendarPopup(True)
        form.addRow("财务启用日：", self.enabled_date)
        layout.addLayout(form)
        self.accounts_table = QTableWidget(len(DEFAULT_ACCOUNT_NAMES), 2)
        self.accounts_table.setHorizontalHeaderLabels(["账户名称", "期初余额"])
        for row, name in enumerate(DEFAULT_ACCOUNT_NAMES):
            self.accounts_table.setItem(row, 0, QTableWidgetItem(name))
            spin = _money_spin(allow_negative=True)
            spin.setValue(0)
            self.accounts_table.setCellWidget(row, 1, spin)
        layout.addWidget(self.accounts_table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self):
        accounts = []
        for row in range(self.accounts_table.rowCount()):
            item = self.accounts_table.item(row, 0)
            name = item.text().strip() if item else ""
            if name:
                accounts.append(
                    {
                        "name": name,
                        "opening_yuan": self.accounts_table.cellWidget(
                            row, 1
                        ).value(),
                    }
                )
        return {
            "enabled_at": self.enabled_date.date().toString("yyyy-MM-dd"),
            "accounts": accounts,
        }


class ManualEntryDialog(QDialog):
    def __init__(self, kind: str, parent=None, *, db_path=None):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle("记收入" if kind == "income" else "记费用")
        layout = QFormLayout(self)
        self.amount_spin = _money_spin()
        self.account_combo = QComboBox()
        for row in list_financial_accounts(db_path=db_path):
            self.account_combo.addItem(row["name"], row["id"])
        _restore_last_account(self.account_combo)
        self.category_combo = QComboBox()
        for row in list_finance_categories(kind, db_path=db_path):
            self.category_combo.addItem(row["name"], row["id"])
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.customer_combo = QComboBox()
        self.customer_combo.addItem("—", None)
        for row in list_customers(db_path=db_path):
            self.customer_combo.addItem(row["name"], row["id"])
        self.supplier_combo = QComboBox()
        self.supplier_combo.addItem("—", None)
        for row in list_suppliers(db_path=db_path):
            self.supplier_combo.addItem(row["name"], row["id"])
        self.quote_spin = QSpinBox()
        self.quote_spin.setRange(0, 999_999_999)
        self.quote_spin.setSpecialValueText("—")
        self.remark_edit = QLineEdit()
        layout.addRow("金额：", self.amount_spin)
        layout.addRow("资金账户：", self.account_combo)
        layout.addRow("分类：", self.category_combo)
        layout.addRow("日期：", self.date_edit)
        layout.addRow("关联客户：", self.customer_combo)
        layout.addRow("关联供应商：", self.supplier_combo)
        layout.addRow("关联报价 ID：", self.quote_spin)
        layout.addRow("备注：", self.remark_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        _remember_account(self.account_combo)
        return {
            "amount": self.amount_spin.value(),
            "account_id": self.account_combo.currentData(),
            "category_id": self.category_combo.currentData(),
            "entry_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "customer_id": self.customer_combo.currentData(),
            "supplier_id": self.supplier_combo.currentData(),
            "quote_id": self.quote_spin.value() or None,
            "remark": self.remark_edit.text().strip(),
        }


class TransferDialog(QDialog):
    def __init__(self, parent=None, *, db_path=None):
        super().__init__(parent)
        self.setWindowTitle("账户转账")
        layout = QFormLayout(self)
        accounts = list_financial_accounts(db_path=db_path)
        self.from_combo = QComboBox()
        self.to_combo = QComboBox()
        for row in accounts:
            self.from_combo.addItem(row["name"], row["id"])
            self.to_combo.addItem(row["name"], row["id"])
        _restore_last_account(self.from_combo)
        if self.to_combo.count() > 1:
            self.to_combo.setCurrentIndex(1)
        self.amount_spin = _money_spin()
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.remark_edit = QLineEdit()
        layout.addRow("转出账户：", self.from_combo)
        layout.addRow("转入账户：", self.to_combo)
        layout.addRow("金额：", self.amount_spin)
        layout.addRow("日期：", self.date_edit)
        layout.addRow("备注：", self.remark_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        _remember_account(self.from_combo)
        return {
            "from_account_id": self.from_combo.currentData(),
            "to_account_id": self.to_combo.currentData(),
            "amount": self.amount_spin.value(),
            "entry_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "remark": self.remark_edit.text().strip(),
        }


class AdjustmentDialog(QDialog):
    def __init__(self, parent=None, *, db_path=None, reconcile=False):
        super().__init__(parent)
        self.setWindowTitle("账户核对" if reconcile else "资金调整")
        self.reconcile = reconcile
        layout = QFormLayout(self)
        self.account_combo = QComboBox()
        for row in list_financial_accounts(db_path=db_path):
            self.account_combo.addItem(row["name"], row["id"])
        _restore_last_account(self.account_combo)
        self.amount_spin = _money_spin(allow_negative=True)
        self.amount_spin.setValue(0)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.reason_edit = QLineEdit()
        layout.addRow("资金账户：", self.account_combo)
        layout.addRow("实际余额：" if reconcile else "调整金额：", self.amount_spin)
        layout.addRow("日期：", self.date_edit)
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
            QMessageBox.warning(self, "提示", "必须填写原因")
            return
        self.accept()

    def get_data(self):
        _remember_account(self.account_combo)
        return {
            "account_id": self.account_combo.currentData(),
            "amount": self.amount_spin.value(),
            "entry_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "reason": self.reason_edit.text().strip(),
        }


class ReturnDialog(QDialog):
    def __init__(self, title: str, parent=None, *, max_quantity=1, db_path=None,
                 allow_restock=False, allocations=None, original_quantity=None,
                 preview_callback=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.preview_callback = preview_callback
        layout = QFormLayout(self)
        total_allocated = sum(item.get("quantity", 0) for item in allocations or [])
        already_returned = sum(item.get("returned_quantity", 0) for item in allocations or [])
        net_returnable = max(total_allocated - already_returned, 0)
        original_quantity = original_quantity if original_quantity is not None else total_allocated
        self.return_summary = QLabel(
            f"原出库 {original_quantity} 台｜累计已退 {already_returned} 台｜净可退 {net_returnable} 台"
        )
        layout.addRow(self.return_summary)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, max(1, min(max_quantity, net_returnable)))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.restock_check = QCheckBox("退回库存")
        self.restock_check.setChecked(True)
        self.restock_check.setVisible(allow_restock)
        self.refund_spin = _money_spin()
        self.refund_spin.setMinimum(0)
        self.refund_spin.setValue(0)
        self.account_combo = QComboBox()
        self.account_combo.addItem("保留往来余额，不立即退款", None)
        for row in list_financial_accounts(db_path=db_path):
            self.account_combo.addItem(row["name"], row["id"])
        _restore_last_account(self.account_combo)
        self.reason_edit = QLineEdit()
        layout.addRow("退货数量：", self.quantity_spin)
        layout.addRow("日期：", self.date_edit)
        layout.addRow(self.restock_check)
        layout.addRow("实际退款：", self.refund_spin)
        layout.addRow("退款账户：", self.account_combo)
        layout.addRow("原因：", self.reason_edit)
        self.allocation_rows = []
        if allocations:
            self.auto_sn_edit = QLineEdit()
            self.auto_sn_edit.setPlaceholderText("输入退货SN可自动定位原出库批次；未知SN再手工选择")
            self.auto_sn_edit.editingFinished.connect(self._match_return_sns)
            layout.addRow("退货SN自动匹配：", self.auto_sn_edit)
            self.allocation_table = QTableWidget(len(allocations), 6)
            self.allocation_table.setHorizontalHeaderLabels(
                ["原批次", "原出库", "已退货", "剩余可退", "本次数量", "退货SN"]
            )
            self.allocation_table.horizontalHeader().setStretchLastSection(True)
            for row, allocation in enumerate(allocations):
                available = max(
                    allocation["quantity"] - allocation.get("returned_quantity", 0), 0
                )
                for col, value in enumerate((
                    f"#{allocation['batch_id']}", allocation["quantity"],
                    allocation.get("returned_quantity", 0), available,
                )):
                    self.allocation_table.setItem(row, col, QTableWidgetItem(str(value)))
                spin = QSpinBox()
                spin.setRange(0, available)
                sn_edit = QLineEdit()
                sn_edit.setPlaceholderText("回库时：唯一来源可留空，否则必须填写；逗号/空格分隔")
                self.allocation_table.setCellWidget(row, 4, spin)
                self.allocation_table.setCellWidget(row, 5, sn_edit)
                self.allocation_rows.append((allocation, spin, sn_edit))
            layout.addRow("原出库分配：", self.allocation_table)
        self.preview_label = QLabel("预览不会写入数据库；提交时仍会重新校验。")
        self.preview_label.setWordWrap(True)
        preview_button = QPushButton("预览退货影响")
        preview_button.setAutoDefault(False)
        preview_button.clicked.connect(self._preview)
        layout.addRow(preview_button)
        layout.addRow("提交预览：", self.preview_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self):
        if self.refund_spin.value() > 0 and self.account_combo.currentData() is None:
            QMessageBox.warning(self, "提示", "实际退款必须选择资金账户")
            return
        if not self.reason_edit.text().strip():
            QMessageBox.warning(self, "提示", "必须填写退货原因")
            return
        if self.allocation_rows:
            selected = self._return_allocations()
            if sum(item["quantity"] for item in selected) != self.quantity_spin.value():
                QMessageBox.warning(self, "提示", "原出库批次分配合计必须等于退货数量")
                return
            for item in selected:
                sns = [value for value in item["sn_list"].replace(" ", ",").split(",") if value]
                if sns and len(sns) != item["quantity"]:
                    QMessageBox.warning(self, "提示", "填写SN后数量必须与对应批次退货数量一致")
                    return
        if not self._preview():
            return
        _remember_account(self.account_combo)
        self.accept()

    def _match_return_sns(self):
        if not self.allocation_rows:
            return
        raw = self.auto_sn_edit.text().strip().replace(" ", ",").replace("\n", ",")
        sns = [value.strip() for value in raw.split(",") if value.strip()]
        if not sns:
            return
        # A new automatic match replaces every prior automatic choice.  Keeping
        # an old row here could otherwise create a stale cross-batch return.
        for _, spin, sn_edit in self.allocation_rows:
            spin.setValue(0)
            sn_edit.clear()
        matched: dict[int, list[str]] = {}
        unknown = []
        for sn in sns:
            target = next(
                (
                    allocation for allocation, _, _ in self.allocation_rows
                    if sn in [value.strip() for value in
                              (allocation.get("sn_list", "") or "").split(",")]
                    and sn not in [value.strip() for value in
                                  (allocation.get("returned_sn_list", "") or "").split(",")]
                ),
                None,
            )
            if target is None:
                unknown.append(sn)
            else:
                matched.setdefault(target["id"], []).append(sn)
        for allocation, spin, sn_edit in self.allocation_rows:
            values = matched.get(allocation["id"], [])
            if values:
                spin.setValue(len(values))
                sn_edit.setText(",".join(values))
        if unknown:
            QMessageBox.information(
                self, "SN未完全匹配",
                "以下SN没有完整历史来源，请手工选择原批次：\n" + "、".join(unknown[:10]),
            )

    def _preview(self) -> bool:
        if self.preview_callback is None:
            return True
        try:
            preview = self.preview_callback(self.get_data())
        except Exception as exc:
            self.preview_label.setText(f"无法预览：{exc}")
            return False
        batch_text = "；".join(
            f"批次#{item['batch_id']} 回库 {item['return_quantity']} 台"
            for item in preview["selected"]
        ) if self.restock_check.isChecked() else "不回库"
        self.preview_label.setText(
            "预览（未写库）："
            f"{batch_text}；成本冲回 {format_yuan(preview['cost_cents'])}；"
            f"净应收 {format_yuan(preview['net_receivable_before_cents'])} → "
            f"{format_yuan(preview['net_receivable_after_cents'])}；"
            f"现金退款 {format_yuan(preview['cash_refund_cents'])}；"
            f"保留往来余额 {format_yuan(preview['retained_balance_cents'])}"
        )
        return True

    def get_data(self):
        return {
            "quantity": self.quantity_spin.value(),
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "restock": self.restock_check.isChecked(),
            "refund": self.refund_spin.value(),
            "account_id": self.account_combo.currentData(),
            "reason": self.reason_edit.text().strip(),
            "restock_allocations": self._return_allocations(),
        }

    def _return_allocations(self):
        result = []
        for allocation, spin, sn_edit in self.allocation_rows:
            quantity = spin.value()
            if quantity:
                raw = sn_edit.text().strip().replace(" ", ",")
                sns = [value.strip() for value in raw.split(",") if value.strip()]
                result.append({
                    "shipment_allocation_id": allocation["id"],
                    "quantity": quantity,
                    "sn_list": ",".join(sns),
                })
        return result
