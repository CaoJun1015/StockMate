"""全局样式常量"""
APP_STYLE = """
/* ---- 全局基础 ---- */
QMainWindow { background-color: #F8FAFC; }
QWidget { font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; font-size: 13px; color: #1E293B; }

/* ---- 输入控件 ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
    padding: 7px 12px;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    background: #FFFFFF;
    color: #1E293B;
    font-size: 13px;
    selection-background-color: #DBEAFE;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus { border-color: #3B82F6; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    width: 10px; height: 10px; image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #64748B;
    margin-right: 6px;
}
QTextEdit {
    padding: 8px 12px; border: 1px solid #E2E8F0;
    border-radius: 6px; background: #FFFFFF;
    font-size: 13px; selection-background-color: #DBEAFE;
}

/* ---- 按钮基础 ---- */
QPushButton {
    padding: 6px 16px; border-radius: 6px;
    border: 1px solid #E2E8F0; background: #FFFFFF;
    color: #1E293B; font-size: 13px;
}
QPushButton:hover { background: #F1F5F9; border-color: #CBD5E1; }
QPushButton:pressed { background: #E2E8F0; }
QPushButton:disabled { color: #94A3B8; background: #F1F5F9; border-color: #E2E8F0; }

/* Primary */
QPushButton#primaryBtn { background: #3B82F6; color: #FFFFFF; border: 1px solid #3B82F6; }
QPushButton#primaryBtn:hover { background: #2563EB; border-color: #2563EB; }
QPushButton#primaryBtn:pressed { background: #1D4ED8; }

/* Success */
QPushButton#successBtn { background: #F0FDF4; color: #16A34A; border: 1px solid #BBF7D0; }
QPushButton#successBtn:hover { background: #DCFCE7; border-color: #86EFAC; }
QPushButton#successBtn:pressed { background: #BBF7D0; }

/* Warning */
QPushButton#warningBtn { background: #FFFBEB; color: #D97706; border: 1px solid #FDE68A; }
QPushButton#warningBtn:hover { background: #FEF3C7; border-color: #FCD34D; }
QPushButton#warningBtn:pressed { background: #FDE68A; }

/* Danger */
QPushButton#dangerBtn { background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; }
QPushButton#dangerBtn:hover { background: #FEE2E2; border-color: #FCA5A5; }
QPushButton#dangerBtn:pressed { background: #FECACA; }

/* Ghost */
QPushButton#ghostBtn { background: transparent; border: 1px solid #E2E8F0; color: #475569; }
QPushButton#ghostBtn:hover { background: #F8FAFC; border-color: #CBD5E1; color: #1E293B; }
QPushButton#ghostBtn:pressed { background: #F1F5F9; }

/* 表内操作按钮 */
QPushButton#tableActionPrimary {
    background: #EFF6FF; color: #2563EB; border: none;
    padding: 4px 12px; font-size: 12px; border-radius: 4px;
}
QPushButton#tableActionPrimary:hover { background: #DBEAFE; }
QPushButton#tableActionOrange {
    background: #FFFBEB; color: #D97706; border: none;
    padding: 4px 12px; font-size: 12px; border-radius: 4px;
}
QPushButton#tableActionOrange:hover { background: #FEF3C7; }
QPushButton#tableActionDanger {
    background: #FEF2F2; color: #DC2626; border: none;
    padding: 4px 12px; font-size: 12px; border-radius: 4px;
}
QPushButton#tableActionDanger:hover { background: #FEE2E2; }

/* 诊断选项按钮 */
QPushButton#diagnoseOptionBtn {
    text-align: left; padding: 8px 16px; font-size: 13px;
    border: 1px solid #E2E8F0; border-radius: 6px; background: #FFFFFF;
}
QPushButton#diagnoseOptionBtn:hover { background: #F1F5F9; border-color: #CBD5E1; }

/* ---- 表格 ---- */
QTableWidget {
    background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;
    gridline-color: #F1F5F9; font-size: 13px;
    selection-background-color: #EFF6FF; selection-color: #1E293B;
    alternate-background-color: #F8FAFC;
}
QHeaderView::section {
    background: #F8FAFC; color: #64748B; font-weight: 600;
    font-size: 12px; padding: 8px 12px;
    border: none; border-bottom: 2px solid #E2E8F0;
}

/* ---- Tab ---- */
QTabWidget::pane {
    border: 1px solid #E2E8F0; border-radius: 0 0 8px 8px;
    background: #FFFFFF; padding: 0px;
}
QTabBar::tab {
    background: transparent; color: #64748B; padding: 10px 24px;
    font-size: 13px; font-weight: 500; border: none;
    border-bottom: 2px solid transparent; margin-right: 2px;
}
QTabBar::tab:selected { color: #1E293B; font-weight: 600; border-bottom: 2px solid #3B82F6; }
QTabBar::tab:hover:!selected { color: #1E293B; background: #F8FAFC; }

/* ---- GroupBox ---- */
QGroupBox {
    font-weight: 600; font-size: 13px; color: #1E293B;
    border: 1px solid #E2E8F0; border-radius: 8px;
    margin-top: 16px; padding: 20px 12px 12px 12px; background: #FFFFFF;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 16px;
    padding: 0 8px; color: #1E293B;
}

/* ---- StatusBar ---- */
QStatusBar {
    background: #FFFFFF; border-top: 1px solid #E2E8F0;
    font-size: 12px; color: #64748B; padding: 2px 16px;
}

/* ---- Dialog ---- */
QDialog { background: #FFFFFF; }
QDialogButtonBox QPushButton { min-width: 80px; padding: 6px 24px; }

/* ---- Splitter ---- */
QSplitter::handle:horizontal { background: #E2E8F0; width: 1px; }
QSplitter::handle:vertical { background: #E2E8F0; height: 1px; }

/* ---- 语义化标签 ---- */
QLabel#sectionTitle { font-size: 15px; font-weight: 600; color: #1E293B; padding: 4px 0; }
QLabel#sectionTitleBlue { font-size: 15px; font-weight: 600; color: #3B82F6; padding: 4px 0; }
QLabel#sectionTitleRed { font-size: 15px; font-weight: 600; color: #DC2626; padding: 4px 0; }
QLabel#sectionTitleOrange { font-size: 15px; font-weight: 600; color: #D97706; padding: 4px 0; }
QLabel#summaryLabel { font-weight: 600; font-size: 14px; padding: 8px; color: #1E293B; }
QLabel#dangerSummaryLabel { font-weight: 600; font-size: 14px; padding: 8px; color: #DC2626; }
QLabel#dialogInfoLabel { font-size: 14px; font-weight: 600; padding: 8px; color: #1E293B; background: #F8FAFC; border-radius: 6px; }
QLabel#filterLabel { color: #64748B; font-size: 12px; }
QLabel#appTitle { font-size: 16px; font-weight: 700; color: #1E293B; padding: 0 4px; }
QTextEdit#reportText { font-size: 13px; font-family: 'Microsoft YaHei', monospace; padding: 12px; }

/* ---- 容器 ---- */
QFrame#filterCard { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; }
QFrame#toolbarSeparator { color: #E2E8F0; max-width: 1px; margin: 0 8px; }

/* ---- 搜索框 ---- */
QLineEdit#globalSearch {
    padding: 7px 12px 7px 32px; border: 1px solid #E2E8F0;
    border-radius: 6px; background: #F8FAFC; font-size: 13px;
}
QLineEdit#globalSearch:focus { background: #FFFFFF; border-color: #3B82F6; }
"""
