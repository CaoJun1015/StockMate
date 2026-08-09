"""
主窗口及各个 UI 面板
"""

import sys
import os
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QComboBox, QTextEdit,
    QMessageBox, QFileDialog, QDialog,
    QDateEdit, QDialogButtonBox,
    QFrame, QHeaderView, QAbstractItemView, QCheckBox, QGroupBox,
    QGridLayout, QMenu, QMenuBar, QStatusBar,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QAction, QClipboard, QColor

# 确保能找到 src 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.services.database_service import initialize_database
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
    create_backup,
    get_backup_dir,
    get_database_path,
    restore_database,
)
from src.services.reconciliation_service import ReconciliationService
from src.version import APP_DISPLAY_NAME
from src.utils.image_gen import generate_quote_image, generate_single_quote_card, WATERMARK_TEXT
from src.utils.excel_export import export_quotes_to_excel
from src.utils.follow_up import get_stale_quotes, format_reminder_text
from src.utils.monthly_report import get_monthly_report, format_report_text
from src.utils.shipment_flow import parse_sn_input, validate_sn, validate_sn_list, check_sn_duplicates, generate_shipment_receipt
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
        init_ok, init_msg = initialize_database()
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
        top_bar.addWidget(self.shipment_flow_btn)
        _add_sep(top_bar)
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
        self.tabs.addTab(self.finance_tab, "💰 财务记账")
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

        export_reconcile_action = QAction("导出对账报告…", self)
        export_reconcile_action.triggered.connect(self.on_export_reconciliation_report)
        data_menu.addAction(export_reconcile_action)

        data_menu.addSeparator()
        backup_action = QAction("立即创建 SQLite 备份", self)
        backup_action.triggered.connect(self.on_create_database_backup)
        data_menu.addAction(backup_action)

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

    def on_export_reconciliation_report(self):
        try:
            report = ReconciliationService().run()
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"无法生成自动对账报告：\n{exc}")
            return
        default_path = get_backup_dir() / (
            f"reconciliation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出自动对账报告",
            str(default_path),
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not file_path:
            return
        try:
            output_path = Path(file_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report.format_text(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出成功", f"对账报告已保存：\n{output_path}")

    def on_create_database_backup(self):
        try:
            backup = create_backup(get_database_path())
        except Exception as exc:
            QMessageBox.critical(self, "备份失败", str(exc))
            return
        if backup is None:
            QMessageBox.warning(self, "备份失败", "当前数据库文件不存在")
            return
        self.status_label.setText(f"备份成功：{backup.path.name}")
        QMessageBox.information(
            self,
            "备份成功",
            f"文件：{backup.path}\nSHA-256：{backup.sha256}",
        )

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
    # Skill: 出库一条龙
    # -------------------------------------------------------
    def on_shipment_flow(self):
        self.record_tab.on_shipment_flow()

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

            output_path = export_all_to_json()
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
            success, message, import_stats = import_from_json(file_path)
            
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
