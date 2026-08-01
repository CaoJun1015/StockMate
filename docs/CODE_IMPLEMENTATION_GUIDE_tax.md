# Code Agent 实现指令 — 价税覆盖层

> 基于 `docs/tax_overlay_design.md`，由 Code Agent 执行。
> 代码基线：当前 `src/main.py` + `src/models/database.py`。
> 原则：95% 订单不受影响，只改代码不改业务逻辑语义。

---

## Step 1：数据库升级

**文件**：`src/models/database.py`

### 1.1 在 `init_db()` 中添加新列

在 `init_db()` 函数末尾，`CREATE TABLE` 语句之后，添加：

```python
# v1.11 价税覆盖层
for col, col_def in [
    ("tax_rate", "REAL DEFAULT NULL"),
    ("purchase_tax_inclusive", "INTEGER DEFAULT 0"),
    ("quote_tax_inclusive", "INTEGER DEFAULT 0"),
]:
    try:
        conn.execute(f"ALTER TABLE quotes ADD COLUMN {col} {col_def}")
    except sqlite3.OperationalError:
        pass  # 列已存在

try:
    conn.execute("ALTER TABLE customers ADD COLUMN default_tax_rate REAL DEFAULT NULL")
except sqlite3.OperationalError:
    pass
```

### 1.2 新增工具函数

在 `database.py` 末尾添加：

```python
def calc_tax_adjusted_profit(purchase_price, quote_price, quantity, tax_rate, purchase_tax_inclusive, quote_tax_inclusive):
    """计算税后利润，tax_rate 为 None 时返回原始利润"""
    if tax_rate is None or tax_rate == 0:
        return (quote_price - purchase_price) * quantity
    purchase_excl = purchase_price / (1 + tax_rate) if purchase_tax_inclusive else purchase_price
    quote_excl = quote_price / (1 + tax_rate) if quote_tax_inclusive else quote_price
    return (quote_excl - purchase_excl) * quantity


def get_customer_default_tax_rate(customer_id):
    """获取客户的默认税率"""
    conn = get_connection()
    row = conn.execute("SELECT default_tax_rate FROM customers WHERE id=?", (customer_id,)).fetchone()
    conn.close()
    return row["default_tax_rate"] if row else None
```

### 1.3 扩展 add_quote()

当前 `add_quote()` 签名类似 `add_quote(batch_id, customer_id, quote_price, quote_quantity, quote_date, note)`。改为：

```python
def add_quote(batch_id, customer_id, quote_price, quote_quantity, quote_date, note,
              tax_rate=None, purchase_tax_inclusive=0, quote_tax_inclusive=0):
```

INSERT 语句加入新列：

```python
conn.execute(
    "INSERT INTO quotes (batch_id, customer_id, quote_price, quote_quantity, status, quote_date, note, tax_rate, purchase_tax_inclusive, quote_tax_inclusive) VALUES (?, ?, ?, ?, '待确认', ?, ?, ?, ?, ?)",
    (batch_id, customer_id, quote_price, quote_quantity, quote_date, note, tax_rate, purchase_tax_inclusive, quote_tax_inclusive),
)
```

### 1.4 扩展 update_quote()

在 `update_quote()` 的 UPDATE 语句中加入 3 个新列。参数加 `tax_rate=None, purchase_tax_inclusive=0, quote_tax_inclusive=0`。

### 1.5 扩展 get_all_quotes()

SELECT 中加入：

```sql
q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive
```

### 1.6 扩展 get_quote_by_id()

同样加入 3 个新列。

---

## Step 2：客户对话框

**文件**：`src/ui/dialogs.py`

### 2.1 CustomerDialog 加默认税率

在 `CustomerDialog` 的表单中添加一行"默认税率"下拉框：

```python
# 在 __init__ 中，表单布局的最下面加一行
tax_layout = QHBoxLayout()
tax_layout.addWidget(QLabel("默认税率:"))
self.tax_combo = QComboBox()
self.tax_combo.addItem("无", None)
self.tax_combo.addItem("8%", 0.08)
self.tax_combo.addItem("13%", 0.13)
tax_layout.addWidget(self.tax_combo)
tax_layout.addStretch()
layout.addLayout(tax_layout)
```

