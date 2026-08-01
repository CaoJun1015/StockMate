"""
OPT-002 测试用例

场景：出库逻辑应封装到 database.py 的 ship_quote() 函数中
当前问题：出库逻辑散落在 main.py 的 on_ship_quote() 中，用裸 SQL 实现
修复目标：新增 ship_quote(quote_id, sn_list) 函数，封装校验+扣减+保存SN+状态更新
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestOpt002_ShipQuoteEncapsulation:
    """出库逻辑封装测试"""

    def test_ship_quote_success(self, sample_quote):
        """
        场景：正常出库，库存充足，状态为待确认
        预期：出库成功，库存扣减，SN保存到报价，状态变为已出库
        """
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        conn = db_module.get_connection()
        remaining_before = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()

        success, msg = db_module.ship_quote(sample_quote, "SN001,SN002")

        assert success, f"出库应成功，实际失败: {msg}"
        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["status"] == "已出库"
        assert quote["sn_list"] == "SN001,SN002"

        conn = db_module.get_connection()
        remaining_after = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()
        assert remaining_after == remaining_before - quote_quantity

    def test_ship_quote_insufficient_stock(self, sample_quote):
        """
        场景：出库数量超过库存剩余
        预期：出库失败，库存不变
        """
        quote = db_module.get_quote_by_id(sample_quote)
        batch_id = quote["batch_id"]

        # 手动把库存改到1
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining=1 WHERE id=?", (batch_id,))
        conn.commit()
        conn.close()

        success, msg = db_module.ship_quote(sample_quote, "SN001,SN002")
        assert not success
        assert "库存不足" in msg

        # 库存应不变
        conn = db_module.get_connection()
        remaining = conn.execute(
            "SELECT remaining FROM batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        conn.close()
        assert remaining == 1

    def test_ship_quote_wrong_status_already_shipped(self, sample_quote):
        """
        场景：已出库的订单不能再次出库
        预期：出库失败
        """
        db_module.ship_quote(sample_quote, "SN001")

        success, msg = db_module.ship_quote(sample_quote, "SN003")
        assert not success
        assert "不允许出库" in msg

    def test_ship_quote_wrong_status_cancelled(self, sample_quote):
        """
        场景：已取消的订单不能出库
        预期：出库失败
        """
        db_module.update_quote_status(sample_quote, "已取消")

        success, msg = db_module.ship_quote(sample_quote, "SN001")
        assert not success
        assert "不允许出库" in msg

    def test_ship_quote_nonexistent(self):
        """
        场景：不存在的报价ID
        预期：出库失败
        """
        success, msg = db_module.ship_quote(99999, "SN001")
        assert not success
        assert "不存在" in msg

    def test_ship_quote_from_quoted_status(self, sample_quote):
        """
        场景：已报价状态也可以出库
        预期：出库成功
        """
        db_module.update_quote_status(sample_quote, "已报价")
        success, msg = db_module.ship_quote(sample_quote, "SN001,SN002")
        assert success
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已出库"
