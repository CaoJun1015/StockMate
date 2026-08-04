"""报价记录 Tab"""
import os
from datetime import datetime, date as date_type

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QComboBox, QTextEdit,
    QMessageBox, QFileDialog, QDialog,
    QSpinBox, QDateEdit, QDialogButtonBox,
    QFrame, QHeaderView, QAbstractItemView, QCheckBox, QGroupBox,
    QApplication,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QClipboard

from src.models.database import (
    get_quote_by_id, update_quote_status, add_payment,
    get_batches, search_quotes, export_quotes, delete_quote, update_quote,
    add_operation_log, get_all_products, ship_quote,
    search_products, add_product,
)
from src.models.queries import get_batch_detail
from src.utils.word_parser import parse_word_pricelist, preview_parse
from src.utils.image_gen import generate_quote_image, generate_single_quote_card, WATERMARK_TEXT
from src.utils.excel_export import export_quotes_to_excel
from src.utils.price_diff import save_snapshot, get_latest_snapshot, diff_snapshots
from src.utils.follow_up import get_stale_quotes, format_reminder_text
from src.utils.monthly_report import get_monthly_report, format_report_text
from src.utils.shipment_flow import parse_sn_input, validate_sn, validate_sn_list, generate_shipment_receipt
from src.ui.dialogs import ShipmentDialog, PaymentDialog, QuoteEditDialog


