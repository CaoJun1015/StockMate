"""报价记录 Tab"""
from datetime import datetime, date as date_type

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QComboBox, QTextEdit,
    QMessageBox, QDialog,
    QSpinBox, QDateEdit, QDialogButtonBox,
    QFrame, QHeaderView, QAbstractItemView, QCheckBox, QGroupBox,
    QApplication,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QClipboard

from src.models.queries import (
    export_quotes,
    get_batch_detail,
    get_quote_detail,
    list_batches,
    list_products,
    list_shipment_allocations,
    search_quotes,
)
from src.services.exceptions import ServiceError
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.payment_service import PaymentService
from src.services.product_service import ProductService
from src.services.return_service import ReturnService
from src.utils.image_gen import generate_quote_image, generate_single_quote_card, WATERMARK_TEXT
from src.utils.excel_export import export_quotes_to_excel
from src.utils.follow_up import get_stale_quotes, format_reminder_text
from src.utils.monthly_report import get_monthly_report, format_report_text
from src.utils.shipment_flow import parse_sn_input, validate_sn, validate_sn_list, generate_shipment_receipt
from src.utils.money import format_yuan, yuan_to_cents
from src.utils.tax import calc_tax_adjusted_profit_cents
from src.ui.dialogs import ShipmentDialog, QuoteEditDialog
from src.ui.finance_dialogs import PaymentDialog, ReturnDialog
from src.ui.utils import refreshing_table


