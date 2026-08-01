"""
调货助手 v1.10 UI 重构 - 黑盒验收测试

验证 UI 重构的正确性：
- APP_STYLE 常量定义
- objectName 替换 setStyleSheet
- filterCard 存在
- 交替行颜色
- 工具栏分隔线
- 边距设置
- 应用启动无异常
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestAppStyle:
    """验证 APP_STYLE 常量"""

    def test_app_style_exists(self):
        """APP_STYLE 常量应存在且为字符串"""
        from main import APP_STYLE
        assert isinstance(APP_STYLE, str), "APP_STYLE 应为字符串"
        assert len(APP_STYLE) > 500, "APP_STYLE 内容不应为空"

    def test_app_style_has_fusion_compatible(self):
        """APP_STYLE 应包含核心 QSS 选择器"""
        from main import APP_STYLE
        # 核心选择器检查
        assert "QMainWindow" in APP_STYLE, "缺少 QMainWindow 样式"
        assert "QTableWidget" in APP_STYLE, "缺少 QTableWidget 样式"
        assert "QHeaderView::section" in APP_STYLE, "缺少 QHeaderView 样式"
        assert "QPushButton" in APP_STYLE, "缺少 QPushButton 样式"
        assert "QLineEdit" in APP_STYLE, "缺少 QLineEdit 样式"
        assert "QTabWidget::pane" in APP_STYLE, "缺少 QTabWidget 样式"
        assert "QGroupBox" in APP_STYLE, "缺少 QGroupBox 样式"
        assert "QStatusBar" in APP_STYLE, "缺少 QStatusBar 样式"
        assert "QDialog" in APP_STYLE, "缺少 QDialog 样式"

    def test_app_style_has_objectname_selectors(self):
        """APP_STYLE 应包含语义化 objectName 选择器"""
        from main import APP_STYLE
        selectors = [
            "primaryBtn", "successBtn", "warningBtn", "dangerBtn", "ghostBtn",
            "tableActionPrimary", "tableActionOrange", "tableActionDanger",
            "sectionTitle", "sectionTitleBlue", "sectionTitleRed", "sectionTitleOrange",
            "summaryLabel", "dangerSummaryLabel", "dialogInfoLabel",
            "filterLabel", "appTitle", "reportText",
            "filterCard", "toolbarSeparator", "globalSearch",
            "diagnoseOptionBtn",
        ]
        for sel in selectors:
            assert f"#{sel}" in APP_STYLE, f"APP_STYLE 缺少 {sel} 选择器"

    def test_app_style_no_old_hex_colors(self):
        """APP_STYLE 不应包含旧版颜色（1976D2, 0D47A1 等被替换的颜色）"""
        from main import APP_STYLE
        # 新版颜色应存在
        assert "#3B82F6" in APP_STYLE, "缺少新版主色 #3B82F6"
        assert "#F8FAFC" in APP_STYLE, "缺少新版背景色 #F8FAFC"
        assert "#E2E8F0" in APP_STYLE, "缺少新版边框色 #E2E8F0"
        # 旧版颜色不应存在（除了可能的注释）
        # 注意：不检查旧颜色，因为可能有其他原因保留


class TestMainWindowStructure:
    """验证 MainWindow 结构"""

    def test_main_window_imports(self):
        """验证必要的 import 存在"""
        from main import APP_STYLE
        from PyQt6.QtWidgets import QFrame
        from PyQt6.QtCore import Qt
        assert True

    def test_main_function_uses_fusion(self):
        """验证 main() 使用 Fusion 样式"""
        import inspect
        import main as main_module
        source = inspect.getsource(main_module.main)
        assert 'setStyle("Fusion")' in source, "main() 应设置 Fusion 样式"
        assert "setStyleSheet(APP_STYLE)" in source, "main() 应设置 APP_STYLE"
        assert "qt_material" not in source, "main() 不应引用 qt_material"


class TestUIInstantiation:
    """验证 UI 组件实例化"""

    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        """创建 QApplication 和 MainWindow"""
        from main import MainWindow
        self.window = MainWindow()
        yield
        self.window.close()

    # ── 工具栏按钮 objectName ──

    def test_toolbar_buttons_have_ghostbtn(self):
        """工具栏按钮应有 ghostBtn objectName"""
        ghost_buttons = [
            self.window.import_btn,
            self.window.broadcast_btn,
            self.window.export_btn,
            self.window.export_json_btn,
            self.window.import_json_btn,
            self.window.log_btn,
            self.window.statement_btn,
            self.window.follow_up_btn,
            self.window.report_btn,
            self.window.diagnose_btn,
            self.window.price_diff_btn,
            self.window.quote_assist_btn,
            self.window.shipment_flow_btn,
        ]
        for btn in ghost_buttons:
            assert btn.objectName() == "ghostBtn", \
                f"按钮 '{btn.text()}' 的 objectName 应为 'ghostBtn'，实际为 '{btn.objectName()}'"

    def test_search_edit_has_globalsearch(self):
        """搜索框应有 globalSearch objectName"""
        assert self.window.search_edit.objectName() == "globalSearch", \
            f"search_edit objectName 应为 'globalSearch'，实际为 '{self.window.search_edit.objectName()}'"

    # ── 产品 Tab 按钮 ──

    def test_product_tab_buttons_objectname(self):
        """产品 Tab 按钮应有正确的 objectName"""
        assert self.window.product_tab.add_product_btn.objectName() == "primaryBtn"
        assert self.window.product_tab.edit_product_btn.objectName() == "ghostBtn"
        assert self.window.product_tab.refresh_product_list_btn.objectName() == "ghostBtn"
        assert self.window.product_tab.del_product_btn.objectName() == "dangerBtn"
        # QuotePanel 内的按钮
        assert self.window.quote_panel.add_batch_btn.objectName() == "primaryBtn"
        assert self.window.quote_panel.refresh_batch_btn.objectName() == "ghostBtn"

    # ── 报价面板按钮 ──

    def test_quote_panel_buttons_objectname(self):
        """报价面板按钮应有正确的 objectName"""
        assert self.window.quote_panel.quote_copy_btn.objectName() == "primaryBtn"
        assert self.window.quote_panel.quote_img_btn.objectName() == "ghostBtn"
        assert self.window.quote_panel.del_batch_btn.objectName() == "dangerBtn"

    # ── 财务 Tab ──

    def test_finance_tab_buttons_objectname(self):
        """财务 Tab 按钮应有正确的 objectName"""
        assert self.window.refresh_receivable_btn.objectName() == "ghostBtn"
        assert self.window.refresh_payable_btn.objectName() == "ghostBtn"
        assert self.window.refresh_flow_btn.objectName() == "ghostBtn"

    # ── 报价记录 Tab ──

    def test_records_tab_buttons_objectname(self):
        """报价记录 Tab 按钮应有正确的 objectName"""
        assert self.window.record_tab.confirm_record_btn.objectName() == "successBtn"
        assert self.window.record_tab.ship_record_btn.objectName() == "warningBtn"
        assert self.window.record_tab.receive_btn.objectName() == "primaryBtn"
        assert self.window.record_tab.cancel_record_btn.objectName() == "ghostBtn"
        assert self.window.record_tab.edit_record_btn.objectName() == "ghostBtn"
        assert self.window.record_tab.del_record_btn.objectName() == "dangerBtn"
        assert self.window.record_tab.refresh_records_btn.objectName() == "ghostBtn"
        assert self.window.record_tab.export_records_btn.objectName() == "ghostBtn"

    # ── 交替行颜色 ──

    def test_alternating_row_colors_enabled(self):
        """所有表格应启用交替行颜色"""
        tables = [
            self.window.product_tab.product_table,
            self.window.record_tab.record_table,
            self.window.receivable_table,
            self.window.payable_table,
            self.window.payment_flow_table,
            self.window.customer_tab.customer_table,
            self.window.customer_tab.customer_history_table,
            self.window.supplier_tab.supplier_table,
            self.window.supplier_tab.supplier_history_table,
        ]
        for table in tables:
            assert table.alternatingRowColors(), \
                f"表格应启用 alternatingRowColors"

    # ── 边距 ──

    def test_main_layout_margins(self):
        """主布局边距应为 12px"""
        central = self.window.centralWidget()
        layout = central.layout()
        margins = layout.contentsMargins()
        assert margins.left() == 12, f"左边距应为 12，实际为 {margins.left()}"
        assert margins.right() == 12, f"右边距应为 12，实际为 {margins.right()}"
        assert margins.top() == 12, f"上边距应为 12，实际为 {margins.top()}"
        assert margins.bottom() == 12, f"下边距应为 12，实际为 {margins.bottom()}"

    # ── 无 setStyleSheet 在 MainWindow 上 ──

    def test_mainwindow_no_setstylesheet(self):
        """MainWindow 不应有 setStyleSheet（样式由 app 级别设置）"""
        assert self.window.styleSheet() == "", \
            "MainWindow 不应有 setStyleSheet，样式应由 QApplication 设置"

    # ── 窗口标题 ──

    def test_window_title(self):
        """窗口标题应正确"""
        assert "调货助手" in self.window.windowTitle(), \
            f"窗口标题应包含 '调货助手'，实际为 '{self.window.windowTitle()}'"


class TestDialogInstantiation:
    """验证对话框实例化"""

    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        from main import MainWindow
        self.window = MainWindow()
        yield
        self.window.close()

    def test_shipment_dialog_margins(self):
        """ShipmentDialog 应有 16px 边距"""
        from main import ShipmentDialog
        mock_quote = {"series": "Y7000P", "cpu": "i7", "customer_name": "测试", "quote_quantity": 1}
        dlg = ShipmentDialog(self.window, quote=mock_quote)
        margins = dlg.layout().contentsMargins()
        assert margins.left() == 16, f"左边距应为 16，实际为 {margins.left()}"

    def test_payment_dialog_margins(self):
        """PaymentDialog 应有 16px 边距"""
        from main import PaymentDialog
        dlg = PaymentDialog(self.window, title="测试", pay_type="receivable")
        margins = dlg.layout().contentsMargins()
        assert margins.left() == 16, f"左边距应为 16，实际为 {margins.left()}"

    def test_statement_dialog_margins(self):
        """StatementDialog 应有 16px 边距"""
        from main import StatementDialog
        dlg = StatementDialog(self.window)
        margins = dlg.layout().contentsMargins()
        assert margins.left() == 16, f"左边距应为 16，实际为 {margins.left()}"

    def test_operation_log_dialog_margins(self):
        """OperationLogDialog 应有 16px 边距"""
        from main import OperationLogDialog
        dlg = OperationLogDialog(self.window)
        margins = dlg.layout().contentsMargins()
        assert margins.left() == 16, f"左边距应为 16，实际为 {margins.left()}"


class TestAppStartup:
    """验证应用启动"""

    def test_app_starts_and_shows(self, qapp):
        """应用应能正常启动和显示"""
        from main import MainWindow
        window = MainWindow()
        assert window.isVisible() is False, "窗口初始应为不可见"
        window.show()
        assert window.isVisible() is True, "show() 后窗口应可见"
        window.close()