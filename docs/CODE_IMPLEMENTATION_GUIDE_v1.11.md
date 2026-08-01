# Code Agent 实现指令 — v1.11 数据安全优化

> 基于 `diaohuo_v1.11_optimization_plan.md`，由 Code Agent 执行。
> 代码基线：当前 `src/main.py` + `src/models/database.py`。
> **只改代码，不改业务逻辑语义，不打包。**

---

## Phase A：事务保护（严重，先做）

**文件**：`src/models/database.py`

### A1. ship_quote() — 已有事务，确认完整性

当前 `ship_quote()`（第703行）已有 `try/commit/rollback`，确认它包裹了所有多表操作。**无需改动，只需验证。**

### A2. add_payment() — 已有事务，确认完整性

当前 `add_payment()`（第848行）调用 `_add_payment_raw()` 后 `commit()`，已有事务包裹。**无需改动，只需验证。**

### A3. update_payment() — 已有事务，确认完整性

当前 `update_payment()`（第1102行）已有 `try/commit/rollback`。**无需改动，只需验证。**

### A4. delete_payment() — 已有事务，确认完整性

当前 `delete_payment()`（第1151行）已有 `try/commit/rollback`。**无需改动，只需验证。**

### A5. 结论

经过代码审查，4 个关键函数**已有事务保护**。Phase A 改为**验证**而非**新增**。

---

## Phase B：安全加固

### B1. 全局异常钩子

**文件**：`src/main.py`

在 `main()` 函数中，`app = QApplication(...)` 之后添加：

```python
import traceback
from datetime import datetime

def _global_excepthook(exc_type, exc_value, exc_tb):
    """全局未捕获异常处理：写入 operation_logs"""
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        from src.models.database import add_operation_log
        add_operation_log("系统异常", "system", 0, tb_str[:500])
    except Exception:
        pass
    # 同时弹出错误对话框
    QMessageBox.critical(None, "系统错误", f"发生未预期的错误:\n{str(exc_value)}\n\n详细信息已记录到操作日志。")
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _global_excepthook
```

### B2. 备份清理

**文件**：`src/models/database.py`

在 `backup_database()` 函数末尾，添加清理逻辑——保留最近 30 个备份，删除更早的：

```python
def backup_database():
    # ... 现有备份逻辑 ...
    
    # 清理旧备份：保留最近 30 个
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    if os.path.exists(backup_dir):
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith(".db")],
            reverse=True
        )
        for old in backups[30:]:
            os.remove(os.path.join(backup_dir, old))
```

### B3. psutil 打包声明

**文件**：`build.spec`

在 `hiddenimports` 列表中添加 `"psutil"`：

```python
hiddenimports=["PyQt6", "PyQt6.QtWidgets", ..., "psutil"],
```

### B4. 日期格式校验

**文件**：`src/main.py`

在 ShipmentDialog、PaymentDialog、BatchDialog 等对话框的 `get_data()` 或 accept 逻辑中，添加日期校验。如果日期字段为空或格式不是 `YYYY-MM-DD`，弹出警告。

```python
def _validate_date(date_str):
    """校验日期格式 YYYY-MM-DD"""
    if not date_str:
        return True  # 允许空日期
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False
```

在 `on_add_batch`、`on_ship_quote`、`on_receive_payment` 等操作中，调用 `_validate_date(data["date"])`，不通过则 `QMessageBox.warning` 并 `return`。

### B5. SN 格式校验

**文件**：`src/main.py`

在 `ShipmentDialog` 的 `get_data()` 或 `accept()` 中，对 SN 列表做正则校验：

```python
import re
SN_PATTERN = re.compile(r"^[a-zA-Z0-9,;\s\-_]*$")

def _validate_sn(sn_text):
    if not sn_text.strip():
        return True
    return bool(SN_PATTERN.match(sn_text))
```

不通过则弹出警告。

### B6. 文件导入大小限制

**文件**：`src/main.py`

在 `on_import_word` 和 `on_import_excel` 中，选择文件后检查大小：

```python
MAX_WORD_SIZE = 10 * 1024 * 1024  # 10MB
MAX_EXCEL_SIZE = 1 * 1024 * 1024  # 1MB

if os.path.getsize(filepath) > MAX_WORD_SIZE:
    QMessageBox.warning(self, "文件过大", "Word 文件不能超过 10MB")
    return
```

---

## Phase C：数据流优化

**文件**：`src/models/database.py`

### C1. 金额精度

在 `_update_quote_payment_status()` 中，`paid` 比较时使用 `round()`：

```python
new_received = round(row["received_amount"], 2)
total_amount = round(row["quote_price"] * row["quote_quantity"], 2)
paid = "是" if new_received >= total_amount else "否"
```

