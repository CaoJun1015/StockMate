"""
主窗口及各个 UI 面板
"""

import sys
import os
import re
import json
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

from src.services.database_service import (
    initialize_database, prepare_database_restore, apply_database_restore,
)
from src.ui.style import APP_STYLE
from src.ui.display_labels import OBJECT_LABELS
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
from src.ui.background_tasks import (
    has_background_tasks, write_text_atomically,
)
from src.models.migrations import DatabaseMigrationError
from src.models.connection import (
    DatabaseRestoreError,
    create_backup,
    get_backup_dir,
    get_app_path,
    get_data_dir,
    get_database_path,
)
from src.models.schema import SCHEMA_VERSION
from src.services.reconciliation_service import ReconciliationService
from src.services.historical_repair_service import HistoricalRepairService
from src.version import APP_DISPLAY_NAME, APP_VERSION
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

        # 搜索 + 工具栏。保留全部入口，但分成两行避免窄窗口挤压业务页。
        toolbar = QVBoxLayout()
        toolbar.setSpacing(6)
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
        self.import_json_btn = QPushButton("JSON 备份恢复")
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

        search_label = QLabel("🔍")
        search_label.setToolTip("搜索机型、CPU 或关键字")
        top_bar.addWidget(search_label)
        top_bar.addWidget(self.search_edit)
        top_bar.addStretch()

        def _add_sep(layout):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setObjectName("toolbarSeparator")
            layout.addWidget(sep)

        _add_sep(top_bar)
        for button, tip in (
            (self.follow_up_btn, "查看超期未成交或未收款订单"),
            (self.report_btn, "查看月度销售、成本和毛利"),
            (self.diagnose_btn, "按症状生成远程诊断步骤"),
            (self.shipment_flow_btn, "连续扫描 SN 并生成出库确认单"),
        ):
            button.setToolTip(tip)
            button.setStatusTip(tip)
            top_bar.addWidget(button)
        toolbar.addLayout(top_bar)

        data_bar = QHBoxLayout()
        for index, (button, tip) in enumerate((
            (self.broadcast_btn, "批量生成报价图片"),
            (self.export_btn, "导出报价记录 Excel"),
            (self.statement_btn, "生成客户对账单"),
            (self.export_json_btn, "导出完整 JSON 备份"),
            (self.import_json_btn, "完整替换恢复 JSON 备份"),
            (self.log_btn, "查看操作日志"),
        )):
            button.setToolTip(tip)
            button.setStatusTip(tip)
            data_bar.addWidget(button)
            if index in (1, 4):
                _add_sep(data_bar)
        data_bar.insertSpacing(0, 4)
        data_bar.addStretch()
        toolbar.addLayout(data_bar)
        main_layout.addLayout(toolbar)

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
        self._build_about_menu()

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

        historical_repair_action = QAction("核对历史异常并修复…", self)
        historical_repair_action.triggered.connect(self.on_repair_historical_anomalies)
        data_menu.addAction(historical_repair_action)

        export_historical_action = QAction("导出历史异常清单…", self)
        export_historical_action.triggered.connect(self.on_export_historical_anomalies)
        data_menu.addAction(export_historical_action)

        data_menu.addSeparator()
        backup_action = QAction("立即创建 SQLite 备份", self)
        backup_action.triggered.connect(self.on_create_database_backup)
        data_menu.addAction(backup_action)

        restore_action = QAction("SQLite 备份恢复…", self)
        restore_action.triggered.connect(self.on_restore_database)
        data_menu.addAction(restore_action)

    def _build_about_menu(self):
        help_menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于 StockMate", self)
        about_action.triggered.connect(self.on_about)
        help_menu.addAction(about_action)

    def on_about(self):
        metadata = {}
        metadata_path = get_app_path() / "version.json"
        try:
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        if not getattr(sys, "frozen", False):
            build_status = "开发版"
        elif metadata.get("candidate_status") == "local-validation-only":
            build_status = "本地验证版"
        elif metadata:
            build_status = "候选版"
        else:
            build_status = "未记录"
        source_commit = metadata.get("source_commit", "未记录")
        QMessageBox.information(
            self,
            "关于 StockMate",
            f"应用名称：{APP_DISPLAY_NAME}\n"
            f"应用版本：{APP_VERSION}\n"
            f"schema 版本：{SCHEMA_VERSION}\n"
            f"数据目录：{get_data_dir()}\n"
            f"当前构建状态：{build_status}\n"
            f"构建提交：{source_commit}",
        )

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
            output_path = write_text_atomically(file_path, report.format_text())
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出成功", f"对账报告已保存：\n{output_path}")

    def _historical_repair_plan(self):
        return HistoricalRepairService(get_database_path()).audit()

    def on_export_historical_anomalies(self):
        try:
            plan = self._historical_repair_plan()
        except Exception as exc:
            QMessageBox.critical(self, "核对失败", str(exc))
            return
        default_path = get_backup_dir() / (
            f"historical_anomalies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出历史异常清单", str(default_path), "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not file_path:
            return
        try:
            output = HistoricalRepairService.export(plan, file_path)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出成功", f"历史异常清单已保存：\n{output}")

    def on_repair_historical_anomalies(self):
        try:
            plan = self._historical_repair_plan()
        except Exception as exc:
            QMessageBox.critical(self, "核对失败", str(exc))
            return
        preview = plan.format_text()
        if not plan.repairable:
            QMessageBox.warning(self, "历史异常核对（只读）", preview + "\n\n没有证据充分的自动修复项，请导出后人工核对。")
            return
        answer = QMessageBox.question(
            self,
            "确认历史修复",
            preview + "\n\n仅会更新上述可确定的报价缓存字段；将先创建并验证安全备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = HistoricalRepairService(get_database_path()).apply(
                plan, reason="用户确认：历史异常修复"
            )
        except Exception as exc:
            QMessageBox.critical(self, "历史修复失败", str(exc))
            return
        self.refresh_records()
        self.finance_tab.refresh()
        remaining = len(result.remaining_report.issues)
        QMessageBox.information(
            self,
            "历史修复完成",
            f"已修复 {len(result.applied)} 项；安全备份：{result.backup.path}\n"
            f"SHA-256：{result.backup.sha256}\n未解决异常：{remaining} 项。",
        )

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
        self._restore_backup(file_path, "sqlite")

    def _restore_backup(self, file_path, backup_format):
        # Disable all business controls throughout preflight, confirmation and apply.
        central = self.centralWidget()
        central.setEnabled(False)
        self.menuBar().setEnabled(False)
        try:
            with prepare_database_restore(file_path, backup_format=backup_format) as prepared:
                counts = prepared.report.metrics.get("table_counts", {})
                summary = "、".join(f"{OBJECT_LABELS.get(name, name)}: {count}" for name, count in counts.items())
                reply = QMessageBox.question(
                    self, "确认完整替换恢复",
                    f"备份已通过升级、完整性与业务对账。\n{file_path}\n{summary}\n\n"
                    "将完整替换当前数据库，不会合并记录。\n"
                    "恢复前自动创建安全备份，成功后退出程序。确定继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                result = apply_database_restore(prepared)
        except DatabaseRestoreError as exc:
            safety = str(exc.safety_backup.path) if exc.safety_backup else "未生成（替换前预检失败时无需备份）"
            QMessageBox.critical(self, "恢复失败", f"{exc}\n\n安全备份：{safety}")
            return
        finally:
            central.setEnabled(True)
            self.menuBar().setEnabled(True)
        safety = str(result.safety_backup.path) if result.safety_backup else "原数据库不存在"
        QMessageBox.information(self, "恢复成功", f"已完整恢复备份。\n安全备份：{safety}\n程序将退出，请重新启动。")
        QApplication.instance().quit()

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
    def on_export_excel(self):
        self.record_tab.on_export_excel()

    def on_export_json(self):
        """导出全量数据为 JSON 格式"""
        from src.utils.json_export import export_all_to_json
        try:
            output_path = export_all_to_json()
            QMessageBox.information(self, "导出成功", f"数据已成功导出为 JSON 格式！\n\n文件位置: {output_path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"导出时发生错误:\n{exc}")

    def closeEvent(self, event):
        if has_background_tasks(self):
            QMessageBox.information(self, "任务进行中", "读取或导出正在完成；为保护输出文件，请完成后再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def on_import_json(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 JSON 备份（完整替换恢复）", "", "JSON 文件 (*.json)"
        )
        if file_path:
            self._restore_backup(file_path, "json")

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
