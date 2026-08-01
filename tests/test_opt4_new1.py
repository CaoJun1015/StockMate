"""
OPT-004 + NEW-001 测试用例

OPT-004: 清理 _update_quote_payment_status 中的 `or True` 调试代码
        修复后，只有 paid 或 status 真正变化时才执行 UPDATE

NEW-001: get_customer_stats 在客户无报价时返回 0 而非 None
         SUM() 在无匹配行时返回 NULL，需用 COALESCE 处理
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestOpt004_UpdatePaymentStatusNoOrTrue:
    """
    场景：_update_quote_payment_status 应该只在 paid 或 status 真正变化时才更新
    修复目标：去掉 `or True`，分开更新 paid 和 status
    """

    def test_paid_updates_when_amount_changes(self, sample_quote):
        """
        场景：收款后 paid 应从空变为"否"
        预期：_update_quote_payment_status 正确更新 paid 字段
        """
        quote = db_module.get_quote_by_id(sample_quote)
        # 初始 paid 为空
        assert quote["paid"] in ("", None)

        # 收一笔（不够全额）
        db_module.add_payment(
            quote_id=sample_quote,
            customer_id=quote["customer_id"],
            pay_type="receivable",
            amount=1000.0,
            pay_date="2026-06-16",
            method="微信"
        )

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["paid"] == "否", "部分收款后 paid 应为'否'"

    def test_status_preserved_when_not_fully_paid(self, sample_quote):
        """
        场景：部分收款后 status 应保持不变（不自动回退）
        预期：status 仍为"待确认"
        """
        # 先设为已报价
        db_module.update_quote_status(sample_quote, "已报价")

        db_module.add_payment(
            quote_id=sample_quote,
            customer_id=db_module.get_quote_by_id(sample_quote)["customer_id"],
            pay_type="receivable",
            amount=1000.0,
            pay_date="2026-06-16",
            method="微信"
        )

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["status"] == "已报价", \
            f"部分收款后 status 应保持'已报价'，实际为'{quote['status']}'"

    def test_status_upgrades_to_received_when_fully_paid(self, sample_quote):
        """
        场景：全额收款后 status 应变为"已收款"，paid 应为"是"
        预期：两个字段同步正确更新
        """
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

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"


class TestNew001_CustomerStatsReturnsZeroNotNone:
    """
    场景：新客户没有任何报价记录时，get_customer_stats 应返回 0 而非 None
    根因：SQL SUM() 在无匹配行时返回 NULL，Python 转为 None
    修复：使用 COALESCE(SUM(...), 0)
    """

    def test_new_customer_stats_returns_zero(self, sample_customer):
        """
        场景：刚创建的客户（无报价记录）查询统计信息
        预期：total_amount 和 total_profit 应为 0，不是 None
        """
        stats = db_module.get_customer_stats(sample_customer)

        assert stats is not None, "get_customer_stats 不应返回 None"
        assert stats["total_quotes"] == 0, \
            f"新客户 total_quotes 应为 0，实际为 {stats['total_quotes']}"
        assert stats["total_amount"] == 0, \
            f"新客户 total_amount 应为 0，实际为 {stats['total_amount']}"
        assert stats["total_profit"] == 0, \
            f"新客户 total_profit 应为 0，实际为 {stats['total_profit']}"

    def test_customer_stats_with_quotes_returns_correct_values(self, sample_quote, sample_customer):
        """
        场景：有报价记录的客户查询统计信息
        预期：total_amount 和 total_profit 计算正确
        """
        quote = db_module.get_quote_by_id(sample_quote)
        expected_amount = quote["quote_price"] * quote["quote_quantity"]  # 5500 * 2 = 11000
        # 利润 = (5500 - 5000) * 2 = 1000
        expected_profit = (quote["quote_price"] - 5000.0) * quote["quote_quantity"]

        stats = db_module.get_customer_stats(sample_customer)

        assert stats["total_quotes"] == 1
        assert stats["total_amount"] == expected_amount, \
            f"total_amount 应为 {expected_amount}，实际为 {stats['total_amount']}"
        assert stats["total_profit"] == expected_profit, \
            f"total_profit 应为 {expected_profit}，实际为 {stats['total_profit']}"
