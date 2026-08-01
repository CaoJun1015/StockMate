# 调货助手 UI 设计规范

> 风格：现代扁平 (Slate 灰 + 微蓝)，参考 Notion/Linear 设计语言
> 适用文件：`src/main.py`
> 应用方式：`app.setStyleSheet(APP_STYLE)` (Fusion 风格基础上覆盖)

---

## 1. 色彩体系

### 基础色板

| Token | 色值 | 用途 |
|-------|------|------|
| `bg-primary` | `#F8FAFC` | 主窗口背景 |
| `bg-card` | `#FFFFFF` | 卡片 / 面板 / 表格背景 |
| `bg-surface` | `#F1F5F9` | 次级表面 (hover 态) |
| `bg-hover` | `#E2E8F0` | pressed 态 / 分割线 |
| `border-default` | `#E2E8F0` | 默认边框 |
| `text-primary` | `#1E293B` | 主文字 |
| `text-secondary` | `#475569` | 次要文字 (Ghost 按钮) |
| `text-tertiary` | `#64748B` | 最弱文字 (表头、状态栏) |
| `text-disabled` | `#94A3B8` | 禁用态文字 |

### 语义色

| Token | 色值 | Hover | 用途 |
|-------|------|-------|------|
| `accent-primary` | `#3B82F6` | `#2563EB` | 主强调、Primary 按钮、Tab 选中线 |
| `accent-success` | `#16A34A` | `#DCFCE7` bg | 确认 / 收款 / 导出 |
| `accent-warning` | `#D97706` | `#FEF3C7` bg | 出库 / 付款 / 警告 |
| `accent-danger` | `#DC2626` | `#FEE2E2` bg | 删除 / 应付金额 |

### 语义色浅底 (用于非实心按钮背景)

| 名称 | 色值 | 对应边框 |
|------|------|---------|
| Primary 浅底 | `#EFF6FF` | 无 |
| Success 浅底 | `#F0FDF4` | `#BBF7D0` |
| Warning 浅底 | `#FFFBEB` | `#FDE68A` |
| Danger 浅底 | `#FEF2F2` | `#FECACA` |

---

## 2. 字体

| 属性 | 值 |
|------|-----|
| 字体族 | `'Microsoft YaHei', 'Segoe UI', sans-serif` |
| 基础字号 | `13px` |
| 表头字号 | `12px` |
| 小号 (表内按钮) | `12px` |
| 摘要标签 | `14px` |
| 节标题 | `15px` |
| 应用标题 | `16px`, `font-weight: 700` |
| 报告文本 | `13px`, `'Microsoft YaHei', monospace` |

---

## 3. 圆角与间距

### 圆角

| 用途 | 值 |
|------|-----|
| 按钮 / 输入框 | `6px` |
| 表格 / Tab 面板 / GroupBox / 筛选卡片 | `8px` |
| 表内操作按钮 | `4px` |

### 间距

| 场景 | 值 |
|------|-----|
| Tab 内容区内边距 | `12px` |
| 对话框内边距 | `16px` |
| 按钮内边距 (标准) | `6px 16px` |
| 按钮内边距 (表内) | `4px 12px` |
| 输入框内边距 | `7px 12px` |
| 表格单元格 | `6px 8px` |
| 表头内边距 | `8px 12px` |
| Tab 标签内边距 | `10px 24px` |
| 筛选卡片内边距 | `12px 8px` |
| GroupBox 内部 padding | `20px 12px 12px 12px` |

---

## 4. 完整 QSS 样式表

```css
/* ---- 全局基础 ---- */
QMainWindow { background-color: #F8FAFC; }
QWidget { font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; font-size: 13px; color: #1E293B; }

/* ---- 输入控件 ---- */
QLineEdit, QComboBox, QSpinBox, QDateEdit {
    padding: 7px 12px;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    background: #FFFFFF;
    color: #1E293B;
    font-size: 13px;
    selection-background-color: #DBEAFE;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: #3B82F6; }
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
QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid #F1F5F9; }
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
```

---

## 5. objectName 命名规范

### 命名格式

```
<类型><语义描述>   (camelCase)
```

**类型前缀**:

| 前缀 | 说明 | 示例 |
|------|------|------|
| (无前缀) | 全局控件，由 QSS `#id` 选择器匹配 | `primaryBtn`, `filterCard` |
| `tableAction` + 颜色 | 表格行内小按钮 | `tableActionPrimary` |

### 按钮体系 (5 级 + 3 表内)

