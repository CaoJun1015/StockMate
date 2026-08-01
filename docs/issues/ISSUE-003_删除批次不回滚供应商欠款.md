# ISSUE-003: 删除批次不回滚供应商欠款

## 严重程度：🔴 高

## 状态：已修复 ✅

## 问题描述

`add_batch()` 在创建批次时，如果指定了 `supplier_id`，会增加供应商的 `balance`（欠款金额，即 `purchase_price * quantity`）。但 `delete_batch()` 删除批次时，只级联删除了关联的报价和付款记录，**没有将对应金额从供应商欠款中减去**。导致删除批次后，供应商欠款虚增。

## 复现步骤

1. 创建一个供应商
2. 创建一个机型
3. 添加一个批次：purchase_price=5000, quantity=10, supplier_id=供应商ID
4. 查询供应商 balance：`SELECT balance FROM suppliers WHERE id=?` → 应显示 50000
5. 删除该批次
6. 再次查询供应商 balance：`SELECT balance FROM suppliers WHERE id=?` → 仍显示 50000

## 期望行为

删除批次后，供应商 balance 应减去该批次的总金额（purchase_price * quantity）。

## 实际行为

供应商 balance 不变，欠款虚增。

## 根因分析

**位置**：`src/models/database.py` 第 391-399 行 `delete_batch()`

```python
def delete_batch(batch_id):
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM quotes WHERE batch_id=?", (batch_id,))
    conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
```

`add_batch()` 的实现（第 325-339 行）：

```python
def add_batch(...):
    conn = get_connection()
    conn.execute("INSERT INTO batches ...")
    if supplier_id:
        total = purchase_price * quantity
        conn.execute("UPDATE suppliers SET balance = balance + ? WHERE id=?", (total, supplier_id))
    conn.commit()
    conn.close()
```

**缺失的对称操作**：`add_batch` 增加了 `balance`，但 `delete_batch` 没有减少 `balance`。

## 修复方案

在 `delete_batch()` 中，删除批次前，先查询该批次的 `supplier_id`、`purchase_price`、`quantity`，计算总金额，然后回滚供应商 `balance`：

```python
def delete_batch(batch_id):
    conn = get_connection()
    try:
        # 先查询批次信息，用于回滚供应商欠款
        row = conn.execute(
            "SELECT supplier_id, purchase_price, quantity FROM batches WHERE id=?", (batch_id,)
        ).fetchone()

        conn.execute("PRAGMA foreign_keys = OFF")

        # 回滚供应商欠款
        if row and row[0]:
            total = row[1] * row[2]
            conn.execute(
                "UPDATE suppliers SET balance = balance - ? WHERE id=?",
                (total, row[0])
            )

        conn.execute("DELETE FROM quotes WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
```

## 测试要求

1. 创建供应商（balance=0）
2. 添加批次（price=5000, quantity=10, supplier_id=...）
3. 验证供应商 balance = 50000
4. 删除批次
5. 验证供应商 balance = 0

## 相关文件

- `src/models/database.py`：`add_batch()`、`delete_batch()`
- `tests/test_database.py`：TestBatchCRUD

## 备注

同样的逻辑缺失也存在于 `delete_product()`（级联删除批次时）。应确保 `delete_product` 也正确回滚所有关联批次的供应商欠款。
