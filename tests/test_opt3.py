"""
OPT-003 测试用例

场景：收款删除/减少后，状态回退应保留原状态
根因：当前 _update_quote_payment_status 在未收满时，根据 sn_list 判断回退到"已出库"或"待确认"
      但如果原状态是"已报价"，回退后应保持"已报价"而非变为"待确认"
修复：未收满时保持当前状态不变，只有从"已收款"回退时才根据 sn_list 判断
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestOpt003_StatusRollbackPreservesOriginal:
    """状态回退应保留原状态"""

    def test_quoted_partial_payment_delete_preserves_quoted(self, sample_quote):
        """
        场景：已报价 → 部分收款 → 删除收款，应回退到"已报价"而非"待确认"
        预期：status 保持"已报价"
        """
        quote = db_module.get_quote_by_id(sample_quote)
        customer_id = quote["customer_id"]

        # 设为已报价
        db_module.update_quote_status(sample_quote, "已报价")
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已报价"

        # 部分收款
        pid = db_module.add_payment(
            quote_id=sample_quote,
            customer_id=customer_id,
            pay_type="receivable",
            amount=1000.0,
            pay_date="2026-06-16",
            method="微信"
        )

        # 删除收款
        db_module.delete_payment(pid)

        # 验证：应回到"已报价"，不是"待确认"
        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["status"] == "已报价", \
            f"删除收款后 status 应保持'已报价'，实际为'{quote['status']}'"
        assert quote["received_amount"] == 0
        assert quote["paid"] == "否"

    def test_shipped_partial_payment_delete_preserves_shipped(self, sample_quote):
        """
        场景：已出库 → 部分收款 → 删除收款，应保持"已出库"
        预期：status 保持"已出库"
        """
        quote = db_module.get_quote_by_id(sample_quote)
        customer_id = quote["customer_id"]
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]

        # 出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (quote_quantity, batch_id))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 部分收款
        pid = db_module.add_payment(
            quote_id=sample_quote,
            customer_id=customer_id,
            pay_type="receivable",
            amount=1000.0,
            pay_date="2026-06-16",
            method="微信"
        )

        # 删除收款
        db_module.delete_payment(pid)

        # 验证：应保持"已出库"
        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["status"] == "已出库", \
            f"删除收款后 status 应保持'已出库'，实际为'{quote['status']}'"

    def test_received_refund_drops_to_shipped(self, sample_quote):
        """
        场景：已出库 → 全额收款(已收款) → 修改收款金额减少 → 应回退到"已出库"
        预期：从"已收款"回退时，根据 sn_list 判断到"已出库"
        """
        quote = db_module.get_quote_by_id(sample_quote)
        customer_id = quote["customer_id"]
        batch_id = quote["batch_id"]
        quote_quantity = quote["quote_quantity"]
        total = quote["quote_price"] * quote_quantity

        # 出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (quote_quantity, batch_id))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?", (sample_quote,))
        conn.commit()
        conn.close()

        # 全额收款 → 已收款
        pid = db_module.add_payment(
            quote_id=sample_quote,
            customer_id=customer_id,
            pay_type="receivable",
            amount=total,
            pay_date="2026-06-16",
            method="微信"
        )
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已收款"

        # 修改收款金额减少
        db_module.update_payment(pid, total / 2, "2026-06-16", "微信", "")

        # 验证：应回退到"已出库"（因为有 sn_list）
        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["status"] == "已出库", \
            f"减少收款后 status 应回退到'已出库'，实际为'{quote['status']}'"
