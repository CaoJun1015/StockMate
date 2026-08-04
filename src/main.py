"""
主窗口及各个 UI 面板
"""

import sys
import os
import re
import traceback
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QComboBox, QTextEdit,
    QMessageBox, QFileDialog, QDialog, QFormLayout,
    QSpinBox, QDateEdit, QDialogButtonBox,
    QFrame, QHeaderView, QAbstractItemView, QCheckBox, QGroupBox,
    QGridLayout, QMenu, QMenuBar, QStatusBar,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QAction, QClipboard, QColor

# 确保能找到 src 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.models.database import (
    init_db, add_product, update_product, delete_product,
    search_products, get_all_products,
    add_batch, get_batches, update_batch_remaining, delete_batch, get_total_remaining,
    get_all_customers,
    add_supplier, search_suppliers, get_all_suppliers,
    add_quote, update_quote, delete_quote, search_quotes, export_quotes, get_quote_by_id,
    update_quote_status, add_payment, _add_payment_raw, get_payments, get_customer_balance,
    get_supplier_payable, get_customer_statement, deduct_batch_remaining,
    delete_supplier_cascade,
    get_payment_by_id, get_all_payments_with_details, update_payment, delete_payment,
    ship_quote, add_operation_log,
)
from src.ui.style import APP_STYLE
from src.ui.dialogs import (
    ShipmentDialog, PaymentDialog, PaymentEditDialog, StatementDialog,
    ProductEditDialog, BatchDialog, CustomerDialog, QuoteEditDialog,
    OperationLogDialog,
)
from src.ui.quote_panel import QuotePanel
from src.ui.product_tab import ProductTab
from src.ui.record_tab import RecordTab
from src.ui.customer_tab import CustomerTab
from src.ui.supplier_tab import SupplierTab
from src.ui.finance_tab import FinanceTab
from src.ui.utils import _validate_date, _global_excepthook
from src.models.migrations import DatabaseMigrationError
from src.models.connection import (
    DatabaseRestoreError,
    get_backup_dir,
    restore_database,
)
from src.services.reconciliation_service import ReconciliationService
from src.version import APP_DISPLAY_NAME
from src.utils.word_parser import parse_word_pricelist, preview_parse
from src.utils.image_gen import generate_quote_image, generate_single_quote_card, WATERMARK_TEXT
from src.utils.excel_export import export_quotes_to_excel
from src.utils.price_diff import save_snapshot, get_latest_snapshot, diff_snapshots, get_all_snapshots
from src.utils.follow_up import get_stale_quotes, format_reminder_text
from src.utils.monthly_report import get_monthly_report, format_report_text
from src.utils.shipment_flow import parse_sn_input, validate_sn, validate_sn_list, check_sn_duplicates, generate_shipment_receipt
from src.utils.quote_assist import get_quote_history, suggest_price, get_customer_price_history
from src.utils.remote_diagnose import search_diagnose, get_diagnose_tree, get_all_diagnose_keys, generate_diagnose_report


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(1100, 700)

        # 初始化数据库（含自动备份 + 完整性检查）
        init_ok, init_msg = init_db()
        self.backup_status = init_msg

        # 中心控件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # 搜索 + 工具栏
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("globalSearch")
        self.search_edit.setPlaceholderText("搜索机型（系列/CPU/关键字）...")
        self.search_edit.textChanged.connect(self.on_search)
        self.search_edit.setMinimumWidth(250)

        self.import_btn = QPushButton("导入 Word 价格表")
        self.import_btn.setObjectName("ghostBtn")
        self.import_btn.clicked.connect(self.on_import_word)
        self.broadcast_btn = QPushButton("群发图片")
        self.broadcast_btn.setObjectName("ghostBtn")
        self.broadcast_btn.clicked.connect(self.on_broadcast)
        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setObjectName("ghostBtn")
        self.export_btn.clicked.connect(self.on_export_excel)
        self.export_json_btn = QPushButton("JSON备份")
        self.export_json_btn.setObjectName("ghostBtn")
        self.export_json_btn.clicked.connect(self.on_export_json)
        self.import_json_btn = QPushButton("JSON导入")
        self.import_json_btn.setObjectName("ghostBtn")
        self.import_json_btn.clicked.connect(self.on_import_json)
        self.log_btn = QPushButton("操作日志")
        self.log_btn.setObjectName("ghostBtn")
        self.log_btn.clicked.connect(self.on_show_logs)
        self.statement_btn = QPushButton("对账单")
        self.statement_btn.setObjectName("ghostBtn")
        self.statement_btn.clicked.connect(self.on_statement)
        self.follow_up_btn = QPushButton("🔔 跟单提醒")
        self.follow_up_btn.setObjectName("ghostBtn")
        self.follow_up_btn.clicked.connect(self.on_follow_up)
        self.report_btn = QPushButton("📊 月度报告")
        self.report_btn.setObjectName("ghostBtn")
        self.report_btn.clicked.connect(self.on_monthly_report)
        self.diagnose_btn = QPushButton("🔧 远程诊断")
        self.diagnose_btn.setObjectName("ghostBtn")
        self.diagnose_btn.clicked.connect(self.on_remote_diagnose)
        self.price_diff_btn = QPushButton("价格异动")
        self.price_diff_btn.setObjectName("ghostBtn")
        self.price_diff_btn.clicked.connect(self.on_price_diff)
        self.quote_assist_btn = QPushButton("报价助手")
        self.quote_assist_btn.setObjectName("ghostBtn")
        self.quote_assist_btn.clicked.connect(self.on_quote_assist)
        self.shipment_flow_btn = QPushButton("出库一条龙")
        self.shipment_flow_btn.setObjectName("ghostBtn")
        self.shipment_flow_btn.clicked.connect(self.on_shipment_flow)

        top_bar.addWidget(QLabel("🔍"))
        top_bar.addWidget(self.search_edit)
        top_bar.addStretch()

        def _add_sep(layout):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setObjectName("toolbarSeparator")
            layout.addWidget(sep)

        top_bar.addWidget(self.follow_up_btn)
        top_bar.addWidget(self.report_btn)
        top_bar.addWidget(self.diagnose_btn)
        top_bar.addWidget(self.price_diff_btn)
        top_bar.addWidget(self.quote_assist_btn)
        top_bar.addWidget(self.shipment_flow_btn)
        _add_sep(top_bar)
        top_bar.addWidget(self.import_btn)
        top_bar.addWidget(self.broadcast_btn)
        top_bar.addWidget(self.export_btn)
        top_bar.addWidget(self.statement_btn)
        top_bar.addWidget(self.export_json_btn)
        top_bar.addWidget(self.import_json_btn)
        _add_sep(top_bar)
        top_bar.addWidget(self.log_btn)
        main_layout.addLayout(top_bar)

        # Tab 页面
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # === Tab 1: 机型管理 ===
        tab_products = QWidget()
        tab_layout = QHBoxLayout(tab_products)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：机型列表
        self.product_tab = ProductTab(self)
        splitter.addWidget(self.product_tab)

        # 右：报价面板
        self.quote_panel = QuotePanel()
        splitter.addWidget(self.quote_panel)
        splitter.setSizes([600, 450])

        tab_layout.addWidget(splitter)
        self.tabs.addTab(tab_products, "📋 机型管理")

        # === Tab 2: 客户管理 ===
        tab_customers = self._build_customer_tab()
        self.tabs.addTab(tab_customers, "👤 客户管理")

        # === Tab 3: 上游管理 ===
        tab_suppliers = self._build_supplier_tab()
        self.tabs.addTab(tab_suppliers, "🏭 上游管理")

        # === Tab 4: 报价记录 ===
        tab_records = self._build_records_tab()
        self.tabs.addTab(tab_records, "📊 报价记录")

        # === Tab 5: 账款管理 ===
        self.finance_tab = FinanceTab(self)
        self.finance_tab.data_changed.connect(self.refresh_records)
        self.tabs.addTab(self.finance_tab, "💰 账款管理")
        # One-release UI compatibility for external scripts/tests.
        for name in (
            "receivable_table", "payable_table", "payment_flow_table",
            "refresh_receivable_btn", "refresh_payable_btn", "refresh_flow_btn",
        ):
            setattr(self, name, getattr(self.finance_tab, name))

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel(f"就绪 | {self.backup_status}")
        self.status_bar.addWidget(self.status_label)
        self._build_data_safety_menu()

        # 加载数据
        self.product_tab.refresh_product_list()
        self.customer_tab.refresh_customer_list()
        self.supplier_tab.refresh_supplier_list()
        self.refresh_records()
        self.finance_tab.refresh()

        # 启用所有表格的列头排序
        for table in self.findChildren(QTableWidget):
            table.setSortingEnabled(True)

    def _build_data_safety_menu(self):
        data_menu = self.menuBar().addMenu("数据安全")

        reconcile_action = QAction("运行自动对账", self)
        reconcile_action.triggered.connect(self.on_reconcile_database)
        data_menu.addAction(reconcile_action)

        restore_action = QAction("从 SQLite 备份恢复…", self)
        restore_action.triggered.connect(self.on_restore_database)
        data_menu.addAction(restore_action)

    def on_reconcile_database(self):
        try:
            report = ReconciliationService().run()
        except Exception as exc:
            QMessageBox.critical(self, "对账失败", f"无法完成自动对账：\n{exc}")
            return
        self.status_label.setText(
            "自动对账通过" if report.is_clean else f"自动对账发现 {len(report.issues)} 个问题"
        )
        if report.is_clean:
            QMessageBox.information(self, "自动对账", report.format_text())
        else:
            QMessageBox.warning(self, "自动对账", report.format_text())

    def on_restore_database(self):
        backup_dir = get_backup_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 SQLite 数据库备份",
            str(backup_dir),
            "SQLite 数据库 (*.db);;所有文件 (*.*)",
        )
        if not file_path:
            return
        reply = QMessageBox.question(
            self,
            "确认恢复数据库",
            (
                f"将从以下备份恢复：\n{file_path}\n\n"
                "恢复前会自动备份当前数据库。恢复成功后程序将退出，"
                "需要重新启动才能继续使用。\n\n确定继续？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = restore_database(file_path)
        except DatabaseRestoreError as exc:
            safety_path = (
                str(exc.safety_backup.path) if exc.safety_backup else "未生成"
            )
            QMessageBox.critical(
                self,
                "恢复失败",
                f"{exc}\n\n恢复前安全备份：{safety_path}",
            )
            return

        safety_path = (
            str(result.safety_backup.path) if result.safety_backup else "原数据库不存在"
        )
        QMessageBox.information(
            self,
            "恢复成功",
            (
                f"数据库已从备份恢复。\n\n"
                f"恢复源：{result.restored_from}\n"
                f"恢复前安全备份：{safety_path}\n\n"
                "程序现在将退出，请重新启动调货助手。"
            ),
        )
        QApplication.instance().quit()

    def on_confirm_quote(self):
        self.record_tab.on_confirm_quote()

    def on_ship_quote(self):
        self.record_tab.on_ship_quote()

    def on_receive_payment(self):
        self.record_tab.on_receive_payment()

    def on_cancel_quote(self):
        self.record_tab.on_cancel_quote()

    def on_statement(self):
        dlg = StatementDialog(self)
        dlg.exec()

    # -------------------------------------------------------
    # Tab 构建
    # -------------------------------------------------------
    def _build_customer_tab(self):
        self.customer_tab = CustomerTab(self)
        return self.customer_tab

    def _build_records_tab(self):
        self.record_tab = RecordTab(self)
        return self.record_tab

    def on_search(self, text):
        self.product_tab.on_search(text)

    # -------------------------------------------------------
    # 上游管理
    # -------------------------------------------------------
    def _build_supplier_tab(self):
        self.supplier_tab = SupplierTab(self)
        return self.supplier_tab

    # -------------------------------------------------------
    # 报价记录（委托到 RecordTab）
    # -------------------------------------------------------
    def refresh_records(self):
        self.record_tab.refresh_records()

    def on_edit_quote(self):
        self.record_tab.on_edit_quote()

    def on_delete_quote(self):
        self.record_tab.on_delete_quote()

    # -------------------------------------------------------
    # 导入 Word（委托到 RecordTab）
    # -------------------------------------------------------
    def on_import_word(self):
        self.record_tab.on_import_word()

    # -------------------------------------------------------
    # Skill 3: 智能跟单提醒（委托到 RecordTab）
    # -------------------------------------------------------
    def on_follow_up(self):
        self.record_tab.on_follow_up()

    # -------------------------------------------------------
    # Skill 4: 月度经营报告（委托到 RecordTab）
    # -------------------------------------------------------
    def on_monthly_report(self):
        self.record_tab.on_monthly_report()

    # -------------------------------------------------------
    # Skill 6: 远程诊断助手
    # -------------------------------------------------------
    def on_remote_diagnose(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("🔧 远程诊断助手")
        dlg.setMinimumSize(650, 550)
        layout = QVBoxLayout(dlg)

        # 搜索栏
        search_row = QHBoxLayout()
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("输入症状关键词（如：蓝屏、开不了机、WiFi断连、风扇噪音大）")
        search_btn = QPushButton("搜索")
        search_row.addWidget(search_edit)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        # 快捷按钮
        quick_row = QHBoxLayout()
        for key_info in get_all_diagnose_keys():
            btn = QPushButton(key_info["title"])
            btn.setObjectName("diagnoseOptionBtn")
            btn.clicked.connect(lambda checked, k=key_info["key"]: self._show_diagnose_steps(dlg, k))
            quick_row.addWidget(btn)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        # 结果区域
        result_text = QTextEdit()
        result_text.setReadOnly(True)
        result_text.setPlaceholderText("选择一个故障类型开始排查...")
        result_text.setObjectName("reportText")
        layout.addWidget(result_text)

        def do_search():
            kw = search_edit.text().strip()
            if not kw:
                return
            results = search_diagnose(kw)
            if results:
                lines = [f"找到 {len(results)} 个匹配项：\n"]
                for r in results:
                    lines.append(f"• {r['title']}（{r['match_type']}）")
                lines.append("\n点击上方快捷按钮开始排查")
                result_text.setPlainText("\n".join(lines))
            else:
                result_text.setPlainText(f"未找到与「{kw}」相关的诊断流程。\n\n当前支持的故障类型：\n" +
                                         "\n".join(f"• {k['title']}" for k in get_all_diagnose_keys()))

        search_btn.clicked.connect(do_search)
        search_edit.returnPressed.connect(do_search)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def _show_diagnose_steps(self, parent_dlg, key):
        tree = get_diagnose_tree(key)
        if not tree:
            return

        dlg = QDialog(parent_dlg)
        dlg.setWindowTitle(f"🔧 {tree['title']}")
        dlg.setMinimumSize(600, 450)
        layout = QVBoxLayout(dlg)

        title = QLabel(tree["title"])
        title.setObjectName("sectionTitleBlue")
        layout.addWidget(title)

        result_text = QTextEdit()
        result_text.setReadOnly(True)
        result_text.setObjectName("reportText")
        layout.addWidget(result_text)

        selected_path = []

        def show_step(step_idx):
            if step_idx >= len(tree["steps"]):
                return
            step = tree["steps"][step_idx]
            result_text.append(f"\n❓ {step['q']}\n")

            # 清除旧按钮
            for i in reversed(range(layout.count())):
                item = layout.itemAt(i)
                if item.widget() and isinstance(item.widget(), QPushButton) and item.widget().text() not in ("关闭",):
                    item.widget().deleteLater()

            for opt_name, opt_data in step["options"].items():
                btn = QPushButton(f"→ {opt_name}")
                btn.setObjectName("diagnoseOptionBtn")
                btn.clicked.connect(lambda checked, on=opt_name, od=opt_data, si=step_idx: handle_option(on, od, si))
                layout.insertWidget(layout.count() - 1, btn)

        def handle_option(opt_name, opt_data, step_idx):
            selected_path.append(opt_name)
            result_text.append(f"  ✅ {opt_name}")

            if "action" in opt_data:
                result_text.append(f"\n📋 处理方案:\n{opt_data['action']}\n")

                # 生成报告按钮
                report_btn = QPushButton("📄 生成诊断报告")
                report_btn.setObjectName("successBtn")
                report_btn.clicked.connect(lambda: self._save_diagnose_report(key, selected_path))
                layout.insertWidget(layout.count() - 1, report_btn)

            elif "next" in opt_data:
                show_step(opt_data["next"])

        show_step(0)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()

    def _save_diagnose_report(self, key, selected_path):
        report = generate_diagnose_report(key, selected_path)
        # 复制到剪贴板
        clipboard = QApplication.clipboard()
        clipboard.setText(report)
        QMessageBox.information(self, "诊断报告", f"报告已复制到剪贴板！\n\n{report}")

    # -------------------------------------------------------
    # Skill: 价格异动哨兵
    # -------------------------------------------------------
    def on_price_diff(self):
        """价格异动哨兵 - 在导入Word时自动对比"""
        snapshots = get_all_snapshots()
        if not snapshots:
            QMessageBox.information(self, "价格异动", "暂无历史价格快照。\n导入 Word 价格表时会自动保存快照。")
            return

        latest = get_latest_snapshot()
        if not latest:
            QMessageBox.information(self, "价格异动", "无法获取最新快照")
            return

        # 显示快照列表
        items_text = "\n".join([f"  {s['import_date']} | {s['item_count']} 条机型" for s in snapshots[:10]])
        QMessageBox.information(self, "价格快照历史", f"已有 {len(snapshots)} 个快照：\n\n{items_text}\n\n下次导入 Word 价格表时将自动对比异动。")

    # -------------------------------------------------------
    # Skill: 报价决策助手
    # -------------------------------------------------------
    def on_quote_assist(self):
        """报价决策助手"""
        dlg = QDialog(self)
        dlg.setWindowTitle("报价决策助手")
        dlg.setMinimumWidth(450)
        layout = QFormLayout(dlg)

        series_edit = QLineEdit()
        series_edit.setPlaceholderText("输入系列名称（如 Y7000P）")
        cpu_edit = QLineEdit()
        cpu_edit.setPlaceholderText("CPU（选填）")
        price_spin = QSpinBox()
        price_spin.setRange(0, 999999)
        price_spin.setPrefix("¥ ")
        customer_edit = QLineEdit()
        customer_edit.setPlaceholderText("客户名称（选填）")

        layout.addRow("系列:", series_edit)
        layout.addRow("CPU:", cpu_edit)
        layout.addRow("进货价:", price_spin)
        layout.addRow("客户:", customer_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec():
            series = series_edit.text().strip()
            if not series:
                QMessageBox.warning(self, "提示", "请输入系列名称")
                return
            suggestion = suggest_price(
                series=series,
                cpu=cpu_edit.text().strip(),
                purchase_price=price_spin.value(),
                customer_name=customer_edit.text().strip(),
            )

            conf_text = {"high": "高", "medium": "中", "low": "低"}[suggestion["confidence"]]
            text = (
                f"建议报价范围: ¥{suggestion['suggested_min']:,.0f} ~ ¥{suggestion['suggested_max']:,.0f}\n"
                f"建议中间价: ¥{suggestion['suggested_mid']:,.0f}\n"
                f"利润率: {suggestion['margin_at_mid']:.1f}%\n"
                f"置信度: {conf_text}\n\n"
                f"依据: {suggestion['basis']}"
            )
            if suggestion.get("history"):
                h = suggestion["history"]
                text += f"\n\n历史报价: {h['total_quotes']} 条\n区间: ¥{h['min_price']:,.0f} ~ ¥{h['max_price']:,.0f}\n均价: ¥{h['avg_price']:,.0f}"

            QMessageBox.information(self, "报价建议", text)

    # -------------------------------------------------------
    # Skill: 出库一条龙
    # -------------------------------------------------------
    def on_shipment_flow(self):
        self.record_tab.on_shipment_flow()

    # -------------------------------------------------------
    # 价格异动报告（Skill 2）
    # -------------------------------------------------------
    def _show_diff_report(self, diff):
        """弹出价格异动报告对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("📊 价格异动报告")
        dlg.setMinimumSize(700, 500)
        layout = QVBoxLayout(dlg)

        # 标题
        title = QLabel(f"📊 价格异动报告（{diff['old_date']} → {diff['new_date']}）")
        title.setObjectName("sectionTitleBlue")
        layout.addWidget(title)

        # 摘要
        summary = QLabel(diff["summary"])
        summary.setObjectName("summaryLabel")
        layout.addWidget(summary)

        # Tab 切换
        tabs = QTabWidget()

        # === 新增 ===
        if diff["added"]:
            add_tab = QWidget()
            add_layout = QVBoxLayout(add_tab)
            add_table = QTableWidget()
            add_table.setAlternatingRowColors(True)
            add_table.setColumnCount(6)
            add_table.setHorizontalHeaderLabels(["系列", "CPU", "内存", "硬盘", "显卡", "备注"])
            add_table.setRowCount(len(diff["added"]))
            for i, item in enumerate(diff["added"]):
                add_table.setItem(i, 0, QTableWidgetItem(item.get("series", "")))
                add_table.setItem(i, 1, QTableWidgetItem(item.get("cpu", "")))
                add_table.setItem(i, 2, QTableWidgetItem(item.get("ram", "")))
                add_table.setItem(i, 3, QTableWidgetItem(item.get("storage", "")))
                add_table.setItem(i, 4, QTableWidgetItem(item.get("gpu", "")))
                add_table.setItem(i, 5, QTableWidgetItem(item.get("note", "")))
            add_table.horizontalHeader().setStretchLastSection(True)
            add_table.resizeColumnsToContents()
            add_layout.addWidget(add_table)
            tabs.addTab(add_tab, f"🔵 新增 ({len(diff['added'])})")

        # === 下架 ===
        if diff["removed"]:
            rm_tab = QWidget()
            rm_layout = QVBoxLayout(rm_tab)
            rm_label = QLabel("⚠️ 以下机型在新价格表中已下架，请检查是否有库存需要尽快出货：")
            rm_label.setObjectName("dangerSummaryLabel")
            rm_layout.addWidget(rm_label)
            rm_table = QTableWidget()
            rm_table.setAlternatingRowColors(True)
            rm_table.setColumnCount(6)
            rm_table.setHorizontalHeaderLabels(["系列", "CPU", "内存", "硬盘", "显卡", "备注"])
            rm_table.setRowCount(len(diff["removed"]))
            for i, item in enumerate(diff["removed"]):
                rm_table.setItem(i, 0, QTableWidgetItem(item.get("series", "")))
                rm_table.setItem(i, 1, QTableWidgetItem(item.get("cpu", "")))
                rm_table.setItem(i, 2, QTableWidgetItem(item.get("ram", "")))
                rm_table.setItem(i, 3, QTableWidgetItem(item.get("storage", "")))
                rm_table.setItem(i, 4, QTableWidgetItem(item.get("gpu", "")))
                rm_table.setItem(i, 5, QTableWidgetItem(item.get("note", "")))
            rm_table.horizontalHeader().setStretchLastSection(True)
            rm_table.resizeColumnsToContents()
            rm_layout.addWidget(rm_table)
            tabs.addTab(rm_tab, f"⚠️ 下架 ({len(diff['removed'])})")

        layout.addWidget(tabs)

        # 底部按钮
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    # -------------------------------------------------------
    # 群发图片
    # -------------------------------------------------------
    def on_broadcast(self):
        self.record_tab.on_broadcast()

    # -------------------------------------------------------
    # 群发图片（委托到 RecordTab）
    # -------------------------------------------------------
    def on_export_records_excel(self):
        self.record_tab.on_export_records_excel()

    def on_export_excel(self):
        self.record_tab.on_export_excel()

    def on_export_json(self):
        """导出全量数据为 JSON 格式"""
        try:
            from src.utils.json_export import export_all_to_json
            from src.models import database as db
            
            output_path = export_all_to_json(db)
            QMessageBox.information(
                self, "导出成功",
                f"数据已成功导出为 JSON 格式！\n\n文件位置: {output_path}\n\n可用于数据备份或迁移到其他电脑。"
            )
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出时发生错误:\n{str(e)}")

    def on_import_json(self):
        """从 JSON 文件导入数据"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 JSON 备份文件", "",
            "JSON 文件 (*.json);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 文件大小校验（最大 50 MB）
        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            QMessageBox.warning(self, "文件过大",
                f"文件大小 {file_size / (1024*1024):.1f} MB 超过限制（最大 50 MB）")
            return
        
        from src.utils.json_export import validate_json_file, import_from_json
        from src.models import database as db
        
        valid, message, stats = validate_json_file(file_path)
        if not valid:
            QMessageBox.warning(self, "文件无效", message)
            return
        
        reply = QMessageBox.question(
            self, "确认导入",
            f"即将导入以下数据:\n\n"
            f"• 机型: {stats.get('products', 0)} 条\n"
            f"• 批次: {stats.get('batches', 0)} 条\n"
            f"• 客户: {stats.get('customers', 0)} 条\n"
            f"• 报价: {stats.get('quotes', 0)} 条\n\n"
            f"是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success, message, import_stats = import_from_json(file_path, db)
            
            if success:
                self.product_tab.refresh_product_list()
                self.customer_tab.refresh_customer_list()
                self.supplier_tab.refresh_supplier_list()
                self.refresh_records()
                
                QMessageBox.information(
                    self, "导入成功",
                    f"数据导入完成！\n\n"
                    f"• 机型: {import_stats.get('products', 0)} 条\n"
                    f"• 批次: {import_stats.get('batches', 0)} 条\n"
                    f"• 客户: {import_stats.get('customers', 0)} 条\n"
                    f"• 报价: {import_stats.get('quotes', 0)} 条\n\n"
                    f"注意: 如果导入的数据与现有数据重复，可能会产生重复记录。"
                )
            else:
                QMessageBox.critical(self, "导入失败", message)

    def on_show_logs(self):
        """显示操作日志"""
        dlg = OperationLogDialog(self)
        dlg.exec()


def main():
    app = QApplication(sys.argv)
    sys.excepthook = _global_excepthook
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    try:
        window = MainWindow()
    except DatabaseMigrationError as exc:
        backup_path = exc.backup.path if exc.backup else "未生成"
        QMessageBox.critical(
            None,
            "数据库升级失败",
            f"{exc}\n\n程序已停止启动，数据未被部分升级。\n迁移前备份: {backup_path}",
        )
        return
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