| objectName | QSS 选择器 | 视觉 | 典型用途 |
|------------|-----------|------|---------|
| `primaryBtn` | `QPushButton#primaryBtn` | 蓝色实心 `#3B82F6` 白字 | 新增机型、报价、收款、新增客户/上游 |
| `successBtn` | `QPushButton#successBtn` | 绿底绿字 `#F0FDF4` / `#16A34A` | 确认报价、导出 Excel、生成报告 |
| `warningBtn` | `QPushButton#warningBtn` | 黄底橙字 `#FFFBEB` / `#D97706` | 出库 |
| `dangerBtn` | `QPushButton#dangerBtn` | 红底红字 `#FEF2F2` / `#DC2626` | 删除机型、删除批次、删除客户/上游、删除记录 |
| `ghostBtn` | `QPushButton#ghostBtn` | 透明底灰边灰字 | 工具栏全部按钮、编辑、刷新 |
| `tableActionPrimary` | `QPushButton#tableActionPrimary` | 浅蓝底蓝字无框 | 应收款收款按钮、流水编辑按钮 |
| `tableActionOrange` | `QPushButton#tableActionOrange` | 浅黄底橙字无框 | 应付款付款按钮 |
| `tableActionDanger` | `QPushButton#tableActionDanger` | 浅红底红字无框 | 流水删除按钮 |
| `diagnoseOptionBtn` | `QPushButton#diagnoseOptionBtn` | 白底灰边左对齐 | 诊断选项列表 |

### 标签体系

| objectName | 视觉 | 用途 |
|------------|------|------|
| `appTitle` | 16px 粗体 `#1E293B` | 顶部应用标题 "调货助手 v1.10" |
| `sectionTitle` | 15px 半粗 `#1E293B` | 通用节标题 (预留) |
| `sectionTitleBlue` | 15px 半粗 `#3B82F6` | 机型标题、诊断报告标题、价格异动标题 |
| `sectionTitleRed` | 15px 半粗 `#DC2626` | "应收款合计" 标题 |
| `sectionTitleOrange` | 15px 半粗 `#D97706` | "应付款合计" 标题 |
| `summaryLabel` | 14px 半粗 `#1E293B` | 统计摘要 (客户数/供应商数/记录统计) |
| `dangerSummaryLabel` | 14px 半粗 `#DC2626` | 危险摘要 (待收金额警告) |
| `dialogInfoLabel` | 14px 半粗 `#1E293B` 浅灰底 | 对话框内信息摘要 |
| `filterLabel` | 12px `#64748B` | 筛选行标签 (预留) |

### 文本编辑器

| objectName | 视觉 | 用途 |
|------------|------|------|
| `reportText` | 13px 等宽字体 12px 内边距 | 月度报告、诊断结果、诊断报告 |

### 容器

| objectName | 视觉 | 用途 |
|------------|------|------|
| `filterCard` | 浅灰底 `#F8FAFC` 圆角边框 | 报价记录 Tab 的筛选栏容器 |
| `toolbarSeparator` | 1px 竖线 `#E2E8F0` | 工具栏按钮分组分隔符 |

### 输入框

| objectName | 视觉 | 用途 |
|------------|------|------|
| `globalSearch` | 浅灰底 `#F8FAFC`，focus 变白 | 顶部搜索框、客户搜索、供应商搜索 |

---

## 6. 布局结构

### 主窗口层级

