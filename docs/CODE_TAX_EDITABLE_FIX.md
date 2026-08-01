# Code Agent 指令 — 税率改为可编辑输入

> 当前问题：税率下拉框只有 3 个固定选项（无/8%/13%），无法输入自定义税率。
> 目标：改为可编辑下拉框，保留快捷选项，同时允许手动输入任意税率值。

---

## 改动范围

3 个文件，3 处税率下拉框，全部同样的改法：

| 文件 | 位置 | 控件 |
|------|------|------|
| `src/ui/dialogs.py` | CustomerDialog 第628行 | 客户默认税率 |
| `src/ui/dialogs.py` | QuoteEditDialog 第716行 | 报价编辑税率 |
| `src/ui/quote_panel.py` | QuotePanel 第107行 | 快速报价税率 |

---

## 改动内容

### 1. 下拉框改为可编辑

```python
# 旧代码
self.tax_combo = QComboBox()
self.tax_combo.addItem("无", None)
self.tax_combo.addItem("8%", 0.08)
self.tax_combo.addItem("13%", 0.13)

# 新代码
self.tax_combo = QComboBox()
self.tax_combo.setEditable(True)                 # ← 允许手动输入
self.tax_combo.addItem("无", None)
self.tax_combo.addItem("8%", 0.08)
self.tax_combo.addItem("13%", 0.13)
self.tax_combo.setCurrentIndex(0)                # ← 默认选中"无"
```

### 2. 读取税率值改用解析函数

在 `dialogs.py` 顶部加一个工具函数（文件级，不在任何 class 内）：

```python
def _parse_tax_rate(combo):
    """解析税率下拉框的值，支持预设选项和手动输入"""
    # 先检查预设选项
    data = combo.currentData()
    if data is not None:
        return data if data > 0 else None
    # 手动输入：解析文本
    text = combo.currentText().strip().replace("%", "").strip()
    if not text:
        return None
    try:
        val = float(text)
        return val / 100.0 if val > 1 else val
    except ValueError:
        return None
```

所有读取税率的地方，把 `self.tax_combo.currentData()` 改为 `_parse_tax_rate(self.tax_combo)`：

| 位置 | 行号 |
|------|------|
| CustomerDialog.get_data() | 第669行 |
| QuoteEditDialog.get_data() | 第800行 |
| QuotePanel 报价保存 | 第354行 |

### 3. 设置税率值时保持兼容

编辑已有报价时，`setCurrentIndex` 的逻辑不变。手动输入的值不在预设列表中时，`findData` 找不到，走 `setEditText` 回退：

```python
# 旧代码
if tax_rate is not None:
    idx = self.tax_combo.findData(tax_rate)
    if idx >= 0:
        self.tax_combo.setCurrentIndex(idx)

# 新代码
if tax_rate is not None:
    idx = self.tax_combo.findData(tax_rate)
    if idx >= 0:
        self.tax_combo.setCurrentIndex(idx)
    else:
        self.tax_combo.setEditText(f"{int(tax_rate * 100)}%")
```

这个逻辑在 3 个位置（CustomerDialog 第639行、QuoteEditDialog 第736行、QuotePanel 第191行）都需要改。

---

## 禁止事项

1. ❌ 不修改数据库表结构
2. ❌ 不修改 `calc_tax_adjusted_profit` 函数
3. ❌ 不修改 `build.spec` 或版本号
4. ❌ 不打包

---

## 验证清单

- [ ] 下拉框仍有"无/8%/13%"三个快捷选项
- [ ] 可以手动输入"3%"→ 解析为 0.03
- [ ] 可以手动输入"6"→ 解析为 0.06
- [ ] 可以手动输入"0.09"→ 解析为 0.09
- [ ] 输入"abc"→ 返回 None（不崩溃）
- [ ] 编辑已有报价时，税率正确回显
- [ ] 客户默认税率也可以手动输入自定义值