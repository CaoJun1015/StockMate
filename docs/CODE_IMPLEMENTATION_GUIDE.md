# Code Agent 实现指令 — UI 重构（v1.11）

> 本指令基于 `UI-DESIGN-SPEC.md` 设计规范，由 Code Agent 执行。
> 代码基线：v1.10 (commit `a59b9a6`)，`src/main.py` 当前为干净状态。

---

## 你的任务

按照 `UI-DESIGN-SPEC.md` 规范，重构 `src/main.py` 的 UI 层。**只改 UI，不改业务逻辑和数据库层。**

---

## 实现步骤

### Step 1：添加 QSS 样式表常量

在 `src/main.py` 文件顶部（import 区域之后、class 定义之前），添加一个 `APP_STYLE` 字符串常量。

**内容**：直接复制 `UI-DESIGN-SPEC.md` 第4节"完整 QSS 样式表"中的 CSS 代码，用 Python 三引号字符串包裹：

```python
APP_STYLE = """
/* 复制 UI-DESIGN-SPEC.md 第4节的完整 QSS */
"""
```

### Step 2：修改 main() 函数入口

将现有的主题逻辑替换为：

```python
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
```

**删除**现有的 `qt_material` 导入和 try/except 逻辑（如果存在）。

### Step 3：添加缺失的 import

确保以下组件已导入：
- `QFrame` — 添加到 `from PyQt6.QtWidgets import (...)` 列表

### Step 4：逐个替换 setStyleSheet 为 setObjectName

按照 `UI-DESIGN-SPEC.md` 第5节的命名规范，将所有按钮和标签的内联 `setStyleSheet` 替换为 `setObjectName`。

**替换规则**：

| 旧代码 | 新代码 |
|--------|--------|
| `btn.setStyleSheet("background-color: #388E3C; color: white; ...")` | `btn.setObjectName("successBtn")` |
| `btn.setStyleSheet("background-color: #F57C00; color: white; ...")` | `btn.setObjectName("warningBtn")` |
| `btn.setStyleSheet("background-color: #1976D2; color: white; ...")` | `btn.setObjectName("primaryBtn")` |
| `btn.setStyleSheet("background-color: #D32F2F; color: white; ...")` | `btn.setObjectName("dangerBtn")` |
| `btn.setStyleSheet("background-color: #757575; color: white; ...")` | `btn.setObjectName("ghostBtn")` |
| `label.setStyleSheet("font-weight: bold; ...")` | `label.setObjectName("summaryLabel")` |
| `label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1976D2; ...")` | `label.setObjectName("sectionTitleBlue")` |
| `text_edit.setStyleSheet("font-size: 13px; ...")` | `text_edit.setObjectName("reportText")` |

**特别注意**：
- `UI-DESIGN-SPEC.md` 第7节列出了**不可移除**的动态样式，保留它们
- 表格行内的操作按钮用 `tableActionPrimary` / `tableActionOrange` / `tableActionDanger`
- 工具栏按钮用 `ghostBtn`
- 搜索框用 `globalSearch`

### Step 5：添加 _add_sep 工具函数

在 `__init__` 的工具栏构建区域，添加分隔线函数：

```python
def _add_sep(layout):
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setObjectName("toolbarSeparator")
    layout.addWidget(sep)
```

在工具栏的按钮组之间调用 `_add_sep(top_bar)`，按设计文档第6节的布局结构分组。

### Step 6：添加 filterCard

在报价记录 Tab（`_build_records_tab`）中，将筛选行包裹在 QFrame 中：

```python
filter_card = QFrame()
filter_card.setObjectName("filterCard")
filter_card_layout = QVBoxLayout(filter_card)
filter_card_layout.setContentsMargins(12, 8, 12, 8)

# 将所有筛选控件添加到 filter_card_layout（而不是直接加到 layout）
filter_card_layout.addLayout(filter_layout)
layout.addWidget(filter_card)
```

**关键**：确保筛选行的**所有控件**（日期选择器、状态下拉、搜索框、导出按钮）都加到 `filter_card_layout` 里，不要遗漏。

### Step 7：启用交替行颜色

为所有 QTableWidget 添加 `setAlternatingRowColors(True)`。

### Step 8：设置 Tab 内容区边距

为每个 Tab 的顶层 layout 设置 `setContentsMargins(12, 12, 12, 12)`。

### Step 9：设置对话框边距

为所有 QDialog 的 layout 设置 `setContentsMargins(16, 16, 16, 16)`。

---

## 禁止事项

1. **禁止修改** `src/models/database.py` — 不改业务逻辑
2. **禁止修改** refresh 函数中的数据处理逻辑 — 不改业务逻辑
3. **禁止删除**现有的功能按钮或事件绑定
4. **禁止修改** `build.spec` 或版本号
5. **禁止运行或打包** — 只改代码，由用户测试

---

## 验证清单

完成后逐项确认：

- [ ] `APP_STYLE` 常量已添加，包含完整 QSS
- [ ] `main()` 函数使用 `app.setStyleSheet(APP_STYLE)` + `app.setStyle("Fusion")`
- [ ] `QFrame` 已添加到 import 列表
- [ ] 所有按钮的 `setStyleSheet` 已替换为 `setObjectName`（除 UI-DESIGN-SPEC 第7节保留的）
- [ ] 所有标签的 `setStyleSheet` 已替换为 `setObjectName`
- [ ] 工具栏有分隔线分组
- [ ] 筛选行包裹在 `filterCard` 中，所有筛选控件都在 card 内
- [ ] 所有表格启用了交替行颜色
- [ ] Tab 内容区有 12px 边距
- [ ] 对话框有 16px 边距
- [ ] 代码能正常 import，无语法错误
