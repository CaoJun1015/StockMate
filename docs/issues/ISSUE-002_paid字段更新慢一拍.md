# ISSUE-002: `paid` 字段更新使用旧值，永远慢一拍

## 严重程度：🔴 高

## 状态：已修复 ✅

## 问题描述

`add_payment()` 函数中，`paid` 字段的更新逻辑在同一个 UPDATE 语句中使用了 `received_amount` 的旧值（更新前的值），导致第一次收款即使收满了，`paid` 仍然是"否"，需要再收一次（哪怕 0 元）才会变成"是"。

## 复现步骤

1. 创建一条报价记录：quote_price=10000, quote_quantity=1
2. 状态变为"已出库"
3. 收款 10000 元（一次性收满）
4. 查询 `quotes` 表：`SELECT paid, received_amount FROM quotes WHERE id=?`

## 期望行为

- `received_amount` = 10000
- `paid` = "是"（因为 10000 >= 10000）

## 实际行为

- `received_amount` = 10000 ✓
- `paid` = "否" ✗（应该为"是"）
- `status` = "已收款" ✓（这部分是正确的，见根因分析）

## 根因分析

**位置**：`src/models/database.py` 第 713-714 行 `add_payment()`

```python
conn.execute(
    "UPDATE quotes SET received_amount = received_amount + ?, "
    "paid = CASE WHEN received_amount >= quote_price * quote_quantity THEN '是' ELSE '否' END "
    "WHERE id=?",
    (amount, quote_id),
)
```

**问题**：这是一个单行 UPDATE，但 SQL 的标准行为是，在同一个 UPDATE 语句中，CASE 表达式里引用的 `received_amount` 使用的是**更新前的值**，而不是 `received_amount + ?` 后的新值。

所以：
- 第一次收款前：`received_amount` = 0
- 更新后的 `received_amount` = 0 + 10000 = 10000 ✓
- 但 CASE 里判断的是 `0 >= 10000`，所以 `paid` = "否" ✗

**为什么 `status` 更新是正确的？**

因为在 UPDATE 之后，代码又做了一次 SELECT（第 715-719 行）：

```python
row = conn.execute(
    "SELECT received_amount, quote_price, quote_quantity FROM quotes WHERE id=?", (quote_id,)
).fetchone()
if row and row[0] >= (row[1] * row[2]):
    conn.execute("UPDATE quotes SET status='已收款' WHERE id=?", (quote_id,))
```

这是两次独立的 SQL 执行，第二次 SELECT 时 `received_amount` 已经是新值了，所以 `status` 判断正确。但 `paid` 是在同一次 UPDATE 里用旧值判断的，所以出错。

## 修复方案

### 方案A：拆分为两次 UPDATE（推荐）

先更新 `received_amount`，再单独更新 `paid` 和 `status`：

```python
# 第一步：更新 received_amount
conn.execute(
    "UPDATE quotes SET received_amount = received_amount + ? WHERE id=?",
    (amount, quote_id),
)

# 第二步：查询新值，更新 paid 和 status
row = conn.execute(
    "SELECT received_amount, quote_price, quote_quantity FROM quotes WHERE id=?", (quote_id,)
).fetchone()
if row:
    new_received = row[0]
    total = row[1] * row[2]
    paid = "是" if new_received >= total else "否"
    status = "已收款" if new_received >= total else row[2]  # 需要保持原状态或设为已出库
    conn.execute(
        "UPDATE quotes SET paid=?, status=? WHERE id=?",
        (paid, status, quote_id)
    )
```

### 方案B：使用 SQL 表达式直接计算

```sql
UPDATE quotes SET
    received_amount = received_amount + ?,
    paid = CASE WHEN (received_amount + ?) >= (quote_price * quote_quantity) THEN '是' ELSE '否' END
WHERE id=?
```

**方案B的问题**：amount 需要传两次，参数冗余。而且如果金额很大导致溢出等极端情况，逻辑不一致。

**推荐方案A**，清晰且避免 SQL 表达式歧义。

## 测试要求

1. 创建报价：quote_price=10000, quantity=1
2. 出库
3. 收款 10000
4. 验证：paid="是", status="已收款", received_amount=10000
5. 再收款 0
6. 验证：paid 仍为"是"（不应改变）

## 相关文件

- `src/models/database.py`：`add_payment()` 函数
- `tests/test_database.py`：`test_add_payment_triggers_status_update`

## 备注

这个 Bug 同时影响 `update_payment()` 和 `delete_payment()` 中的 `paid` 更新逻辑，都需要一并检查修复。