```
QMainWindow
  |
  +-- QLabel "调货助手 v1.10"  (objectName="appTitle")
  +-- QHBoxLayout (工具栏)
  |     +-- QLineEdit 搜索框 (objectName="globalSearch")
  |     +-- def _add_sep()  -->  QFrame (objectName="toolbarSeparator")
  |     +-- [智能技能组] 跟单提醒 | 月度报告 | 远程诊断 | 价格异动 | 报价助手 | 出库一条龙
  |     +-- _add_sep()
  |     +-- [数据操作组] 导入Word | 群发图片 | 导出Excel | 对账单 | JSON备份 | JSON导入
  |     +-- _add_sep()
  |     +-- [系统组] 操作日志
  |     +-- stretch
  |
  +-- QTabWidget
       +-- Tab 1: 机型管理  (QHBoxLayout, margins=12px)
       |     +-- Left: 按钮行 + QTableWidget (product_table, alternatingRowColors)
       |     +-- QSplitter
       |     +-- Right: QuotePanel
       |           +-- QLabel 机型标题 (sectionTitleBlue)
       |           +-- QGroupBox "报价操作"
       |           |     +-- QTableWidget (batch_table, alternatingRowColors)
       |           |     +-- QPushButton 报价并复制 (primaryBtn)
       |           |     +-- QPushButton 生成报价图片 (ghostBtn)
       |           +-- QPushButton +新增批次 (primaryBtn)
       |           +-- QPushButton 刷新 (ghostBtn)
       |
       +-- Tab 2: 客户管理  (QVBoxLayout, margins=12px)
       |     +-- 按钮行: +新增(primaryBtn) | 删除(dangerBtn) | QLineEdit搜索(globalSearch)
       |     +-- QTableWidget (customer_table, alternatingRowColors)
       |     +-- QLabel 统计 (summaryLabel)
       |     +-- QGroupBox "购买历史"
       |           +-- QTableWidget (customer_history_table, alternatingRowColors)
       |
       +-- Tab 3: 上游管理  (QVBoxLayout, margins=12px)
       |     +-- 按钮行: +新增(primaryBtn) | 删除(dangerBtn) | QLineEdit搜索(globalSearch)
       |     +-- QTableWidget (supplier_table, alternatingRowColors)
       |     +-- QLabel 统计 (summaryLabel)
       |     +-- QGroupBox "采购历史"
       |           +-- QTableWidget (supplier_history_table, alternatingRowColors)
       |
       +-- Tab 4: 报价记录  (QVBoxLayout, margins=12px)
       |     +-- 按钮行: 确认(successBtn) | 出库(warningBtn) | 收款(primaryBtn)
       |     |         取消(ghostBtn) | 编辑(ghostBtn) | 删除(dangerBtn) | 刷新(ghostBtn)
       |     +-- QFrame#filterCard
       |     |     +-- 开始: 日期 | 结束: 日期 | 状态: 下拉 | 搜索: 输入框
       |     +-- QTableWidget (record_table, alternatingRowColors, 17列)
       |     +-- QLabel 统计 (summaryLabel)
       |     +-- QPushButton 导出Excel (ghostBtn)
       |
       +-- Tab 5: 账款管理  (QVBoxLayout, margins=12px)
             +-- QSplitter (水平)
             |     +-- Left: QLabel "应收款合计"(sectionTitleRed)
             |     |   +-- QTableWidget (receivable_table, alternatingRowColors)
             |     |   +-- QPushButton 刷新 (ghostBtn)
             |     +-- Right: QLabel "应付款合计"(sectionTitleOrange)
             |           +-- QTableWidget (payable_table, alternatingRowColors)
             |           +-- QPushButton 刷新 (ghostBtn)
             +-- QGroupBox "收付款流水"
                   +-- 筛选行 | QPushButton 刷新 (ghostBtn)
                   +-- QTableWidget (payment_flow_table, alternatingRowColors)
                   +-- QLabel 统计 (summaryLabel)
  |
  +-- QStatusBar
```

### 对话框统一规范

所有 9 个 QDialog 的 layout 均设置 `setContentsMargins(16, 16, 16, 16)`：

| 对话框 | 布局 | 特殊 objectName |
|--------|------|----------------|
| ShipmentDialog | QFormLayout | `dialogInfoLabel` |
| PaymentDialog | QFormLayout | `dialogInfoLabel`, `dangerSummaryLabel` |
| PaymentEditDialog | QFormLayout | `dialogInfoLabel` |
| StatementDialog | QVBoxLayout | `successBtn` (导出), `summaryLabel` |
| ProductEditDialog | QFormLayout | - |
| BatchDialog | QFormLayout | - |
| CustomerDialog | QFormLayout | - |
| QuoteEditDialog | QFormLayout | - |
| OperationLogDialog | QVBoxLayout | - |

---

## 7. 保留的动态样式

以下 `setStyleSheet` 因运行时数据驱动，不可移入 QSS：

| 位置 | 代码 | 原因 |
|------|------|------|
| `_update_remaining()` (行 110) | `self.remaining_label.setStyleSheet(f"color: {color}; ...")` | 颜色取决于库存数量与需求数量的比较 |

---

## 8. 技术约束

- PyQt6 QSS 不支持 CSS 变量 (`var()`)、嵌套选择器、`:nth-child()`、`@media`
- 多个控件可共享同一 objectName，QSS 选择器会匹配所有同名控件
- QSS 应用方式：`app.setStyleSheet(APP_STYLE)` (应用级)，确保所有 QDialog 继承样式
- 基础窗口风格：`app.setStyle("Fusion")`