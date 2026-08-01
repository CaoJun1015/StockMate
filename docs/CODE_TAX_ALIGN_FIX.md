# Code Agent 指令 — 价税控件右对齐

两处改动，`src/ui/quote_panel.py`：

## 改动 1：税控控件从 grid 中移出，改为独立右对齐行

找到第 106-119 行（价税覆盖层注释块），将税控控件从 `glayout` 的 grid 中移除，改为独立 `QHBoxLayout`：

```python
# 价税覆盖层（右对齐，放在按钮行下方，避免与"生成图片"重叠）
self.tax_combo = QComboBox()
self.tax_combo.setEditable(True)
self.tax_combo.addItem("无", None)
self.tax_combo.addItem("8%", 0.08)
self.tax_combo.addItem("13%", 0.13)
self.tax_combo.setCurrentIndex(0)
self.purchase_tax_check = QCheckBox("进价含税")
self.quote_tax_check = QCheckBox("售价含税")

tax_row = QHBoxLayout()
tax_row.addStretch()          # ← 推到右侧
tax_row.addWidget(QLabel("税率:"))
tax_row.addWidget(self.tax_combo)
tax_row.addWidget(self.purchase_tax_check)
tax_row.addWidget(self.quote_tax_check)
```

## 改动 2：把 tax_row 加入布局

在 `layout.addWidget(group)` 之后、`layout.addStretch()` 之前，加一行：

```python
layout.addWidget(group)
layout.addLayout(tax_row)    # ← 新增
layout.addStretch()
```

---

禁止：不改数据库、不打包。