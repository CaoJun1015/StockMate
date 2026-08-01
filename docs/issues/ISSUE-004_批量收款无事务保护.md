# ISSUE-004: 批量收款无事务保护

## 严重程度：🔴 高

## 状态：已修复 ✅

## 问题描述

客户一次性收款分配到多个欠款订单时（`_on_finance_receive()`），循环调用 `add_payment()` 逐笔分配到各个订单。但每个 `add_payment()` 内部是一个独立的事务（各自 `conn.commit()`）。如果循环中途某一笔失败，前几次已经提交，后面的不会执行，导致**部分收款成功、部分失败**，客户实际到账金额与系统记录不一致。

## 复现步骤

1. 客户有 3 个欠款订单，各欠 3000 元
2. 客户一次性付款 9000 元
3. `_on_finance_receive()` 循环分配：
   - 第 1 笔：add_payment(quote1, 3000) → commit ✓
   - 第 2 笔：add_payment(quote2, 3000) → commit ✓
   - 第 3 笔：add_payment(quote3, 3000) → **假设此处失败**（如磁盘满）
4. 查询结果：quote1 和 quote2 已收款，quote3 未收款
5. 客户实际付了 9000，但系统只记录了 6000，且客户余额可能仍显示欠款 3000

## 期望行为

批量收款应该在一个事务中完成：全部成功或全部失败，不能出现中间状态。

## 实际行为

每笔收款是独立事务，中间失败会导致数据不一致。

## 根因分析

**位置**：`src/main.py` 第 1800-1815 行 `_on_finance_receive()`

```python
def _on_finance_receive(self):
    # ... 获取数据和验证 ...
    for qid, amount in allocations:
        add_payment(
            quote_id=qid,
            customer_id=customer_id,
            pay_type="receivable",
            amount=amount,
            pay_date=pay_date,
            method=method,
            remark=remark,
        )
    QMessageBox.information(self, "成功", f"收款 ¥{total:.2f} 已分配到 {len(allocations)} 笔订单！")
```

`add_payment()` 的实现（`database.py` 第 702-727 行）：

```python
def add_payment(...):
    conn = get_connection()
    conn.execute("INSERT INTO payments ...")
    if quote_id and pay_type == "receivable":
        conn.execute("UPDATE quotes SET received_amount ...")
        # ...
    conn.commit()  # ← 每笔都 commit
    conn.close()
```

**问题**：`add_payment` 每次都会 `get_connection()` → 操作 → `commit()` → `close()`。`_on_finance_receive` 循环调用时，每笔都是独立连接和事务。

## 修复方案

### 方案A：重构 `add_payment` 支持外部连接（推荐）

将 `add_payment` 拆分为纯数据库操作函数（接受连接对象）和包装函数：

```python
def _add_payment_raw(conn, quote_id, customer_id, supplier_id, pay_type, amount, pay_date, method, remark):
    """内部函数，不管理连接和事务，依赖外部传入的 conn"""
    conn.execute("INSERT INTO payments ...")
    if quote_id and pay_type == "receivable":
        conn.execute("UPDATE quotes SET received_amount ...")
        # ...
    # 不 commit，不 close

def add_payment(...):
    """对外接口，保持原有行为"""
    conn = get_connection()
    try:
        _add_payment_raw(conn, ...)
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
```

然后在 `_on_finance_receive` 中：

```python
def _on_finance_receive(self):
    conn = get_connection()
    try:
        for qid, amount in allocations:
            _add_payment_raw(conn, quote_id=qid, ...)
        conn.commit()
        QMessageBox.information(self, "成功", ...)
    except Exception as e:
        conn.rollback()
        QMessageBox.critical(self, "失败", f"收款失败: {str(e)}")
    finally:
        conn.close()
```

### 方案B：在 `_on_finance_receive` 中统一回滚

如果方案A改动太大，可以至少在 `_on_finance_receive` 中增加补偿逻辑：出错时删除已创建的 payment 记录并回滚 received_amount。但这复杂且容易出错。

**推荐方案A**，因为这是根本解决方式。

## 测试要求

1. 创建 3 个已出库未收款的报价记录
2. 模拟批量收款，分配金额到 3 个订单
3. 验证：3 个订单都正确收到分配金额
4. 如果中途模拟失败（如通过 monkeypatch），验证所有订单状态不变（原子性）

## 相关文件

- `src/main.py`：`_on_finance_receive()`
- `src/models/database.py`：`add_payment()`

## 备注

这个修复涉及函数式 API 的重构（引入内部函数）。如果项目有其他地方也需要类似的事务控制（如批量付款给供应商），这个重构可以同时解决。