class RecordTab(QWidget):
    """报价记录 Tab，嵌入 MainWindow"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.record_table = None
        self.inventory_service = InventoryService()
        self.order_service = OrderService()
        self.payment_service = PaymentService()
        self.return_service = ReturnService()
        self.product_service = ProductService()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.confirm_record_btn = QPushButton("确认报价")
        self.confirm_record_btn.setObjectName("successBtn")
        self.confirm_record_btn.clicked.connect(self.on_confirm_quote)
        self.ship_record_btn = QPushButton("出库")
        self.ship_record_btn.setObjectName("warningBtn")
        self.ship_record_btn.clicked.connect(self.on_ship_quote)
        self.receive_btn = QPushButton("收款")
        self.receive_btn.setObjectName("primaryBtn")
        self.receive_btn.clicked.connect(self.on_receive_payment)
        self.return_btn = QPushButton("销售退货")
        self.return_btn.setObjectName("warningBtn")
        self.return_btn.clicked.connect(self.on_return_sale)
        self.shipment_detail_btn = QPushButton("出库明细")
        self.shipment_detail_btn.setObjectName("ghostBtn")
        self.shipment_detail_btn.clicked.connect(self.on_shipment_detail)
        self.cancel_record_btn = QPushButton("取消订单")
        self.cancel_record_btn.setObjectName("ghostBtn")
        self.cancel_record_btn.clicked.connect(self.on_cancel_quote)
        self.edit_record_btn = QPushButton("编辑")
        self.edit_record_btn.setObjectName("ghostBtn")
        self.edit_record_btn.clicked.connect(self.on_edit_quote)
        self.del_record_btn = QPushButton("删除")
        self.del_record_btn.setObjectName("dangerBtn")
        self.del_record_btn.clicked.connect(self.on_delete_quote)
        self.refresh_records_btn = QPushButton("刷新")
        self.refresh_records_btn.setObjectName("ghostBtn")
        self.refresh_records_btn.clicked.connect(self.refresh_records)
        btn_row.addWidget(self.confirm_record_btn)
        btn_row.addWidget(self.ship_record_btn)
        btn_row.addWidget(self.receive_btn)
        btn_row.addWidget(self.return_btn)
        btn_row.addWidget(self.shipment_detail_btn)
        btn_row.addWidget(self.cancel_record_btn)
        btn_row.addWidget(self.edit_record_btn)
        btn_row.addWidget(self.del_record_btn)
        btn_row.addWidget(self.refresh_records_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.record_search = QLineEdit()
        self.record_search.setPlaceholderText("搜索机型/序列...")
        self.record_search.textChanged.connect(self.refresh_records)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.dateChanged.connect(self.refresh_records)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self.refresh_records)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["全部状态", "待确认", "已报价", "已出库", "已收款", "已全退", "已取消"])
        self.status_filter.currentTextChanged.connect(self.refresh_records)

        self.export_records_btn = QPushButton("导出 Excel")
        self.export_records_btn.setObjectName("ghostBtn")
        self.export_records_btn.clicked.connect(self.on_export_records_excel)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("开始:"))
        filter_layout.addWidget(self.date_from)
        filter_layout.addWidget(QLabel("结束:"))
        filter_layout.addWidget(self.date_to)
        filter_layout.addWidget(QLabel("状态:"))
        filter_layout.addWidget(self.status_filter)
        filter_layout.addWidget(QLabel("搜索:"))
        filter_layout.addWidget(self.record_search)
        filter_layout.addStretch()
        filter_layout.addWidget(self.export_records_btn)

        filter_card = QFrame()
        filter_card.setObjectName("filterCard")
        filter_card_layout = QVBoxLayout(filter_card)
        filter_card_layout.setContentsMargins(12, 8, 12, 8)
        filter_card_layout.addLayout(filter_layout)
        layout.addWidget(filter_card)

        self.record_table = QTableWidget()
        self.record_table.setAlternatingRowColors(True)
        self.record_table.setColumnCount(17)
        self.record_table.setHorizontalHeaderLabels(
            ["ID", "日期", "客户", "机型", "CPU", "内存", "硬盘", "显卡", "上游",
             "购入价", "数量", "对外报价", "状态", "已收款", "SN", "备注", "打款"]
        )
        self.record_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.record_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.record_table.horizontalHeader().setStretchLastSection(True)
        self.record_table.setColumnHidden(0, True)
        layout.addWidget(self.record_table)

        self.stats_label = QLabel()
        self.stats_label.setObjectName("summaryLabel")
        layout.addWidget(self.stats_label)

    # -------------------------------------------------------
    # 报价记录
    # -------------------------------------------------------
    def refresh_records(self):
        keyword = self.record_search.text().strip()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        quotes = search_quotes(keyword, date_from, date_to)

        status_filter = self.status_filter.currentText()
        if status_filter != "全部状态":
            quotes = [q for q in quotes if q.get("status", "待确认") == status_filter]

        STATUS_COLORS = {
            "待确认": "#9E9E9E",
            "已报价": "#1976D2",
            "已出库": "#F57C00",
            "已收款": "#388E3C",
            "已全退": "#7B1FA2",
            "已取消": "#BDBDBD",
        }

        with refreshing_table(self.record_table, key_column=0):
            self.record_table.setRowCount(len(quotes))
            total_cost = 0
            total_sale = 0
            for i, q in enumerate(quotes):
                status = q.get("status", "待确认")
                received = q.get("received_amount_cents", 0) or 0
                sn_list = q.get("sn_list", "") or q.get("batch_sn_list", "") or ""
                quote_price = q.get("quote_price_cents", 0) or 0
                quote_quantity = q.get("quote_quantity", 1) or 1
                total_amount = q.get("net_total_cents", quote_price * quote_quantity) or 0

                self.record_table.setItem(i, 0, QTableWidgetItem(str(q.get("id", ""))))
                self.record_table.setItem(i, 1, QTableWidgetItem(q.get("quote_date", "")))
                self.record_table.setItem(i, 2, QTableWidgetItem(q.get("customer_name", "")))
                self.record_table.setItem(i, 3, QTableWidgetItem(q.get("series", "")))
                self.record_table.setItem(i, 4, QTableWidgetItem(q.get("cpu", "")))
                self.record_table.setItem(i, 5, QTableWidgetItem(q.get("ram", "")))
                self.record_table.setItem(i, 6, QTableWidgetItem(q.get("storage", "")))
                self.record_table.setItem(i, 7, QTableWidgetItem(q.get("gpu", "")))
                self.record_table.setItem(i, 8, QTableWidgetItem(q.get("supplier_name", "") or ""))
                self.record_table.setItem(
                    i,
                    9,
                    QTableWidgetItem(
                        format_yuan(q.get("purchase_price_cents", 0))
                        if q.get("purchase_price_cents")
                        else ""
                    ),
                )
                self.record_table.setItem(i, 10, QTableWidgetItem(str(quote_quantity)))
                self.record_table.setItem(
                    i, 11, QTableWidgetItem(format_yuan(quote_price) if quote_price else "")
                )

                status_item = QTableWidgetItem(status)
                color = STATUS_COLORS.get(status, "#333")
                status_item.setForeground(Qt.GlobalColor.white)
                status_item.setBackground(QColor(color))
                self.record_table.setItem(i, 12, status_item)

                received_text = format_yuan(received)
                received_item = QTableWidgetItem(received_text)
                if received >= total_amount and total_amount > 0:
                    received_item.setForeground(QColor("#388E3C"))
                self.record_table.setItem(i, 13, received_item)

                self.record_table.setItem(i, 14, QTableWidgetItem(sn_list))

                batch_remark = q.get("batch_remark", "") or ""
                quote_remark = q.get("remark", "") or ""
                merged_remark = " | ".join(filter(None, [batch_remark, quote_remark]))
                self.record_table.setItem(i, 15, QTableWidgetItem(merged_remark))
                self.record_table.setItem(i, 16, QTableWidgetItem(q.get("paid", "否")))

                total_cost += (q.get("purchase_price_cents", 0) or 0) * max(
                    quote_quantity - (q.get("returned_quantity", 0) or 0), 0
                )
                total_sale += total_amount

                # 收款提醒：已出库超过7天未收满的订单，整行红色高亮
                if status == "已出库" and received < total_amount and total_amount > 0:
                    from datetime import date, datetime
                    quote_date = q.get("quote_date", "")
                    if quote_date:
                        try:
                            quote_dt = datetime.strptime(quote_date, "%Y-%m-%d").date()
                            if (date.today() - quote_dt).days > 7:
                                for col in range(self.record_table.columnCount()):
                                    item = self.record_table.item(i, col)
                                    if item:
                                        item.setForeground(QColor("#D32F2F"))
                        except ValueError:
                            pass

        self.record_table.resizeColumnsToContents()
        self.record_table.setColumnWidth(12, 70)
        self.record_table.setColumnWidth(13, 80)
        self.record_table.setColumnWidth(14, 120)

        # 税后利润统计
        tax_total_cost = 0
        tax_total_sale = 0
        total_tax_profit = 0
        for q in quotes:
            purchase_price = q.get("purchase_price_cents", 0) or 0
            quote_price = q.get("quote_price_cents", 0) or 0
            quantity = q.get("quote_quantity", 1) or 1
            tax_rate = q.get("tax_rate")
            purchase_tax_inclusive = q.get("purchase_tax_inclusive", 0) or 0
            quote_tax_inclusive = q.get("quote_tax_inclusive", 0) or 0
            net_quantity = max(quantity - (q.get("returned_quantity", 0) or 0), 0)
            tax_total_cost += purchase_price * net_quantity
            tax_total_sale += q.get("net_total_cents", quote_price * quantity) or 0
            total_tax_profit += calc_tax_adjusted_profit_cents(
                purchase_price, quote_price, net_quantity, tax_rate,
                purchase_tax_inclusive, quote_tax_inclusive,
            )
        profit = total_tax_profit
        self.stats_label.setText(
            f"共 {len(quotes)} 条记录  |  总购入: {format_yuan(tax_total_cost)}  |  "
            f"总报价: {format_yuan(tax_total_sale)}  |  毛利: {format_yuan(profit)}"
        )

    def on_edit_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote:
            QMessageBox.warning(self, "错误", "无法获取报价记录信息")
            return
        dlg = QuoteEditDialog(self, quote)
        if dlg.exec():
            data = dlg.get_data()
            if data["batch_id"]:
                try:
                    self.order_service.update_quote(
                        quote_id,
                        batch_id=data["batch_id"],
                        customer_id=data["customer_id"],
                        quote_price_cents=yuan_to_cents(data["quote_price"]),
                        quote_quantity=data["quote_quantity"],
                        quote_date=data["quote_date"],
                        remark=data["remark"],
                        paid=data["paid"],
                        sn_list=data.get("sn_list", ""),
                        tax_rate=data.get("tax_rate"),
                        purchase_tax_inclusive=bool(
                            data.get("purchase_tax_inclusive", 0)
                        ),
                        quote_tax_inclusive=bool(data.get("quote_tax_inclusive", 0)),
                    )
                except ServiceError as exc:
                    QMessageBox.warning(self, "编辑失败", str(exc))
                    return
                self.refresh_records()

    def on_delete_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        series = self.record_table.item(row, 3).text() if self.record_table.item(row, 3) else ""
        customer = self.record_table.item(row, 2).text() if self.record_table.item(row, 2) else ""
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除报价记录「{customer} - {series}」？\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.order_service.delete_quote(
                    quote_id,
                    f"用户删除报价：客户={customer}, 机型={series}",
                )
            except ServiceError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return
            self.refresh_records()

    def on_confirm_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote:
            return
        status = quote.get("status", "")
        if status != "待确认":
            QMessageBox.warning(self, "提示", f"当前状态为「{status}」，只有「待确认」状态的订单可以确认报价")
            return
        reply = QMessageBox.question(
            self, "确认报价", f"确定将此订单状态更新为「已报价」？\n\n客户: {quote.get('customer_name','')}\n机型: {quote.get('series','')} {quote.get('cpu','')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.order_service.transition(quote_id, "已报价", "用户确认报价")
            except ServiceError as exc:
                QMessageBox.warning(self, "确认失败", str(exc))
                return
            self.refresh_records()

    def on_ship_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote:
            return
        status = quote.get("status", "")
        if status not in ("待确认", "已报价"):
            QMessageBox.warning(self, "提示", f"当前状态为「{status}」，只有「待确认」或「已报价」状态的订单可以出库")
            return

        batch = get_batch_detail(quote.get("batch_id"))
        product_id = batch["product_id"] if batch else None

        batches = list_batches(product_id) if product_id else []
        if not batches:
            QMessageBox.warning(self, "提示", "没有可用批次，无法出库")
            return

        dlg = ShipmentDialog(self, quote, batches)
        if dlg.exec():
            data = dlg.get_data()
            try:
                result = self.inventory_service.ship_quote(
                    quote_id,
                    shipped_date=data.get("shipped_date"),
                    allocations=data.get("allocations"),
                    remark=data.get("remark", ""),
                )
            except ServiceError as exc:
                QMessageBox.warning(self, "出库失败", str(exc))
                return
            warnings = result.get("sn_compatibility_warnings", []) if result else []
            message = "出库成功"
            if warnings:
                message += "\n\n以下SN因历史入库SN不完整已兼容放行，并写入审计：\n"
                message += "、".join(warnings[:10])
            QMessageBox.information(self, "成功", message)
            self.refresh_records()

    def on_receive_payment(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote:
            return
        status = quote.get("status", "")
        if status in ("已取消",):
            QMessageBox.warning(self, "提示", f"当前状态为「{status}」，无法收款")
            return

        dlg = PaymentDialog(self, title="收款", pay_type="receivable", quote=quote)
        if dlg.exec():
            data = dlg.get_data()
            if data["amount"] <= 0:
                QMessageBox.warning(self, "提示", "请输入收款金额")
                return
            customer_id = quote.get("customer_id")
            if not customer_id:
                QMessageBox.warning(self, "收款失败", "报价未关联客户，无法记录客户收款")
                return
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
            QMessageBox.information(self, "成功", f"收款 ¥{data['amount']:.0f} 已记录！")
            self.refresh_records()

    def on_return_sale(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条已出库记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote or quote.get("status") not in ("已出库", "已收款"):
            QMessageBox.warning(self, "提示", "只有已出库或已收款订单可以退货")
            return
        dialog = ReturnDialog(
            "销售退货",
            self,
            max_quantity=quote.get("quote_quantity", 1),
            allow_restock=True,
            allocations=list_shipment_allocations(quote_id),
        )
        if not dialog.exec():
            return
        data = dialog.get_data()
        try:
            self.return_service.return_sale(
                quote_id,
                quantity=data["quantity"],
                return_date=data["date"],
                restock=data["restock"],
                reason=data["reason"],
                refund_account_id=data["account_id"],
                cash_refund_cents=yuan_to_cents(data["refund"]),
                restock_allocations=data.get("restock_allocations"),
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "销售退货失败", str(exc))
            return
        QMessageBox.information(self, "成功", "销售退货已记录")
        self.refresh_records()

    def on_shipment_detail(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        allocations = list_shipment_allocations(quote_id)
        if not allocations:
            QMessageBox.information(self, "出库明细", "该报价尚无出库批次分配")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"报价#{quote_id} 出库明细")
        dialog.resize(720, 360)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(allocations), 5)
        table.setHorizontalHeaderLabels(["批次", "数量", "单位成本", "总成本", "SN"])
        for r, allocation in enumerate(allocations):
            values = (
                allocation["batch_id"], allocation["quantity"],
                format_yuan(allocation["unit_cost_cents"]),
                format_yuan(allocation["cost_cents"]), allocation.get("sn_list", ""),
            )
            for col, value in enumerate(values):
                table.setItem(r, col, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def on_cancel_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_detail(quote_id)
        if not quote:
            return
        status = quote.get("status", "")
        if status == "已收款":
            QMessageBox.warning(self, "提示", "已收款的订单不能取消，如需退款请手动处理")
            return
        reply = QMessageBox.question(
            self, "确认取消",
            f"确定取消此订单？\n\n客户: {quote.get('customer_name','')}\n机型: {quote.get('series','')} {quote.get('cpu','')}\n当前状态: {status}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.order_service.cancel_quote(quote_id, "用户取消订单")
            except ServiceError as exc:
                QMessageBox.warning(self, "取消失败", str(exc))
                return
            self.refresh_records()

    # -------------------------------------------------------
    # Skill 3: 智能跟单提醒
    # -------------------------------------------------------
    def on_follow_up(self):
        stale = get_stale_quotes(stale_days=3)
        text = format_reminder_text(stale)

        dlg = QDialog(self)
        dlg.setWindowTitle("🔔 跟单提醒")
        dlg.setMinimumSize(500, 400)
        layout = QVBoxLayout(dlg)

        label = QLabel(text)
        label.setObjectName("reportText")
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    # -------------------------------------------------------
    # Skill 4: 月度经营报告
    # -------------------------------------------------------
    def on_monthly_report(self):
        report = get_monthly_report()
        text = format_report_text(report)

        dlg = QDialog(self)
        dlg.setWindowTitle("📊 月度经营报告")
        dlg.setMinimumSize(550, 500)
        layout = QVBoxLayout(dlg)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(text)
        text_edit.setObjectName("reportText")
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    # -------------------------------------------------------
    # 群发图片
    # -------------------------------------------------------
    def on_broadcast(self):
        products = list_products()
        if not products:
            QMessageBox.warning(self, "提示", "还没有机型数据，请先新增机型")
            return

        # 弹出选择对话框
        dlg = QDialog(self)
        dlg.setWindowTitle("选择要群发的机型")
        dlg.setMinimumSize(600, 500)
        layout = QVBoxLayout(dlg)

        label = QLabel("勾选要生成的机型（默认全选）：")
        layout.addWidget(label)

        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["选中", "系列", "CPU", "内存", "硬盘", "显卡", "备注"])
        table.setRowCount(len(products))
        checkboxes = []
        for i, p in enumerate(products):
            cb = QCheckBox()
            cb.setChecked(True)
            checkboxes.append(cb)
            w = QWidget()
            w_layout = QHBoxLayout(w)
            w_layout.addWidget(cb)
            w_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w_layout.setContentsMargins(0, 0, 0, 0)
            table.setCellWidget(i, 0, w)
            table.setItem(i, 1, QTableWidgetItem(p.get("series", "")))
            table.setItem(i, 2, QTableWidgetItem(p.get("cpu", "")))
            table.setItem(i, 3, QTableWidgetItem(p.get("ram", "")))
            table.setItem(i, 4, QTableWidgetItem(p.get("storage", "")))
            table.setItem(i, 5, QTableWidgetItem(p.get("gpu", "")))
            table.setItem(i, 6, QTableWidgetItem(p.get("note", "")))

        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        unselect_all_btn = QPushButton("取消全选")
        select_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
        unselect_all_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(unselect_all_btn)
        btn_layout.addStretch()

        gen_btn = QPushButton("生成群发图片")
        gen_btn.setObjectName("successBtn")
        gen_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(gen_btn)
        layout.addLayout(btn_layout)

        if dlg.exec():
            selected = []
            for i, cb in enumerate(checkboxes):
                if cb.isChecked() and i < len(products):
                    selected.append(products[i])
            if not selected:
                QMessageBox.warning(self, "提示", "请至少选择一个机型")
                return
            paths = generate_quote_image(selected)
            msg = f"已生成 {len(paths)} 张图片:\n" + "\n".join(paths)
            QMessageBox.information(self, "生成完成", msg)

    # -------------------------------------------------------
    # 导出 Excel
    # -------------------------------------------------------
    def on_export_records_excel(self):
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        quotes = export_quotes(date_from, date_to)
        if not quotes:
            QMessageBox.warning(self, "提示", "当前筛选条件下没有报价记录")
            return
        output = export_quotes_to_excel(quotes)
        QMessageBox.information(self, "导出成功", f"报价记录已导出:\n{output}")

    def on_export_excel(self):
        """从菜单/工具栏导出全部记录"""
        self.main.tabs.setCurrentIndex(2)  # 切到报价记录 tab
        QMessageBox.information(self, "提示", "请切换到「报价记录」标签页，筛选后点击「导出为 Excel」")

    # -------------------------------------------------------
    # Skill: 出库一条龙
    # -------------------------------------------------------
    def on_shipment_flow(self):
        """出库一条龙 - SN批量校验"""
        dlg = QDialog(self)
        dlg.setWindowTitle("出库一条龙 - SN批量校验")
        dlg.setMinimumWidth(500)
        dlg.setMinimumHeight(400)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("批量输入SN（支持条码枪连续扫入，换行/逗号分隔）:"))
        sn_input = QTextEdit()
        sn_input.setPlaceholderText("扫码或粘贴SN，多个SN用换行或逗号分隔...")
        layout.addWidget(sn_input)

        qty_row = QHBoxLayout()
        qty_spin = QSpinBox()
        qty_spin.setRange(0, 9999)
        qty_spin.setValue(0)
        qty_row.addWidget(QLabel("期望数量:"))
        qty_row.addWidget(qty_spin)
        qty_row.addStretch()
        layout.addLayout(qty_row)

        result_area = QTextEdit()
        result_area.setReadOnly(True)
        layout.addWidget(result_area)

        btn_row = QHBoxLayout()
        check_btn = QPushButton("校验SN")
        def on_check():
            raw = sn_input.toPlainText()
            sn_list = parse_sn_input(raw)
            if not sn_list:
                result_area.setPlainText("未输入SN")
                return
            expected = qty_spin.value() if qty_spin.value() > 0 else None
            result = validate_sn_list(sn_list, expected)
            text = result["message"] + "\n\n"
            if result["valid"]:
                text += f"有效SN:\n" + "\n".join([f"  {sn}" for sn in result["valid"]]) + "\n"
            if result["invalid"]:
                text += f"无效SN:\n" + "\n".join([f"  {sn} - {msg}" for sn, msg in result["invalid"]]) + "\n"
            result_area.setPlainText(text)

        check_btn.clicked.connect(on_check)

        receipt_btn = QPushButton("生成出库单")
        def on_receipt():
            raw = sn_input.toPlainText()
            sn_list = parse_sn_input(raw)
            valid = [sn for sn in sn_list if validate_sn(sn)[0]]
            if not valid:
                QMessageBox.warning(self, "提示", "没有有效的SN")
                return
            receipt = generate_shipment_receipt(
                {
                    "series": "手动出库",
                    "customer_name": "________",
                    "quote_price_cents": 0,
                    "quote_quantity": len(valid),
                },
                valid
            )
            clipboard = QApplication.clipboard()
            clipboard.setText(receipt)
            QMessageBox.information(self, "出库单", "出库确认单已复制到剪贴板！")

        receipt_btn.clicked.connect(on_receipt)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(check_btn)
        btn_row.addWidget(receipt_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()
