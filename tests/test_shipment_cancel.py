"""
黑盒测试：出库与取消出库库存回补验证

测试目标：
- 验证出库操作是否正确扣减库存
- 验证取消订单（撤销出库）时库存是否正确回补

重点验证Bug：出库后库存扣减成功，撤销出库操作，库存不能自动回补还原。
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestShipmentInventory:
    """出库库存扣减测试"""

    def test_shipment_reduces_inventory(self, sample_quote):
        """场景：正常出库后，批次剩余数量应正确扣减"""
        # 前置：出库前库存
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        batch_before = db_module.get_batches(quote["series"])  # 不对，需要直接查batch
        # 直接查批次剩余
        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 操作：模拟出库（扣减库存 + 更新状态）
        quote_quantity = quote["quote_quantity"]
        conn = db_module.get_connection()
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 验证：库存扣减正确
        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        assert remaining_after == remaining_before - quote_quantity, \
            f"出库后库存应为 {remaining_before - quote_quantity}，实际为 {remaining_after}"

    def test_cancel_shipped_quote_restores_inventory(self, sample_quote):
        """
        场景：已出库订单取消后，库存应回补还原

        预期：取消已出库订单时，应将出库数量加回批次剩余数量
        实际（Bug）：update_quote_status 只改状态，不回补库存
        """
        # 前置：先出库
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        conn = db_module.get_connection()
        remaining_before_ship = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 第一步：出库扣减
        conn = db_module.get_connection()
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 验证出库成功
        conn = db_module.get_connection()
        remaining_after_ship = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()
        assert remaining_after_ship == remaining_before_ship - quote_quantity

        # 第二步：取消订单（调用系统提供的 update_quote_status）
        db_module.update_quote_status(sample_quote, "已取消")

        # 验证：取消后库存应回补到出库前的数量
        conn = db_module.get_connection()
        remaining_after_cancel = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        quote_status = conn.execute(
            "SELECT status FROM quotes WHERE id=?", (sample_quote,)
        ).fetchone()[0]
        conn.close()

        # 状态确实变成"已取消"
        assert quote_status == "已取消", "订单状态应变为已取消"
        # Bug验证：库存没有回补（这是当前系统的实际行为，与预期不符）
        assert remaining_after_cancel == remaining_before_ship, \
            f"取消出库后库存应回补到 {remaining_before_ship}，实际为 {remaining_after_cancel}，库存未回补！"


class TestCancelQuoteEdgeCases:
    """取消订单的边界情况测试"""

    def test_cancel_pending_quote_no_inventory_change(self, sample_quote):
        """场景：取消"待确认"状态的订单，不应影响库存（因为还没出库）"""
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 取消待确认的订单
        db_module.update_quote_status(sample_quote, "已取消")

        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 待确认订单未出库，取消后库存不变
        assert remaining_after == remaining_before

    def test_cancel_quoted_quote_no_inventory_change(self, sample_quote):
        """场景：取消"已报价"状态的订单，不应影响库存（因为还没出库）"""
        # 先改为已报价
        db_module.update_quote_status(sample_quote, "已报价")

        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 取消已报价的订单
        db_module.update_quote_status(sample_quote, "已取消")

        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        # 已报价订单未出库，取消后库存不变
        assert remaining_after == remaining_before

    def test_partial_payment_then_cancel_shipment(self, sample_quote):
        """场景：已出库且有部分收款的订单取消，库存应回补，收款记录应保留或标记"""
        # 先出库
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 部分收款
        db_module.add_payment(
            quote_id=sample_quote,
            customer_id=quote["customer_id"],
            pay_type="receivable",
            amount=3000.0,
            pay_date="2026-06-16",
            method="微信",
            remark="订金"
        )

        # 取消订单
        db_module.update_quote_status(sample_quote, "已取消")

        # 验证库存回补
        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        assert remaining_after == remaining_before, \
            f"有部分收款的出库订单取消后，库存应回补到 {remaining_before}，实际为 {remaining_after}"

    def test_delete_quote_should_restore_inventory(self, sample_quote):
        """场景：删除已出库的报价记录，库存应回补"""
        # 先出库
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 删除报价
        db_module.delete_quote(sample_quote)

        # 验证库存回补
        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        assert remaining_after == remaining_before, \
            f"删除已出库报价后，库存应回补到 {remaining_before}，实际为 {remaining_after}"


class TestShipmentSnManagement:
    """出库SN管理测试"""

    def test_cancel_shipment_should_remove_sn_from_quote(self, sample_quote):
        """场景：取消出库时，报价记录上的SN应被清空或标记为未出库"""
        # 先出库并设置SN
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        conn = db_module.get_connection()
        conn.execute(
            "UPDATE batches SET remaining = remaining - ? WHERE id = ?",
            (quote_quantity, batch_id)
        )
        conn.execute(
            "UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?",
            (sample_quote,)
        )
        conn.commit()
        conn.close()

        # 取消订单
        db_module.update_quote_status(sample_quote, "已取消")

        # 验证：取消后报价SN应被清空（或保留但状态明确标识已取消）
        quote_after = db_module.get_quote_by_id(sample_quote)
        # 至少状态应为已取消
        assert quote_after["status"] == "已取消"
        # SN是否应清空取决于业务设计，这里只记录现状
        # 当前系统行为：SN保留在报价记录中（因为update_quote_status只改状态字段）