class RecordTab(QWidget):
    """报价记录 Tab，嵌入 MainWindow"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.record_table = None
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
        self.status_filter.addItems(["全部状态", "待确认", "已报价", "已出库", "已收款", "已取消"])
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
            "已取消": "#BDBDBD",
        }

        self.record_table.setSortingEnabled(False)
        self.record_table.setRowCount(len(quotes))
        total_cost = 0
        total_sale = 0
        for i, q in enumerate(quotes):
            status = q.get("status", "待确认")
            received = q.get("received_amount", 0) or 0
            sn_list = q.get("sn_list", "") or q.get("batch_sn_list", "") or ""
            quote_price = q.get("quote_price", 0) or 0
            quote_quantity = q.get("quote_quantity", 1) or 1
            total_amount = quote_price * quote_quantity

            self.record_table.setItem(i, 0, QTableWidgetItem(str(q.get("id", ""))))
            self.record_table.setItem(i, 1, QTableWidgetItem(q.get("quote_date", "")))
            self.record_table.setItem(i, 2, QTableWidgetItem(q.get("customer_name", "")))
            self.record_table.setItem(i, 3, QTableWidgetItem(q.get("series", "")))
            self.record_table.setItem(i, 4, QTableWidgetItem(q.get("cpu", "")))
            self.record_table.setItem(i, 5, QTableWidgetItem(q.get("ram", "")))
            self.record_table.setItem(i, 6, QTableWidgetItem(q.get("storage", "")))
            self.record_table.setItem(i, 7, QTableWidgetItem(q.get("gpu", "")))
            self.record_table.setItem(i, 8, QTableWidgetItem(q.get("supplier_name", "") or ""))
            self.record_table.setItem(i, 9, QTableWidgetItem(f"¥{q.get('purchase_price', 0):.0f}" if q.get('purchase_price') else ""))
            self.record_table.setItem(i, 10, QTableWidgetItem(str(quote_quantity)))
            self.record_table.setItem(i, 11, QTableWidgetItem(f"¥{quote_price:.0f}" if quote_price else ""))

            status_item = QTableWidgetItem(status)
            color = STATUS_COLORS.get(status, "#333")
            status_item.setForeground(Qt.GlobalColor.white)
            status_item.setBackground(QColor(color))
            self.record_table.setItem(i, 12, status_item)

            received_text = f"¥{received:.0f}" if received > 0 else "¥0"
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

            total_cost += (q.get("purchase_price", 0) or 0) * quote_quantity
            total_sale += quote_price * quote_quantity

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

        self.record_table.setSortingEnabled(True)
        self.record_table.resizeColumnsToContents()
        self.record_table.setColumnWidth(12, 70)
        self.record_table.setColumnWidth(13, 80)
        self.record_table.setColumnWidth(14, 120)

        # 税后利润统计
        from src.models.database import calc_tax_adjusted_profit
        tax_total_cost = 0
        tax_total_sale = 0
        total_tax_profit = 0
        for q in quotes:
            purchase_price = q.get("purchase_price", 0) or 0
            quote_price = q.get("quote_price", 0) or 0
            quantity = q.get("quote_quantity", 1) or 1
            tax_rate = q.get("tax_rate")
            purchase_tax_inclusive = q.get("purchase_tax_inclusive", 0) or 0
            quote_tax_inclusive = q.get("quote_tax_inclusive", 0) or 0
            tax_total_cost += purchase_price * quantity
            tax_total_sale += quote_price * quantity
            total_tax_profit += calc_tax_adjusted_profit(
                purchase_price, quote_price, quantity, tax_rate,
                purchase_tax_inclusive, quote_tax_inclusive,
            )
        profit = total_tax_profit
        self.stats_label.setText(
            f"共 {len(quotes)} 条记录  |  总购入: ¥{tax_total_cost:.0f}  |  总报价: ¥{tax_total_sale:.0f}  |  毛利: ¥{profit:.0f}"
        )

    def on_edit_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_by_id(quote_id)
        if not quote:
            QMessageBox.warning(self, "错误", "无法获取报价记录信息")
            return
        dlg = QuoteEditDialog(self, quote)
        if dlg.exec():
            data = dlg.get_data()
            if data["batch_id"]:
                update_quote(
                    quote_id,
                    data["batch_id"],
                    data["customer_id"],
                    data["quote_price"],
                    data["quote_quantity"],
                    data["quote_date"],
                    data["remark"],
                    data["paid"],
                    data.get("sn_list", ""),
                    tax_rate=data.get("tax_rate"),
                    purchase_tax_inclusive=data.get("purchase_tax_inclusive", 0),
                    quote_tax_inclusive=data.get("quote_tax_inclusive", 0),
                )
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
            delete_quote(quote_id)
            add_operation_log("删除报价", "quotes", quote_id, f"客户={customer}, 机型={series}")
            self.refresh_records()

    def on_confirm_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_by_id(quote_id)
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
            update_quote_status(quote_id, "已报价")
            self.refresh_records()

    def on_ship_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_by_id(quote_id)
        if not quote:
            return
        status = quote.get("status", "")
        if status not in ("待确认", "已报价"):
            QMessageBox.warning(self, "提示", f"当前状态为「{status}」，只有「待确认」或「已报价」状态的订单可以出库")
            return

        batch = get_batch_detail(quote.get("batch_id"))
        product_id = batch["product_id"] if batch else None

        batches = get_batches(product_id) if product_id else []
        if not batches:
            QMessageBox.warning(self, "提示", "没有可用批次，无法出库")
            return

        dlg = ShipmentDialog(self, quote, batches)
        if dlg.exec():
            data = dlg.get_data()
            # 使用封装好的 ship_quote 函数（含校验+扣减+保存SN+状态更新）
            success, msg = ship_quote(quote_id, data.get("sn_list", ""))
            if success:
                QMessageBox.information(self, "成功", msg)
                add_operation_log("出库", "quotes", quote_id, f"SN={data.get('sn_list', '')}")
                self.refresh_records()
            else:
                QMessageBox.warning(self, "出库失败", msg)

    def on_receive_payment(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_by_id(quote_id)
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
            add_payment(
                quote_id=quote_id,
                customer_id=quote.get("customer_id"),
                pay_type="receivable",
                amount=data["amount"],
                pay_date=data["pay_date"],
                method=data["method"],
                remark=data["remark"],
            )
            QMessageBox.information(self, "成功", f"收款 ¥{data['amount']:.2f} 已记录！")
            add_operation_log("收款", "quotes", quote_id, f"金额={data['amount']:.2f}")
            self.refresh_records()

    def on_cancel_quote(self):
        row = self.record_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一条报价记录")
            return
        quote_id = int(self.record_table.item(row, 0).text())
        quote = get_quote_by_id(quote_id)
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
            update_quote_status(quote_id, "已取消")
            add_operation_log("取消订单", "quotes", quote_id, "取消订单")
            self.refresh_records()

    # -------------------------------------------------------
    # 导入 Word
    # -------------------------------------------------------
    def on_import_word(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择 Word 价格表", "", "Word 文档 (*.docx *.doc)"
        )
        if not filepath:
            return

        # 文件大小校验（最大 50 MB）
        file_size = os.path.getsize(filepath)
        if file_size > 50 * 1024 * 1024:
            QMessageBox.warning(self, "文件过大",
                f"文件大小 {file_size / (1024*1024):.1f} MB 超过限制（最大 50 MB）")
            return

        try:
            products = parse_word_pricelist(filepath)
        except Exception as e:
            QMessageBox.critical(self, "解析失败", f"无法解析 Word 文件:\n{str(e)}")
            return

        if not products:
            QMessageBox.warning(self, "提示", "未从文档中识别到任何机型")
            return

        # 预览确认
        preview = "\n".join([f"{p.get('series','?')} | {p.get('cpu','')} | {p.get('ram','')} | {p.get('storage','')} | {p.get('gpu','')}" for p in products[:20]])
        if len(products) > 20:
            preview += f"\n... 及其他 {len(products)-20} 条"

        reply = QMessageBox.question(
            self, "确认导入",
            f"识别到 {len(products)} 条机型，预览如下:\n\n{preview}\n\n是否导入？"
            "\n（已存在的机型将被跳过）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 导入
        imported = 0
        for p in products:
            series = p.get("series", "")
            if not series or series == "未识别":
                continue
            # 检查是否已存在（按全部字段去重）
            existing = search_products(series)
            skip = False
            for e in existing:
                if (e["series"] == series and
                    e.get("cpu", "") == p.get("cpu", "") and
                    e.get("ram", "") == p.get("ram", "") and
                    e.get("storage", "") == p.get("storage", "") and
                    e.get("gpu", "") == p.get("gpu", "") and
                    e.get("screen", "") == p.get("screen", "") and
                    e.get("note", "") == p.get("note", "")):
                    skip = True
                    break
            if not skip:
                add_product(
                    series=series,
                    cpu=p.get("cpu", ""),
                    ram=p.get("ram", ""),
                    storage=p.get("storage", ""),
                    gpu=p.get("gpu", ""),
                    screen=p.get("screen", ""),
                    note=p.get("note", ""),
                )
                imported += 1

        self.main.product_tab.refresh_product_list(self.main.search_edit.text().strip())
        QMessageBox.information(self, "导入完成", f"成功导入 {imported} 条新机型\n跳过 {len(products)-imported} 条已存在的记录")

        # ---- Skill 2: 价格异动哨兵 ----
        # 保存本次快照
        try:
            snapshot_id, count = save_snapshot(products)
        except Exception:
            snapshot_id = None

        # 和上一次快照做对比
        if snapshot_id:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                old_snapshot = get_latest_snapshot(before_date=today)
                # 如果今天之前没有更早的快照，再取最新的（可能是今天刚导入的前一次）
                if old_snapshot is None:
                    old_snapshot = get_latest_snapshot()
                    # 排除当前这次刚保存的
                    if old_snapshot and old_snapshot["snapshot_id"] == snapshot_id:
                        old_snapshot = None

                if old_snapshot:
                    diff = diff_snapshots(old_snapshot, products)
                    if diff["added"] or diff["removed"]:
                        self.main._show_diff_report(diff)
                else:
                    QMessageBox.information(
                        self, "首次快照",
                        f"已保存价格快照（{count} 条机型）。\n下次导入时将自动对比变动。"
                    )
            except Exception as e:
                pass  # 异动分析失败不影响主流程

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
        products = get_all_products()
        if not products:
            QMessageBox.warning(self, "提示", "还没有机型数据，请先导入 Word 价格表")
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
                {"series": "手动出库", "customer_name": "________", "quote_price": 0, "quote_quantity": len(valid)},
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
