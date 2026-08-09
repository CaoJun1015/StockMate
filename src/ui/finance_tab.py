"""Five-page operating-finance workspace for v1.16."""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.models.finance_queries import (
    get_finance_dashboard,
    get_finance_setup_state,
    get_operating_entry_detail,
    get_profit_report,
    list_counterparty_balances,
    list_finance_categories,
    list_financial_accounts,
    list_operating_entries,
)
from src.models.queries import get_payment_detail
from src.services.exceptions import ServiceError
from src.services.finance_service import FinanceService
from src.services.payment_service import PaymentService
from src.ui.finance_dialogs import (
    AdjustmentDialog,
    FinanceSetupDialog,
    ManualEntryDialog,
    PaymentDialog,
    PaymentEditDialog,
    TransferDialog,
)
from src.ui.display_labels import format_source_label
from src.utils.excel_export import export_finance_to_excel
from src.utils.money import format_yuan, yuan_to_cents


EVENT_LABELS = {
    "opening_funds": "期初资金",
    "opening_receivable": "期初应收",
    "opening_payable": "期初应付",
    "opening_inventory": "期初库存",
    "inventory_receipt": "采购入库",
    "sales_shipment": "销售出库",
    "customer_receipt": "客户收款",
    "supplier_payment": "供应商付款",
    "manual_income": "其他收入",
    "manual_expense": "日常费用",
    "account_transfer": "账户转账",
    "funds_adjustment": "资金调整",
    "sales_return": "销售退货",
    "purchase_return": "采购退货",
}


