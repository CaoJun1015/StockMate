# 价税覆盖层 — 设计文档

> 目的：在现有利润计算逻辑上叠加可选价税分离，解决含税/不含税口径不一致导致的利润失真。
> 原则：95% 订单不受影响，只有显式设置税率的报价才触发额外计算。

---

## 一、数据库改动

### 1.1 quotes 表新增 3 列

```sql
ALTER TABLE quotes ADD COLUMN tax_rate REAL DEFAULT NULL;
ALTER TABLE quotes ADD COLUMN purchase_tax_inclusive INTEGER DEFAULT 0;
ALTER TABLE quotes ADD COLUMN quote_tax_inclusive INTEGER DEFAULT 0;
```

| 列名 | 类型 | 含义 |
|------|------|------|
| `tax_rate` | REAL | 税率（如 0.08 或 0.13），NULL = 不处理价税 |
| `purchase_tax_inclusive` | INTEGER | 进价是否含税（0=不含税, 1=含税） |
| `quote_tax_inclusive` | INTEGER | 售价是否含税（0=不含税, 1=含税） |

### 1.2 customers 表新增 1 列

```sql
ALTER TABLE customers ADD COLUMN default_tax_rate REAL DEFAULT NULL;
```

新建报价时自动带入客户的默认税率，报价创建后仍可手动修改。

### 1.3 在 init_db() 中处理

使用 `ALTER TABLE ... ADD COLUMN` 的 `IF NOT EXISTS` 逻辑（或 `try/except` 捕获重复列错误），确保旧数据库升级时不报错。

---

## 二、计算逻辑

### 2.1 核心公式

```python
def calc_tax_adjusted(purchase_price, quote_price, quantity, tax_rate, purchase_tax_inclusive, quote_tax_inclusive):
    """计算税后利润，如果 tax_rate 为 None 则返回原始利润"""
    if tax_rate is None or tax_rate == 0:
        return (quote_price - purchase_price) * quantity
    
    purchase_excl = purchase_price / (1 + tax_rate) if purchase_tax_inclusive else purchase_price
    quote_excl = quote_price / (1 + tax_rate) if quote_tax_inclusive else quote_price
    return (quote_excl - purchase_excl) * quantity
```

### 2.2 触发条件

只有 `tax_rate IS NOT NULL AND tax_rate > 0` 的报价才走过价税分离逻辑。其余 95% 走原始路径，计算结果不变。

### 2.3 利润分析用途

新建报价时，在 `quotes` 表存储原始 `quote_price`、`purchase_price`（不修改），同时计算 `tax_adjusted_profit` 存为新列或实时计算。利润分析报表优先使用税后利润。

---

## 三、UI 改动

### 3.1 客户对话框（CustomerDialog）

在客户表单中加一个"默认税率"下拉框：

```python
tax_combo = QComboBox()
tax_combo.addItem("无", None)
tax_combo.addItem("8%", 0.08)
tax_combo.addItem("13%", 0.13)
```

### 3.2 报价对话框（QuoteEditDialog / QuotePanel.on_quote_for_batch）

在报价表单中加两个 checkbox 和一个税率显示：

```
[ ] 进价含税    [ ] 售价含税    税率: [8% ▼]
```

- 税率默认从客户 `default_tax_rate` 带入，可手动改
- 两个 checkbox 默认都不勾（95% 用户无感知）
- 税率显示为下拉框：无 / 8% / 13%

### 3.3 报价记录表格

在 `refresh_records()` 中，利润列的计算逻辑改为：

```python
if q.get("tax_rate"):
    profit = calc_tax_adjusted(
        purchase_price, quote_price, quantity,
        q["tax_rate"], q["purchase_tax_inclusive"], q["quote_tax_inclusive"]
    )
else:
    profit = (quote_price - purchase_price) * quantity
```

### 3.4 利润分析 / 统计

所有涉及利润展示的地方（refresh_records 汇总、客户统计、月报、差价分析），有 `tax_rate` 的报价使用税后利润。

---

## 四、database.py 改动

### 4.1 add_quote() 参数扩展

```python
def add_quote(batch_id, customer_id, quote_price, quote_quantity,
              quote_date, note, tax_rate=None,
              purchase_tax_inclusive=0, quote_tax_inclusive=0):
```

新增 3 个参数，默认值保证旧调用代码不变。

### 4.2 update_quote() 参数扩展

同样加 3 个可选参数。

### 4.3 get_all_quotes() 返回扩展

SELECT 中加入 `q.tax_rate, q.purchase_tax_inclusive, q.quote_tax_inclusive`。

### 4.4 新增工具函数

```python
def get_customer_default_tax_rate(customer_id):
    """获取客户的默认税率"""
```

---

## 五、不改动的内容

- ❌ `batches` 表不动（进价含税按报价判断，不按批次）
- ❌ 现有 `quote_price`、`purchase_price` 字段不动
- ❌ 现有利润计算逻辑也不删，只是多算一个调整值
- ❌ 95% 无税率的报价走完全相同的代码路径

---

## 六、回滚方案

如果出问题，把 `tax_rate` 设为 NULL：

```sql
UPDATE quotes SET tax_rate = NULL WHERE tax_rate IS NOT NULL;
```

所有利润计算自动回退到原始逻辑。

---

## 七、测试用例

1. 含税进价 + 含税售价 → 验证税后利润正确
2. 含税进价 + 不含税售价 → 验证只剥离进价税
3. 不含税进价 + 含税售价 → 验证只剥离售价税
4. 无税率报价 → 验证利润和原来一样
5. 客户默认税率自动带入 → 新建报价验证
6. 客户税率改了，历史报价利润不变 → 验证独立性