在 `get_data()` 返回中加 `"default_tax_rate": self.tax_combo.currentData()`。

在 `set_data()` 中设置税率：找到对应值选中。

### 2.2 客户编辑时读取税率

在 `on_edit_customer_from_table` 中，读取客户数据时包含 `default_tax_rate`。

---

## Step 3：报价对话框

**文件**：`src/ui/dialogs.py` 或 `src/ui/quote_panel.py`

### 3.1 QuoteEditDialog 加价税控件

在报价表单中，在价格字段下方添加：

```python
# 税率行
tax_layout = QHBoxLayout()
tax_layout.addWidget(QLabel("税率:"))
self.tax_combo = QComboBox()
self.tax_combo.addItem("无", None)
self.tax_combo.addItem("8%", 0.08)
self.tax_combo.addItem("13%", 0.13)
tax_layout.addWidget(self.tax_combo)
tax_layout.addStretch()
layout.addLayout(tax_layout)

# 含税勾选行
check_layout = QHBoxLayout()
self.purchase_tax_check = QCheckBox("进价含税")
self.quote_tax_check = QCheckBox("售价含税")
check_layout.addWidget(self.purchase_tax_check)
check_layout.addWidget(self.quote_tax_check)
check_layout.addStretch()
layout.addLayout(check_layout)
```

在 `get_data()` 中加：

```python
"tax_rate": self.tax_combo.currentData(),
"purchase_tax_inclusive": 1 if self.purchase_tax_check.isChecked() else 0,
"quote_tax_inclusive": 1 if self.quote_tax_check.isChecked() else 0,
```

### 3.2 创建报价时自动带入客户税率

在 `QuotePanel.on_quote_for_batch` 或创建报价的位置，打开 `QuoteEditDialog` 之前，查询客户税率并设置：

```python
from src.models.database import get_customer_default_tax_rate
tax_rate = get_customer_default_tax_rate(customer_id)
# 传给对话框或预填
```

---

## Step 4：报价记录表格

**文件**：`src/ui/record_tab.py`

### 4.1 利润列改用税后计算

在 `refresh_records()` 中，利润列的计算逻辑改为：

```python
from src.models.database import calc_tax_adjusted_profit

# 原代码：
# profit = (quote_price - purchase_price) * quantity

# 改为：
tax_rate = q.get("tax_rate")
profit = calc_tax_adjusted_profit(
    purchase_price, quote_price, quantity,
    tax_rate,
    q.get("purchase_tax_inclusive", 0),
    q.get("quote_tax_inclusive", 0),
)
```

### 4.2 汇总行也使用税后利润

`total_cost` 和 `total_sale` 的汇总逻辑不变（成本口径不变），但显示的利润汇总使用税后值。

---

## Step 5：利润分析相关

**文件**：`src/ui/record_tab.py`、`src/ui/customer_tab.py`

### 5.1 客户统计利润

`refresh_customer_stats()` 中，利润汇总也改用 `calc_tax_adjusted_profit()`。

### 5.2 月报 / 差价分析

`src/utils/monthly_report.py` 和 `src/utils/price_diff.py` — 如果接收到的数据中已包含 `tax_rate` 字段，则使用税后利润。否则保持现状。

---

## 禁止事项

1. ❌ 不修改 `quote_price` 或 `purchase_price` 的存储值
2. ❌ 不修改 `batches` 表
3. ❌ 不修改 `ship_quote` 或 `add_payment` 的流程
4. ❌ 不修改 `build.spec` 或版本号
5. ❌ 不打包

---

## 验证清单

- [ ] `init_db()` 能正常升级旧数据库（重复运行不报错）
- [ ] `calc_tax_adjusted_profit(1500, 1450, 1, 0.13, 1, 0)` 返回正数（约 177）
- [ ] `calc_tax_adjusted_profit(100, 200, 1, None, 0, 0)` 返回 100（和原来一样）
- [ ] 客户对话框有默认税率下拉框
- [ ] 报价对话框有税率先项和两个 checkbox
- [ ] 无税率报价 → 利润列显示和原来一样
- [ ] 有税率报价 → 利润列显示税后值
- [ ] `python -m pytest tests/ -q` 全部通过