class FinanceTab(QWidget):
    data_changed = pyqtSignal()

    def __init__(self, parent=None, *, db_path=None):
        super().__init__(parent)
        self.db_path = db_path
        self.finance_service = FinanceService(db_path)
        self.payment_service = PaymentService(db_path)
        self._build_ui()

    @staticmethod
    def _table(headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _build_ui(self):
        root = QVBoxLayout(self)
        self.setup_banner = QWidget()
        setup_layout = QHBoxLayout(self.setup_banner)
        self.setup_label = QLabel("经营记账尚未启用，业务写操作将被保护。")
        self.setup_button = QPushButton("启用经营记账")
        self.setup_button.setObjectName("primaryBtn")
        self.setup_button.clicked.connect(self._setup_finance)
        setup_layout.addWidget(self.setup_label)
        setup_layout.addStretch()
        setup_layout.addWidget(self.setup_button)
        root.addWidget(self.setup_banner)

        self.section_tabs = QTabWidget()
        root.addWidget(self.section_tabs)
        self._build_dashboard_page()
        self._build_daily_page()
        self._build_counterparty_page()
        self._build_profit_page()
        self._build_audit_page()
        # MainWindow aliases remain for one local compatibility cycle.
        self.payment_flow_table = self.daily_table
        self.refresh_receivable_btn = QPushButton("刷新", self)
        self.refresh_payable_btn = QPushButton("刷新", self)
        self.refresh_flow_btn = QPushButton("刷新", self)
        for button in (
            self.refresh_receivable_btn,
            self.refresh_payable_btn,
            self.refresh_flow_btn,
        ):
            button.setObjectName("ghostBtn")
            button.clicked.connect(self.refresh)
            button.hide()

    def _build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.dashboard_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.dashboard_from.setCalendarPopup(True)
        self.dashboard_to = QDateEdit(QDate.currentDate())
        self.dashboard_to.setCalendarPopup(True)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        export = QPushButton("导出财务 Excel")
        export.clicked.connect(self._export_excel)
        filters.addWidget(QLabel("期间："))
        filters.addWidget(self.dashboard_from)
        filters.addWidget(QLabel("至"))
        filters.addWidget(self.dashboard_to)
        filters.addWidget(refresh)
        filters.addWidget(export)
        filters.addStretch()
        layout.addLayout(filters)
        grid = QGridLayout()
        cards = (
            ("funds", "资金总额"),
            ("receivable", "客户应收"),
            ("customer_advance", "客户预收"),
            ("payable", "供应商应付"),
            ("supplier_advance", "供应商预付"),
            ("sales", "期间销售"),
            ("gross_profit", "毛利润"),
            ("expense", "期间费用"),
            ("net_profit", "净利润"),
            ("cash_change", "资金净变化"),
        )
        self.dashboard_cards = {}
        for index, (key, title) in enumerate(cards):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            title_label = QLabel(title)
            value = QLabel("¥0.00")
            value.setObjectName("summaryLabel")
            value.setStyleSheet("font-size: 20px; font-weight: 700;")
            box_layout.addWidget(title_label)
            box_layout.addWidget(value)
            grid.addWidget(box, index // 5, index % 5)
            self.dashboard_cards[key] = value
        layout.addLayout(grid)
        layout.addStretch()
        self.section_tabs.addTab(page, "经营总览")

    def _build_daily_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        for text, handler, style in (
            ("记收入", lambda: self._manual_entry("income"), "primaryBtn"),
            ("记费用", lambda: self._manual_entry("expense"), "warningBtn"),
            ("账户转账", self._transfer, "ghostBtn"),
            ("资金调整", lambda: self._adjust(False), "ghostBtn"),
            ("账户核对", lambda: self._adjust(True), "ghostBtn"),
            ("新增账户", self._add_account, "ghostBtn"),
            ("停用/启用账户", self._toggle_account, "ghostBtn"),
            ("新增分类", self._add_category, "ghostBtn"),
            ("停用/启用分类", self._toggle_category, "ghostBtn"),
        ):
            button = QPushButton(text)
            button.setObjectName(style)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.accounts_table = self._table(["账户", "余额", "状态"])
        self.accounts_table.setMaximumHeight(180)
        layout.addWidget(self.accounts_table)
        self.daily_table = self._table(
            ["ID", "日期", "类型", "对象", "资金变化", "状态", "备注"]
        )
        self.daily_table.setColumnHidden(0, True)
        self.daily_table.doubleClicked.connect(self._show_entry_detail)
        layout.addWidget(self.daily_table)
        self.section_tabs.addTab(page, "日常收支")

    def _build_counterparty_page(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.addWidget(QLabel("客户应收 / 预收"), 0, 0)
        layout.addWidget(QLabel("供应商应付 / 预付"), 0, 1)
        self.receivable_table = self._table(
            ["客户", "往来余额", "状态", "未结订单", "账龄", "联系方式", "操作"]
        )
        self.payable_table = self._table(
            ["供应商", "往来余额", "状态", "未结批次", "账龄", "联系方式", "操作"]
        )
        layout.addWidget(self.receivable_table, 1, 0)
        layout.addWidget(self.payable_table, 1, 1)
        self.section_tabs.addTab(page, "应收应付")

    def _build_profit_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.profit_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.profit_from.setCalendarPopup(True)
        self.profit_to = QDateEdit(QDate.currentDate())
        self.profit_to.setCalendarPopup(True)
        self.profit_group = QComboBox()
        self.profit_group.addItem("报价单", "quote")
        self.profit_group.addItem("客户", "customer")
        self.profit_group.addItem("型号", "product")
        self.profit_group.addItem("月份", "month")
        query = QPushButton("查询")
        query.clicked.connect(self._refresh_profit)
        filters.addWidget(QLabel("日期："))
        filters.addWidget(self.profit_from)
        filters.addWidget(QLabel("至"))
        filters.addWidget(self.profit_to)
        filters.addWidget(QLabel("分组："))
        filters.addWidget(self.profit_group)
        filters.addWidget(query)
        filters.addStretch()
        layout.addLayout(filters)
        self.profit_table = self._table(
            ["对象", "销售额", "商品成本", "毛利润", "费用", "其他收入", "净利润"]
        )
        layout.addWidget(self.profit_table)
        self.profit_summary = QLabel()
        self.profit_summary.setObjectName("summaryLabel")
        layout.addWidget(self.profit_summary)
        self.section_tabs.addTab(page, "利润分析")

    def _build_audit_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.audit_from = QDateEdit(QDate.currentDate().addMonths(-3))
        self.audit_from.setCalendarPopup(True)
        self.audit_to = QDateEdit(QDate.currentDate())
        self.audit_to.setCalendarPopup(True)
        self.audit_event = QComboBox()
        self.audit_event.addItem("全部业务", None)
        for key, label in EVENT_LABELS.items():
            self.audit_event.addItem(label, key)
        self.audit_status = QComboBox()
        self.audit_status.addItem("全部状态", None)
        self.audit_status.addItem("正常", "normal")
        self.audit_status.addItem("已冲销", "reversed")
        self.audit_status.addItem("冲销记录", "reversal")
        self.audit_status.addItem("更正记录", "corrected")
        self.audit_entity_id = QLineEdit()
        self.audit_entity_id.setPlaceholderText("业务实体 ID")
        self.audit_keyword = QLineEdit()
        self.audit_keyword.setPlaceholderText("客户、供应商、备注或原因")
        query = QPushButton("查询")
        query.clicked.connect(self._refresh_audit)
        void = QPushButton("作废手工账")
        void.clicked.connect(self._void_selected_entry)
        correct = QPushButton("更正收付款")
        correct.clicked.connect(self._correct_selected_payment)
        filters.addWidget(self.audit_from)
        filters.addWidget(QLabel("至"))
        filters.addWidget(self.audit_to)
        filters.addWidget(self.audit_event)
        filters.addWidget(self.audit_status)
        filters.addWidget(self.audit_entity_id)
        filters.addWidget(self.audit_keyword)
        filters.addWidget(query)
        filters.addWidget(void)
        filters.addWidget(correct)
        layout.addLayout(filters)
        self.audit_table = self._table(
            ["ID", "日期", "业务环节", "来源", "关联对象", "总额", "状态", "原因/备注"]
        )
        self.audit_table.setColumnHidden(0, True)
        self.audit_table.doubleClicked.connect(self._show_entry_detail)
        layout.addWidget(self.audit_table)
        layout.addWidget(QLabel("双击记录查看平衡分录、业务来源及关联对象。"))
        self.section_tabs.addTab(page, "流水审计")

    def refresh(self):
        state = get_finance_setup_state(self.db_path)
        self.setup_banner.setVisible(not state["enabled"])
        self.section_tabs.setEnabled(state["enabled"])
        if not state["enabled"]:
            self.setup_label.setText(
                "经营记账尚未启用："
                f"待建立期初应收 {format_yuan(state['opening_receivable_cents'])}，"
                f"期初应付 {format_yuan(state['opening_payable_cents'])}。"
            )
            return
        self._refresh_dashboard()
        self._refresh_accounts()
        self._refresh_daily()
        self._refresh_counterparties()
        self._refresh_profit()
        self._refresh_audit()

    def _refresh_dashboard(self):
        data = get_finance_dashboard(
            self.dashboard_from.date().toString("yyyy-MM-dd"),
            self.dashboard_to.date().toString("yyyy-MM-dd"),
            self.db_path,
        )
        for key, label in self.dashboard_cards.items():
            label.setText(format_yuan(data[f"{key}_cents"]))
        color = "#388E3C" if data["net_profit_cents"] >= 0 else "#D32F2F"
        self.dashboard_cards["net_profit"].setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {color};"
        )

    def _refresh_accounts(self):
        rows = list_financial_accounts(include_inactive=True, db_path=self.db_path)
        self.accounts_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            item = QTableWidgetItem(row["name"])
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.accounts_table.setItem(index, 0, item)
            self.accounts_table.setItem(
                index, 1, QTableWidgetItem(format_yuan(row["balance_cents"]))
            )
            self.accounts_table.setItem(
                index, 2, QTableWidgetItem("正常" if row["is_active"] else "已停用")
            )

    def _refresh_daily(self):
        rows = list_operating_entries(
            date_from=self.dashboard_from.date().toString("yyyy-MM-dd"),
            date_to=self.dashboard_to.date().toString("yyyy-MM-dd"),
            db_path=self.db_path,
        )
        self._fill_entry_table(self.daily_table, rows, audit=False)

    def _refresh_counterparties(self):
        customers = list_counterparty_balances("customer", self.db_path)
        self.receivable_table.setRowCount(len(customers))
        for index, row in enumerate(customers):
            balance = row["balance_cents"]
            self.receivable_table.setItem(index, 0, QTableWidgetItem(row["name"]))
            amount = QTableWidgetItem(format_yuan(abs(balance)))
            amount.setForeground(QColor("#D32F2F" if balance > 0 else "#388E3C"))
            self.receivable_table.setItem(index, 1, amount)
            self.receivable_table.setItem(
                index, 2, QTableWidgetItem("应收" if balance > 0 else "预收")
            )
            self.receivable_table.setItem(
                index, 3, QTableWidgetItem(str(row.get("open_item_count") or 0))
            )
            oldest = row.get("oldest_open_date")
            age = (
                max((date.today() - date.fromisoformat(oldest)).days, 0)
                if oldest
                else None
            )
            self.receivable_table.setItem(
                index, 4, QTableWidgetItem(f"{age} 天" if age is not None else "—")
            )
            self.receivable_table.setItem(
                index,
                5,
                QTableWidgetItem(
                    " | ".join(filter(None, [row["wechat"] or "", row["phone"] or ""]))
                ),
            )
            button = QPushButton("收款")
            button.clicked.connect(
                lambda _, customer_id=row["id"], pending=balance: self._receive(
                    customer_id, pending
                )
            )
            self.receivable_table.setCellWidget(index, 6, button)
        suppliers = list_counterparty_balances("supplier", self.db_path)
        self.payable_table.setRowCount(len(suppliers))
        for index, row in enumerate(suppliers):
            balance = row["balance_cents"]
            self.payable_table.setItem(index, 0, QTableWidgetItem(row["name"]))
            self.payable_table.setItem(
                index, 1, QTableWidgetItem(format_yuan(abs(balance)))
            )
            self.payable_table.setItem(
                index, 2, QTableWidgetItem("应付" if balance > 0 else "预付")
            )
            self.payable_table.setItem(
                index, 3, QTableWidgetItem(str(row.get("open_item_count") or 0))
            )
            oldest = row.get("oldest_open_date")
            age = (
                max((date.today() - date.fromisoformat(oldest)).days, 0)
                if oldest
                else None
            )
            self.payable_table.setItem(
                index, 4, QTableWidgetItem(f"{age} 天" if age is not None else "—")
            )
            self.payable_table.setItem(
                index,
                5,
                QTableWidgetItem(
                    " | ".join(filter(None, [row["wechat"] or "", row["phone"] or ""]))
                ),
            )
            button = QPushButton("付款")
            button.clicked.connect(
                lambda _, supplier_id=row["id"], pending=balance: self._pay(
                    supplier_id, pending
                )
            )
            self.payable_table.setCellWidget(index, 6, button)

    def _refresh_profit(self):
        rows = get_profit_report(
            date_from=self.profit_from.date().toString("yyyy-MM-dd"),
            date_to=self.profit_to.date().toString("yyyy-MM-dd"),
            group_by=self.profit_group.currentData(),
            db_path=self.db_path,
        )
        self.profit_table.setRowCount(len(rows))
        totals = {
            "sales_cents": 0,
            "cogs_cents": 0,
            "expense_cents": 0,
            "other_income_cents": 0,
        }
        for index, row in enumerate(rows):
            gross = row["sales_cents"] - row["cogs_cents"]
            net = gross - row["expense_cents"] + row["other_income_cents"]
            values = (
                row["label"] or str(row["group_key"]),
                format_yuan(row["sales_cents"]),
                format_yuan(row["cogs_cents"]),
                format_yuan(gross),
                format_yuan(row["expense_cents"]),
                format_yuan(row["other_income_cents"]),
                format_yuan(net),
            )
            for column, value in enumerate(values):
                self.profit_table.setItem(index, column, QTableWidgetItem(value))
            for key in totals:
                totals[key] += row[key]
        gross = totals["sales_cents"] - totals["cogs_cents"]
        net = gross - totals["expense_cents"] + totals["other_income_cents"]
        self.profit_summary.setText(
            f"销售 {format_yuan(totals['sales_cents'])}　"
            f"毛利润 {format_yuan(gross)}　净利润 {format_yuan(net)}"
        )

    def _refresh_audit(self):
        rows = list_operating_entries(
            date_from=self.audit_from.date().toString("yyyy-MM-dd"),
            date_to=self.audit_to.date().toString("yyyy-MM-dd"),
            event_type=self.audit_event.currentData(),
            status=self.audit_status.currentData(),
            source_id=self.audit_entity_id.text().strip() or None,
            keyword=self.audit_keyword.text().strip(),
            db_path=self.db_path,
        )
        self._fill_entry_table(self.audit_table, rows, audit=True)

    def _fill_entry_table(self, table, rows, *, audit):
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            name = row.get("customer_name") or row.get("supplier_name") or ""
            if audit:
                values = (
                    str(row["id"]),
                    row["entry_date"],
                    EVENT_LABELS.get(row["event_type"], row["event_type"]),
                    format_source_label(row["source_type"], row.get("source_id")),
                    name,
                    format_yuan(row["debit_total_cents"]),
                    row["status"],
                    row.get("reason") or row.get("remark") or "",
                )
            else:
                values = (
                    str(row["id"]),
                    row["entry_date"],
                    EVENT_LABELS.get(row["event_type"], row["event_type"]),
                    name,
                    format_yuan(row["cash_change_cents"]),
                    row["status"],
                    row.get("remark") or "",
                )
            for column, value in enumerate(values):
                table.setItem(index, column, QTableWidgetItem(str(value)))

    def _setup_finance(self):
        dialog = FinanceSetupDialog(self, db_path=self.db_path)
        if not dialog.exec():
            return
        data = dialog.get_data()
        accounts = [
            {
                "name": row["name"],
                "opening_balance_cents": yuan_to_cents(row["opening_yuan"]),
            }
            for row in data["accounts"]
        ]
        try:
            self.finance_service.initialize_finance(data["enabled_at"], accounts)
        except ServiceError as exc:
            QMessageBox.warning(self, "启用失败", str(exc))
            return
        self._changed("经营记账已启用")

    def _manual_entry(self, kind):
        dialog = ManualEntryDialog(kind, self, db_path=self.db_path)
        if not dialog.exec():
            return
        data = dialog.get_data()
        data["amount_cents"] = yuan_to_cents(data.pop("amount"))
        try:
            if kind == "income":
                self.finance_service.record_income(**data)
            else:
                self.finance_service.record_expense(**data)
        except ServiceError as exc:
            QMessageBox.warning(self, "记账失败", str(exc))
            return
        self._changed("记录已保存")

    def _transfer(self):
        dialog = TransferDialog(self, db_path=self.db_path)
        if not dialog.exec():
            return
        data = dialog.get_data()
        data["amount_cents"] = yuan_to_cents(data.pop("amount"))
        try:
            self.finance_service.transfer(**data)
        except ServiceError as exc:
            QMessageBox.warning(self, "转账失败", str(exc))
            return
        self._changed("账户转账已保存")

    def _adjust(self, reconcile):
        dialog = AdjustmentDialog(self, db_path=self.db_path, reconcile=reconcile)
        if not dialog.exec():
            return
        data = dialog.get_data()
        amount_cents = yuan_to_cents(data.pop("amount"))
        try:
            if reconcile:
                self.finance_service.reconcile_account(
                    actual_balance_cents=amount_cents, **data
                )
            else:
                self.finance_service.adjust_account(
                    delta_cents=amount_cents, **data
                )
        except ServiceError as exc:
            QMessageBox.warning(self, "资金调整失败", str(exc))
            return
        self._changed("资金账户已更新")

    def _add_account(self):
        name, ok = QInputDialog.getText(self, "新增账户", "账户名称：")
        if not ok:
            return
        try:
            self.finance_service.create_account(name)
        except ServiceError as exc:
            QMessageBox.warning(self, "新增账户失败", str(exc))
            return
        self._changed("账户已新增")

    def _toggle_account(self):
        row = self.accounts_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择资金账户")
            return
        item = self.accounts_table.item(row, 0)
        account_id = item.data(Qt.ItemDataRole.UserRole)
        active = self.accounts_table.item(row, 2).text() == "正常"
        try:
            self.finance_service.update_account(
                account_id,
                name=item.text(),
                is_active=not active,
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "更新账户失败", str(exc))
            return
        self._changed("账户状态已更新")

    def _add_category(self):
        kind, ok = QInputDialog.getItem(
            self, "新增分类", "类型：", ["费用", "收入"], editable=False
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "新增分类", "分类名称：")
        if not ok:
            return
        try:
            self.finance_service.create_category(
                name, "expense" if kind == "费用" else "income"
            )
        except (ServiceError, Exception) as exc:
            QMessageBox.warning(self, "新增分类失败", str(exc))
            return
        self._changed("分类已新增")

    def _toggle_category(self):
        rows = list_finance_categories(
            include_inactive=True, db_path=self.db_path
        )
        labels = [
            f"{'费用' if row['kind']=='expense' else '收入'} / {row['name']} / "
            f"{'正常' if row['is_active'] else '已停用'}"
            for row in rows
        ]
        selected, ok = QInputDialog.getItem(
            self, "分类状态", "选择分类：", labels, editable=False
        )
        if not ok:
            return
        row = rows[labels.index(selected)]
        try:
            self.finance_service.update_category(
                row["id"],
                name=row["name"],
                is_active=not row["is_active"],
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "更新分类失败", str(exc))
            return
        self._changed("分类状态已更新")

    def _receive(self, customer_id, pending):
        dialog = PaymentDialog(
            self,
            title="客户收款",
            preview_pending=abs(pending) / 100,
            db_path=self.db_path,
        )
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
                account_id=data["account_id"],
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "收款失败", str(exc))
            return
        self._changed("客户收款已记录")

    def _pay(self, supplier_id, pending):
        dialog = PaymentDialog(
            self,
            title="供应商付款",
            pay_type="payable",
            preview_pending=abs(pending) / 100,
            db_path=self.db_path,
        )
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
                account_id=data["account_id"],
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "付款失败", str(exc))
            return
        self._changed("供应商付款已记录")

    def _selected_entry_id(self):
        table = (
            self.audit_table
            if self.section_tabs.currentIndex() == 4
            else self.daily_table
        )
        row = table.currentRow()
        if row < 0:
            return None
        return int(table.item(row, 0).text())

    def _show_entry_detail(self, *_):
        table = self.sender()
        row = table.currentRow()
        if row < 0:
            return
        detail = get_operating_entry_detail(
            int(table.item(row, 0).text()), self.db_path
        )
        if not detail:
            return
        lines = [
            f"日期：{detail['entry_date']}",
            f"业务：{EVENT_LABELS.get(detail['event_type'], detail['event_type'])}",
            f"来源：{format_source_label(detail['source_type'], detail.get('source_id'))}",
            f"状态：{detail['status']}",
            f"原因：{detail.get('reason') or '—'}",
            "",
            "平衡分录：",
        ]
        for line in detail["lines"]:
            amount = line["debit_cents"] or line["credit_cents"]
            direction = "增加" if line["debit_cents"] else "减少/来源"
            relation = (
                line.get("customer_name")
                or line.get("supplier_name")
                or line.get("category_name")
                or ""
            )
            lines.append(
                f"- {line['account_name']}　{direction} {format_yuan(amount)}　{relation}"
            )
        if detail.get("payment"):
            payment = detail["payment"]
            lines.extend(
                [
                    "",
                    "收付款：",
                    f"- 流水 #{payment['id']}　"
                    f"{format_yuan(payment['amount_cents'])}　"
                    f"{payment['pay_date']}　{payment.get('entry_kind') or ''}",
                ]
            )
            for allocation in detail.get("allocations", []):
                target = (
                    f"报价 #{allocation['quote_id']}"
                    if "quote_id" in allocation
                    else f"批次 #{allocation['batch_id']}"
                )
                lines.append(
                    f"- FIFO 分配至 {target}："
                    f"{format_yuan(allocation['amount_cents'])}"
                )
        for snapshot in detail.get("shipment_snapshots", []):
            lines.extend(
                [
                    "",
                    "出库成本快照：",
                    f"- 报价 #{snapshot['quote_id']}，数量 {snapshot['quantity']}，"
                    f"销售 {format_yuan(snapshot['revenue_cents'])}，"
                    f"成本 {format_yuan(snapshot['cost_cents'])}",
                ]
            )
        for return_row in detail.get("sales_returns", []):
            lines.append(
                f"- 销售退货 #{return_row['id']}：数量 {return_row['quantity']}，"
                f"冲减收入 {format_yuan(return_row['revenue_cents'])}"
            )
        for return_row in detail.get("purchase_returns", []):
            lines.append(
                f"- 采购退货 #{return_row['id']}：数量 {return_row['quantity']}，"
                f"金额 {format_yuan(return_row['amount_cents'])}"
            )
        if detail.get("related_entries"):
            lines.extend(["", "冲销 / 更正关联："])
            for related in detail["related_entries"]:
                lines.append(
                    f"- 账本 #{related['id']}　"
                    f"{EVENT_LABELS.get(related['event_type'], related['event_type'])}　"
                    f"{related['status']}"
                )
        dialog = QDialog(self)
        dialog.setWindowTitle(f"账务详情 #{detail['id']}")
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _void_selected_entry(self):
        entry_id = self._selected_entry_id()
        if entry_id is None:
            QMessageBox.warning(self, "提示", "请先选择流水")
            return
        reason, ok = QInputDialog.getText(self, "作废手工账", "作废原因：")
        if not ok:
            return
        try:
            self.finance_service.void_entry(
                entry_id,
                entry_date=date.today().isoformat(),
                reason=reason,
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "作废失败", str(exc))
            return
        self._changed("手工账已作废")

    def _correct_selected_payment(self):
        entry_id = self._selected_entry_id()
        if entry_id is None:
            QMessageBox.warning(self, "提示", "请先选择收付款流水")
            return
        detail = get_operating_entry_detail(entry_id, self.db_path)
        if not detail or detail["source_type"] != "payment":
            QMessageBox.warning(self, "提示", "选择的记录不是可更正收付款")
            return
        payment_id = int(detail["source_id"])
        payment = get_payment_detail(payment_id, self.db_path)
        if not payment:
            return
        dialog = PaymentEditDialog(self, payment, db_path=self.db_path)
        if not dialog.exec():
            return
        reason, ok = QInputDialog.getText(self, "更正收付款", "更正原因：")
        if not ok:
            return
        data = dialog.get_data()
        try:
            self.payment_service.correct_payment(
                payment_id,
                amount_cents=yuan_to_cents(data["amount"]),
                pay_date=data["pay_date"],
                method=data["method"],
                account_id=data["account_id"],
                remark=data["remark"],
                reason=reason,
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "更正失败", str(exc))
            return
        self._changed("收付款已更正")

    def _export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出财务报表",
            f"经营财务_{QDate.currentDate().toString('yyyyMMdd')}.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            export_finance_to_excel(
                path,
                date_from=self.dashboard_from.date().toString("yyyy-MM-dd"),
                date_to=self.dashboard_to.date().toString("yyyy-MM-dd"),
                db_path=self.db_path,
            )
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出成功", path)

    def _changed(self, message):
        QMessageBox.information(self, "成功", message)
        self.refresh()
        self.data_changed.emit()