同样在 `_add_payment_raw()` 中做相同修改。

### C2. 数据完整性检查

新增函数 `verify_data_integrity()`，在 `init_db()` 末尾调用。返回 `(bool, list_of_warnings)`：

```python
def verify_data_integrity():
    """检查数据完整性，返回 (是否干净, 警告列表)"""
    conn = get_connection()
    warnings = []
    
    # 1. 孤儿记录：quotes.batch_id 指向不存在的批次
    orphan = conn.execute("""
        SELECT q.id FROM quotes q
        LEFT JOIN batches b ON q.batch_id = b.id
        WHERE b.id IS NULL AND q.batch_id IS NOT NULL
    """).fetchall()
    if orphan:
        warnings.append(f"发现 {len(orphan)} 条报价的批次已被删除")
    
    # 2. 库存一致性
    mismatches = conn.execute("""
        SELECT b.id, b.remaining, b.quantity,
               COALESCE(SUM(q.quote_quantity), 0) as shipped
        FROM batches b
        LEFT JOIN quotes q ON q.batch_id = b.id AND q.status = '已出库'
        GROUP BY b.id
        HAVING b.remaining != b.quantity - shipped
    """).fetchall()
    if mismatches:
        warnings.append(f"发现 {len(mismatches)} 个批次库存数量不一致")
    
    # 3. 收款金额一致性
    pay_mismatch = conn.execute("""
        SELECT q.id, q.received_amount,
               COALESCE(SUM(p.amount), 0) as pay_total
        FROM quotes q
        LEFT JOIN payments p ON p.quote_id = q.id AND p.type = 'receivable'
        GROUP BY q.id
        HAVING ABS(q.received_amount - pay_total) > 0.01
    """).fetchall()
    if pay_mismatch:
        warnings.append(f"发现 {len(pay_mismatch)} 条报价收款金额与流水不一致")
    
    conn.close()
    return len(warnings) == 0, warnings
```

在 `init_db()` 末尾调用：

```python
ok, warnings = verify_data_integrity()
if not ok:
    for w in warnings:
        print(f"[数据完整性警告] {w}")
```

---

## Phase D：状态机统一

**文件**：`src/models/database.py`

### D1. _update_quote_payment_status 走状态机

当前 `_update_quote_payment_status()` 直接 SQL 更新 `quotes.status`，跳过了状态机守卫。修改为调用 `update_quote_status()`：

```python
def _update_quote_payment_status(conn, quote_id):
    row = conn.execute(
        "SELECT received_amount, quote_price, quote_quantity, status, sn_list FROM quotes WHERE id=?",
        (quote_id,),
    ).fetchone()
    if not row:
        return
    total_amount = round(row["quote_price"] * row["quote_quantity"], 2)
    new_received = round(row["received_amount"], 2)
    current_status = row["status"]
    sn_list = row["sn_list"] or ""

    paid = "是" if new_received >= total_amount else "否"

    if new_received >= total_amount and current_status != "已收款":
        target_status = "已收款"
    elif current_status == "已收款" and new_received < total_amount:
        target_status = "已出库" if sn_list else "待确认"
    else:
        target_status = current_status

    conn.execute("UPDATE quotes SET paid=? WHERE id=?", (paid, quote_id))
    if current_status != target_status:
        conn.execute("UPDATE quotes SET status=? WHERE id=?", (target_status, quote_id))
```

注意：此函数在事务内部被调用，直接 SQL 更新是安全的。状态机守卫 `update_quote_status()` 是独立函数会管理自己的连接，不适合在事务内调用。**保持现状，不修改。**

---

## 禁止事项

1. ❌ 不修改 `src/models/database.py` 中已有的业务逻辑语义
2. ❌ 不拆分 `src/main.py`（架构重构留到后续）
3. ❌ 不添加数据库索引
4. ❌ 不修改 `build.spec` 除 `hiddenimports` 外的内容
5. ❌ 不打包

---

## 验证清单

- [ ] B1: `sys.excepthook` 全局异常钩子已添加
- [ ] B2: `backup_database()` 末尾有清理逻辑
- [ ] B3: `build.spec` 的 `hiddenimports` 包含 `psutil`
- [ ] B4: 日期格式校验函数 `_validate_date()` 已添加，并在对话框中使用
- [ ] B5: SN 格式校验已添加到 ShipmentDialog
- [ ] B6: 文件导入有大小限制（Word 10MB, Excel 1MB）
- [ ] C1: `_update_quote_payment_status` 和 `_add_payment_raw` 中金额使用 `round(, 2)`
- [ ] C2: `verify_data_integrity()` 已添加，并在 `init_db()` 末尾调用
- [ ] 代码能正常 import，无语法错误