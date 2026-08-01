"""
OPT-001 测试用例

场景：update_quote_status 应校验状态流转合法性
当前Bug：允许任意跳转，如"已收款"→"待确认"、"已取消"→任意状态
修复：定义合法状态流转表，非法跳转返回 (False, msg)
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestOpt001_StatusMachineGuard:
    """状态机守卫测试"""

    def test_valid_transition_quoted_to_shipped(self, sample_quote):
        """
        场景：待确认 → 已报价（合法）
        预期：成功
        """
        result = db_module.update_quote_status(sample_quote, "已报价")
        assert result == (True, "状态更新成功")
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已报价"

    def test_valid_transition_quoted_to_cancelled(self, sample_quote):
        """
        场景：待确认 → 已取消（合法）
        预期：成功
        """
        result = db_module.update_quote_status(sample_quote, "已取消")
        assert result == (True, "状态更新成功")

    def test_valid_transition_shipped_to_cancelled(self, sample_quote):
        """
        场景：待确认 → 已报价 → 已出库 → 已取消（合法链路）
        预期：全部成功，且出库取消时库存回补
        """
        db_module.update_quote_status(sample_quote, "已报价")
        db_module.update_quote_status(sample_quote, "已出库")
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        result = db_module.update_quote_status(sample_quote, "已取消")
        assert result == (True, "状态更新成功")

        # 验证库存回补
        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()
        assert remaining_after == remaining_before + quote["quote_quantity"]

    def test_invalid_transition_received_to_quoted(self, sample_quote):
        """
        场景：已收款 → 待确认（非法，已收款不可变更）
        预期：被拒绝，返回 False
        """
        # 先推到已收款
        db_module.update_quote_status(sample_quote, "已报价")
        db_module.update_quote_status(sample_quote, "已出库")
        quote = db_module.get_quote_by_id(sample_quote)
        total = quote["quote_price"] * quote["quote_quantity"]
        db_module.add_payment(
            quote_id=sample_quote,
            customer_id=quote["customer_id"],
            pay_type="receivable",
            amount=total,
            pay_date="2026-06-16",
            method="微信"
        )

        # 尝试非法跳转
        result = db_module.update_quote_status(sample_quote, "待确认")
        assert result == (False, '不允许从「已收款」变更为「待确认」')

    def test_invalid_transition_cancelled_to_quoted(self, sample_quote):
        """
        场景：已取消 → 已报价（非法，已取消不可变更）
        预期：被拒绝，返回 False
        """
        db_module.update_quote_status(sample_quote, "已取消")
        result = db_module.update_quote_status(sample_quote, "已报价")
        assert result == (False, '不允许从「已取消」变更为「已报价」')

    def test_invalid_transition_quoted_to_received(self, sample_quote):
        """
        场景：待确认 → 已收款（非法，跳过了已报价和已出库）
        预期：被拒绝
        """
        result = db_module.update_quote_status(sample_quote, "已收款")
        assert result == (False, '不允许从「待确认」变更为「已收款」')

    def test_nonexistent_quote_returns_false(self):
        """
        场景：更新不存在的报价ID
        预期：返回 False
        """
        result = db_module.update_quote_status(99999, "已取消")
        assert result == (False, "报价记录不存在")
