# ISSUE-001: 出库SN未保存到报价记录

## 严重程度：🔴 高

## 状态：已修复 ✅

## 问题描述

出库时，用户输入的 SN 码只被追加到了 `batches.sn_list`（批次表），**没有保存到 `quotes.sn_list`（报价记录）**。这导致报价记录无法关联到实际出库的 SN，后续取消出库时也无法正确回退 SN。

## 复现步骤

1. 创建一个机型，添加一个批次（含 SN: SN001,SN002,SN003）
2. 创建一条报价记录，状态为"待确认"
3. 点击"出库"，在出库对话框中输入 SN: SN001
4. 确认出库
5. 查询报价记录：`SELECT sn_list FROM quotes WHERE id=?`
6. 查询批次记录：`SELECT sn_list FROM batches WHERE id=?`

## 期望行为

- `quotes.sn_list` = `SN001`（记录本次出库的SN）
- `batches.sn_list` = `SN001,SN002,SN003`（保持入库时的SN不变）
- `batches.remaining` = 原始数量 - 1

## 实际行为

- `quotes.sn_list` = `''`（空！出库SN未保存）
- `batches.sn_list` = `SN001,SN002,SN003,SN001`（错误！出库SN被追加到批次）
- `batches.remaining` = 原始数量 - 1 ✓

## 根因分析

**位置**：`src/main.py` 第 1311-1316 行 `on_ship_quote()`

```python
# 追加SN到批次 ← 这是错的！
if data.get("sn_list"):
    existing_sn = conn.execute("SELECT sn_list FROM batches WHERE id=?", ...).fetchone()
    new_sn = data["sn_list"]
    if existing_sn and existing_sn[0]:
        new_sn = existing_sn[0] + "," + new_sn
    conn.execute("UPDATE batches SET sn_list=? WHERE id=?", (new_sn, data["batch_id"]))

conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
# ↑ 只改了状态，没有更新 quotes.sn_list
```

**设计意图混淆**：
- `batches.sn_list` 应该保存**入库时**的所有 SN（固定不变）
- `quotes.sn_list` 应该保存**本次出库**的 SN
- 但代码把出库 SN 追加到了批次表，完全是方向性错误

## 修复方案

### 修改 `on_ship_quote()`（`src/main.py`）

1. 出库 SN **保存到 `quotes.sn_list`**，不要修改 `batches.sn_list`
2. 同时更新 `quotes.status` 为"已出库"

```python
# 正确的逻辑：
conn.execute(
    "UPDATE quotes SET status='已出库', sn_list=? WHERE id=?",
    (data.get("sn_list", ""), quote_id)
)
# batches.sn_list 不应该被修改
```

## 测试用例

文件：`tests/test_shipment_cancel.py::TestShipmentSnManagement::test_cancel_shipment_should_remove_sn_from_quote`

```python
def test_cancel_shipment_should_remove_sn_from_quote(self, sample_quote):
    """场景：取消出库时，报价记录上的SN应被清空"""
    # 先出库并设置SN
    # 取消订单
    # 验证：取消后报价SN应被清空
    # 批次SN应保持不变
```

## 相关Issue

- ISSUE-006: 取消出库时未清空 `quotes.sn_list`
- ISSUE-007: `update_quote_status` 和 `delete_quote` 中 SN 回退逻辑基于错误前提
