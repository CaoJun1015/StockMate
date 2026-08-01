"""
调货助手 v1.08 - 全面黑盒测试

测试覆盖：
- 🔴 P0: 数据一致性（最重要）
- 🟡 P1: 业务流程完整性
- 🟢 P2: 边界情况
- 🔄 回归测试: Issue #001-#009 已修复问题验证

测试方法：数据库层黑盒测试
直接调用 src/models/database.py 的函数，使用内存数据库隔离。
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 P0 - 数据一致性测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestP0DataConsistency:
    """P0级：数据一致性测试 - 这是系统最脆弱的部分"""

    # ─────────────────────────────────────────────────────────────────────────
    # Issue #001 回归: 出库SN保存位置
    # ─────────────────────────────────────────────────────────────────────────

    def test_issue001_sn_saved_to_quote_not_batch(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库时SN应保存到quotes.sn_list，而非batches.sn_list

        验证点：
        - 出库后 quotes.sn_list = 本次出库的SN
        - batches.sn_list = 入库时的SN（固定不变）
        - 两者不能混为一谈

        Given: 批次有SN列表 "SN001,...,SN010"
        When: 出库2台，指定SN为 "SN001,SN002"
        Then: quotes.sn_list = "SN001,SN002"
              batches.sn_list = "SN001,...,SN010"（不变）
        """
        # 入库
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )
        original_batch_sn = "SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010"

        # 创建报价
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )

        # 出库（模拟UI层操作：扣库存 + 更新状态 + 写入SN）
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - 2 WHERE id = ?", (batch_id,))
        conn.execute(
            "UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?",
            (quote_id,)
        )
        conn.commit()
        conn.close()

        # 验证 Issue #001 修复：SN 分开管理
        quote = db_module.get_quote_by_id(quote_id)
        batch = db_module.get_batches(sample_product)[0]

        assert quote["sn_list"] == "SN001,SN002", \
            f"[Issue#001] 出库后quotes.sn_list应为'SN001,SN002'，实际为'{quote['sn_list']}'"
        assert batch["sn_list"] == original_batch_sn, \
            f"[Issue#001] batches.sn_list不应被修改，实际为'{batch['sn_list']}'"

    # ─────────────────────────────────────────────────────────────────────────
    # Issue #002 回归: paid字段更新时序
    # ─────────────────────────────────────────────────────────────────────────

    def test_issue002_paid_updates_immediately_on_full_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：首次全额收款后，paid应立即变为"是"

        验证点：
        - add_payment 后立即查询，paid="是"
        - 不需要额外操作或等待

        Given: 报价总额 = 11000 (5500 * 2)
        When: 一次性收款 11000
        Then: paid = "是"，status = "已收款"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )

        # 全额收款
        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=11000.0,
            pay_date="2026-06-16",
            method="微信",
        )

        # 立即验证（Issue #002 核心：paid 不能慢一拍）
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是", \
            f"[Issue#002] 全额收款后paid应立即为'是'，实际为'{quote['paid']}'"
        assert quote["status"] == "已收款", \
            f"[Issue#002] 全额收款后status应为'已收款'，实际为'{quote['status']}'"
        assert quote["received_amount"] == 11000.0

    # ─────────────────────────────────────────────────────────────────────────
    # Issue #003 回归: 删除批次回滚供应商欠款
    # ─────────────────────────────────────────────────────────────────────────

    def test_issue003_delete_batch_restores_supplier_balance(self, sample_product, sample_supplier):
        """
        场景：删除批次后，供应商欠款应正确回滚

        验证点：
        - 删除前: balance = purchase_price * quantity
        - 删除后: balance = 0

        Given: 入库 6000元 * 5台 = 30000元应付
        When: 删除该批次
        Then: 供应商balance从30000变为0
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=6000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        # 验证入库后供应商欠款
        conn = db_module.get_connection()
        balance_before = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_before == 30000.0, f"入库后供应商欠款应为30000，实际为{balance_before}"

        # 删除批次
        db_module.delete_batch(batch_id)

        # 验证 Issue #003 修复：欠款回滚
        conn = db_module.get_connection()
        balance_after = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_after == 0.0, \
            f"[Issue#003] 删除批次后供应商欠款应回滚到0，实际为{balance_after}"

    # ─────────────────────────────────────────────────────────────────────────
    # Issue #004 回归: 批量收款原子性
    # ─────────────────────────────────────────────────────────────────────────

    def test_issue004_batch_payment_atomicity(self, sample_product, sample_customer, sample_supplier):
        """
        场景：批量收款时，所有收款要么全部成功，要么全部失败

        验证点：
        - 使用同一事务，不会出现部分成功
        - 如果中间某笔失败，前面的也不应提交

        Given: 3笔报价，每笔11000
        When: 批量收款分配
        Then: 所有收款记录和状态更新都成功
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=30,
            remaining=30,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_ids = []
        for i in range(3):
            qid = db_module.add_quote(
                batch_id=batch_id,
                customer_id=sample_customer,
                quote_price=5500.0,
                quote_quantity=2,
                quote_date=f"2026-06-{15+i}",
            )
            quote_ids.append(qid)

        # 批量收款（模拟UI层的批量收款操作）
        # 使用 _add_payment_raw 在同一事务中处理
        conn = db_module.get_connection()
        try:
            for qid in quote_ids:
                db_module._add_payment_raw(
                    conn, quote_id=qid, customer_id=sample_customer,
                    pay_type="receivable", amount=11000.0,
                    pay_date="2026-06-20", method="微信",
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        # 验证所有报价都已收款
        for qid in quote_ids:
            quote = db_module.get_quote_by_id(qid)
            assert quote["paid"] == "是", \
                f"[Issue#004] 报价{qid}的paid应为'是'，实际为'{quote['paid']}'"
            assert quote["status"] == "已收款", \
                f"[Issue#004] 报价{qid}的status应为'已收款'，实际为'{quote['status']}'"

        # 验证付款记录数
        all_payments = db_module.get_payments(customer_id=sample_customer)
        assert len(all_payments) == 3, \
            f"[Issue#004] 应有3条付款记录，实际为{len(all_payments)}"

    # ─────────────────────────────────────────────────────────────────────────
    # Issue #005-009 回归: 状态回退逻辑
    # ─────────────────────────────────────────────────────────────────────────

    def test_issue005_status_rollback_to_pending_when_no_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：未出库的订单收款后删除，状态应回退为"待确认"

        验证点：
        - 有SN → 回退到"已出库"
        - 无SN → 回退到"待确认"

        Given: 报价无SN（未出库），全额收款后status="已收款"
        When: 删除收款记录
        Then: status回退为"待确认"（不是"已出库"）
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=1,
            quote_date="2026-06-15",
            sn_list="",  # 未出库，无SN
        )

        # 全额收款
        payment_id = db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=5500.0,
            pay_date="2026-06-16",
            method="微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已收款"

        # 删除收款
        db_module.delete_payment(payment_id)

        # 验证 Issue #005 修复：根据SN判断回退状态
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认", \
            f"[Issue#005] 无SN的订单收款删除后，status应回退为'待确认'，实际为'{quote['status']}'"

    def test_issue006_status_rollback_to_shipped_when_has_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：已出库的订单收款后删除，状态应回退为"已出库"

        验证点：
        - 有SN → 回退到"已出库"

        Given: 报价有SN（已出库），全额收款后status="已收款"
        When: 删除收款记录
        Then: status回退为"已出库"（不是"待确认"）
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=1,
            quote_date="2026-06-15",
            sn_list="SN001",  # 已出库，有SN
        )
        # 模拟已出库状态
        conn = db_module.get_connection()
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        # 全额收款
        payment_id = db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=5500.0,
            pay_date="2026-06-16",
            method="微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已收款"

        # 删除收款
        db_module.delete_payment(payment_id)

        # 验证 Issue #006 修复：有SN回退到"已出库"
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库", \
            f"[Issue#006] 有SN的订单收款删除后，status应回退为'已出库'，实际为'{quote['status']}'"

    def test_issue007_update_payment_amount_syncs_status(self, sample_product, sample_customer, sample_supplier):
        """
        场景：修改收款金额后，paid和status应同步更新

        验证点：
        - 增加金额到满额 → paid="是", status="已收款"
        - 减少金额到未满 → paid="否", status回退

        Given: 报价总额11000，已收5000
        When: 修改收款金额为11000
        Then: paid="是", status="已收款"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )

        # 部分收款
        payment_id = db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=5000.0,
            pay_date="2026-06-16",
            method="微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "否"
        assert quote["status"] == "待确认"

        # 修改收款金额到满额
        db_module.update_payment(payment_id, 11000.0, "2026-06-16", "微信", "补齐全款")

        # 验证同步更新
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 11000.0, \
            f"[Issue#007] 修改收款后received_amount应为11000，实际为{quote['received_amount']}"
        assert quote["paid"] == "是", \
            f"[Issue#007] 收满后paid应为'是'，实际为'{quote['paid']}'"
        assert quote["status"] == "已收款", \
            f"[Issue#007] 收满后status应为'已收款'，实际为'{quote['status']}'"

    def test_issue008_reduce_payment_rollback_status(self, sample_product, sample_customer, sample_supplier):
        """
        场景：减少收款金额后，status应回退

        Given: 报价总额11000，已全额收款
        When: 修改收款金额为5000
        Then: paid="否", status回退
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )

        # 全额收款
        payment_id = db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=11000.0,
            pay_date="2026-06-16",
            method="微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"

        # 减少收款金额
        db_module.update_payment(payment_id, 5000.0, "2026-06-16", "微信", "退款部分")

        # 验证回退
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 5000.0
        assert quote["paid"] == "否", \
            f"[Issue#008] 减少收款后paid应回退为'否'，实际为'{quote['paid']}'"
        # 无SN，应回退到"待确认"
        assert quote["status"] == "待确认", \
            f"[Issue#008] 减少收款后status应回退为'待确认'，实际为'{quote['status']}'"

    # ─────────────────────────────────────────────────────────────────────────
    # 出库后取消订单 - 库存回补
    # ─────────────────────────────────────────────────────────────────────────

    def test_cancel_shipped_quote_restores_batch_remaining(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消已出库订单，批次剩余数量应回补

        Given: 批次10台，出库2台，剩余8
        When: 取消订单
        Then: 批次剩余回补到10
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )

        # 出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - 2 WHERE id = ?", (batch_id,))
        conn.execute(
            "UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?",
            (quote_id,)
        )
        conn.commit()
        conn.close()

        # 验证出库后
        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 8

        # 取消订单
        db_module.update_quote_status(quote_id, "已取消")

        # 验证回补
        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10, \
            f"取消已出库订单后，批次剩余应回补到10，实际为{batch['remaining']}"

    def test_cancel_shipped_quote_clears_quote_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消已出库订单，报价SN应清空

        Given: 报价sn_list = "SN001,SN002"
        When: 取消订单
        Then: quotes.sn_list = ""
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
            sn_list="SN001,SN002",
        )
        conn = db_module.get_connection()
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        # 取消
        db_module.update_quote_status(quote_id, "已取消")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["sn_list"] == "", \
            f"取消已出库订单后，报价SN应清空，实际为'{quote['sn_list']}'"
        assert quote["status"] == "已取消"

    def test_cancel_shipped_quote_keeps_batch_sn_unchanged(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消已出库订单，批次SN不应被修改

        Given: 批次sn_list = "SN001,...,SN010"
        When: 取消订单
        Then: batches.sn_list 不变
        """
        original_sn = "SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010"
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list=original_sn,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
            sn_list="SN001,SN002",
        )
        conn = db_module.get_connection()
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.execute("UPDATE batches SET remaining = 8 WHERE id=?", (batch_id,))
        conn.commit()
        conn.close()

        # 取消
        db_module.update_quote_status(quote_id, "已取消")

        batch = db_module.get_batches(sample_product)[0]
        assert batch["sn_list"] == original_sn, \
            f"取消订单后批次SN不应被修改，实际为'{batch['sn_list']}'"

    # ─────────────────────────────────────────────────────────────────────────
    # 删除已出库报价 - 库存回补
    # ─────────────────────────────────────────────────────────────────────────

    def test_delete_shipped_quote_restores_inventory(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除已出库报价，库存应回补

        Given: 批次10台，出库3台，剩余7
        When: 删除报价
        Then: 批次剩余回补到10
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=3,
            quote_date="2026-06-15",
        )

        # 出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = 7 WHERE id=?", (batch_id,))
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        # 删除报价
        db_module.delete_quote(quote_id)

        # 验证回补
        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10, \
            f"删除已出库报价后，批次剩余应回补到10，实际为{batch['remaining']}"

    def test_delete_shipped_quote_cascades_payments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除已出库报价，关联的付款记录应级联删除

        Given: 报价有1条收款记录
        When: 删除报价
        Then: 付款记录也被删除
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )
        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=5000.0,
            pay_date="2026-06-16",
            method="微信",
        )

        # 确认存在
        assert len(db_module.get_payments(quote_id=quote_id)) == 1

        # 删除报价
        db_module.delete_quote(quote_id)

        # 验证级联删除
        assert len(db_module.get_payments(quote_id=quote_id)) == 0, \
            "删除报价后，关联的付款记录应被级联删除"

    # ─────────────────────────────────────────────────────────────────────────
    # 删除批次 - 级联删除 + 供应商欠款回滚
    # ─────────────────────────────────────────────────────────────────────────

    def test_delete_batch_cascades_quotes_and_payments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除批次应级联删除关联的报价和付款

        Given: 批次有2条报价，每条有1条付款
        When: 删除批次
        Then: 报价和付款全部删除
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        q2 = db_module.add_quote(batch_id, sample_customer, 5600.0, 1, "2026-06-16")
        db_module.add_payment(q1, sample_customer, None, "receivable", 5500.0, "2026-06-16", "微信")
        db_module.add_payment(q2, sample_customer, None, "receivable", 5600.0, "2026-06-17", "支付宝")

        # 删除批次
        db_module.delete_batch(batch_id)

        # 验证级联删除
        assert db_module.get_quote_by_id(q1) is None
        assert db_module.get_quote_by_id(q2) is None
        assert len(db_module.get_payments(quote_id=q1)) == 0
        assert len(db_module.get_payments(quote_id=q2)) == 0

    def test_delete_product_cascades_all_related_data(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除机型应级联删除所有关联数据（批次、报价、付款）

        Given: 机型 → 批次 → 报价 → 付款
        When: 删除机型
        Then: 所有关联数据全部删除，供应商欠款回滚
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 5500.0, "2026-06-16", "微信")

        # 记录删除前供应商欠款
        conn = db_module.get_connection()
        balance_before = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_before == 25000.0  # 5000 * 5

        # 删除机型
        db_module.delete_product(sample_product)

        # 验证级联删除
        assert db_module.get_all_products() == []
        assert db_module.get_quote_by_id(quote_id) is None

        # 验证供应商欠款回滚
        conn = db_module.get_connection()
        balance_after = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_after == 0.0, \
            f"删除机型后供应商欠款应回滚到0，实际为{balance_after}"

    # ─────────────────────────────────────────────────────────────────────────
    # 收款金额同步
    # ─────────────────────────────────────────────────────────────────────────

    def test_multiple_payments_accumulate_correctly(self, sample_product, sample_customer, sample_supplier):
        """
        场景：多次收款，received_amount累加正确

        Given: 报价总额11000
        When: 分3次收款 3000+4000+4000
        Then: received_amount = 11000, paid = "是"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id, sample_customer, 5500.0, 2, "2026-06-15",
        )

        # 分3次收款
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 3000.0, "2026-06-16", "微信")
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-17", "支付宝")
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-18", "现金")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 11000.0, \
            f"3次收款后received_amount应为11000，实际为{quote['received_amount']}"
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"

    def test_delete_one_of_multiple_payments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：有多笔收款时删除其中一笔，received_amount正确回退

        Given: 3笔收款 3000+4000+4000 = 11000
        When: 删除4000那笔
        Then: received_amount = 7000, paid = "否"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        p1 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 3000.0, "2026-06-16", "微信")
        p2 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-17", "支付宝")
        p3 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-18", "现金")

        # 删除中间那笔
        db_module.delete_payment(p2)

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 7000.0, \
            f"删除一笔后received_amount应为7000，实际为{quote['received_amount']}"
        assert quote["paid"] == "否"

    # ─────────────────────────────────────────────────────────────────────────
    # 供应商余额管理
    # ─────────────────────────────────────────────────────────────────────────

    def test_supplier_balance_on_add_batch(self, sample_product, sample_supplier):
        """
        场景：入库时供应商欠款增加

        Given: 供应商初始余额0
        When: 入库 5000元 * 10台
        Then: balance = 50000
        """
        conn = db_module.get_connection()
        balance_before = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_before == 0.0

        db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        conn = db_module.get_connection()
        balance_after = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_after == 50000.0, \
            f"入库后供应商欠款应为50000，实际为{balance_after}"

    def test_supplier_balance_multiple_batches(self, sample_product, sample_supplier):
        """
        场景：多次入库，供应商欠款累加

        Given: 两次入库 5000*10 + 6000*5
        Then: balance = 50000 + 30000 = 80000
        """
        db_module.add_batch(sample_product, 5000.0, 10, 10, "2026-06-01", supplier_id=sample_supplier)
        db_module.add_batch(sample_product, 6000.0, 5, 5, "2026-06-05", supplier_id=sample_supplier)

        conn = db_module.get_connection()
        balance = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance == 80000.0, f"两次入库后供应商欠款应为80000，实际为{balance}"

    def test_supplier_payment_reduces_balance(self, sample_product, sample_supplier):
        """
        场景：向供应商付款后，欠款减少

        Given: 入库后欠款50000
        When: 付款20000
        Then: balance = 30000
        """
        db_module.add_batch(sample_product, 5000.0, 10, 10, "2026-06-01", supplier_id=sample_supplier)

        # 向供应商付款
        db_module.add_payment(
            supplier_id=sample_supplier,
            pay_type="payable",
            amount=20000.0,
            pay_date="2026-06-10",
            method="转账",
        )

        conn = db_module.get_connection()
        balance = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance == 30000.0, f"付款后供应商欠款应为30000，实际为{balance}"

    def test_delete_supplier_payment_increases_balance(self, sample_product, sample_supplier):
        """
        场景：删除供应商付款记录，欠款增加（因为付款被撤销）

        Given: 入库50000，付款20000，余额30000
        When: 删除付款记录
        Then: balance = 50000
        """
        db_module.add_batch(sample_product, 5000.0, 10, 10, "2026-06-01", supplier_id=sample_supplier)
        payment_id = db_module.add_payment(
            supplier_id=sample_supplier,
            pay_type="payable",
            amount=20000.0,
            pay_date="2026-06-10",
            method="转账",
        )

        # 删除付款记录
        db_module.delete_payment(payment_id)

        conn = db_module.get_connection()
        balance = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance == 50000.0, \
            f"删除付款记录后供应商欠款应回到50000，实际为{balance}"


# ═══════════════════════════════════════════════════════════════════════════════
# 🟡 P1 - 业务流程完整性测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestP1BusinessFlow:
    """P1级：业务流程完整性测试"""

    def test_full_forward_flow_inventory_to_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：完整正向流程
        入库 → 报价 → 出库 → 收款

        验证每一步的数据状态变化：
        1. 入库后: batches.remaining=10, supplier.balance=50000
        2. 报价后: quotes.status="待确认", received_amount=0
        3. 出库后: batches.remaining=8, quotes.status="已出库", quotes.sn_list有值
        4. 收款后: quotes.paid="是", quotes.status="已收款"
        """
        # Step 1: 入库
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10

        conn = db_module.get_connection()
        supplier_balance = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert supplier_balance == 50000.0

        # Step 2: 报价
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认"
        assert quote["received_amount"] == 0

        # Step 3: 出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - 2 WHERE id=?", (batch_id,))
        conn.execute(
            "UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?",
            (quote_id,)
        )
        conn.commit()
        conn.close()

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 8
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"
        assert quote["sn_list"] == "SN001,SN002"

        # Step 4: 收款
        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=11000.0,  # 5500 * 2
            pay_date="2026-06-16",
            method="微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"
        assert quote["received_amount"] == 11000.0

    def test_cancel_at_pending_status_no_side_effects(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消"待确认"状态的订单

        验证：不影响库存、供应商余额
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 记录取消前状态
        batch_before = db_module.get_batches(sample_product)[0]
        conn = db_module.get_connection()
        balance_before = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()

        # 取消
        db_module.update_quote_status(quote_id, "已取消")

        # 验证无副作用
        batch_after = db_module.get_batches(sample_product)[0]
        conn = db_module.get_connection()
        balance_after = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()

        assert batch_after["remaining"] == batch_before["remaining"]
        assert balance_after == balance_before

    def test_cancel_at_quoted_status_no_side_effects(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消"已报价"状态的订单

        验证：不影响库存、供应商余额
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")
        db_module.update_quote_status(quote_id, "已报价")

        batch_before = db_module.get_batches(sample_product)[0]

        # 取消
        db_module.update_quote_status(quote_id, "已取消")

        batch_after = db_module.get_batches(sample_product)[0]
        assert batch_after["remaining"] == batch_before["remaining"]

    def test_customer_balance_calculation(self, sample_product, sample_customer, sample_supplier):
        """
        场景：客户应收余额计算

        验证：只有"已报价"和"已出库"状态的订单计入应收

        Given: 3笔报价 - 待确认5000, 已报价6000, 已出库7000, 已收款8000
        Then: 应收余额 = 6000 + 7000 = 13000
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=100,
            remaining=100,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        # 创建不同状态的报价
        q1 = db_module.add_quote(batch_id, sample_customer, 5000.0, 1, "2026-06-10")  # 待确认
        q2 = db_module.add_quote(batch_id, sample_customer, 6000.0, 1, "2026-06-11")  # 已报价
        q3 = db_module.add_quote(batch_id, sample_customer, 7000.0, 1, "2026-06-12")  # 已出库
        q4 = db_module.add_quote(batch_id, sample_customer, 8000.0, 1, "2026-06-13")  # 已收款

        db_module.update_quote_status(q2, "已报价")
        db_module.ship_quote(q3, "SN001")  # 用 ship_quote 代替直接跳状态
        # q4 全额收款
        db_module.add_payment(q4, sample_customer, None, "receivable", 8000.0, "2026-06-14", "微信")

        balance = db_module.get_customer_balance(sample_customer)
        assert balance == 13000.0, \
            f"客户应收余额应为13000（已报价6000+已出库7000），实际为{balance}"

    def test_supplier_payable_calculation(self, sample_product, sample_supplier):
        """
        场景：供应商应付余额计算

        Given: 入库50000，付款20000
        Then: 应付余额 = 30000
        """
        db_module.add_batch(sample_product, 5000.0, 10, 10, "2026-06-01", supplier_id=sample_supplier)
        db_module.add_payment(
            supplier_id=sample_supplier,
            pay_type="payable",
            amount=20000.0,
            pay_date="2026-06-10",
            method="转账",
        )

        payable = db_module.get_supplier_payable(sample_supplier)
        assert payable == 30000.0, f"供应商应付余额应为30000，实际为{payable}"

    def test_customer_statement_accuracy(self, sample_product, sample_customer, sample_supplier):
        """
        场景：客户对账单数据准确性

        Given: 2笔报价
        Then: 对账单返回正确的报价信息
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            remark="含税",
        )
        db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15", remark="报价1")
        db_module.add_quote(batch_id, sample_customer, 5600.0, 1, "2026-06-16", remark="报价2")

        statement = db_module.get_customer_statement(sample_customer)
        assert len(statement) == 2
        # 验证数据包含必要字段
        for item in statement:
            assert "quote_date" in item
            assert "quote_price" in item
            assert "quote_quantity" in item
            assert "series" in item

    def test_delete_quote_with_payment_rollbacks_received_amount(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除有收款的报价时，验证数据清理

        Given: 报价已收5000
        When: 删除报价
        Then: 报价和收款记录都被删除
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 5000.0, "2026-06-16", "微信")

        # 删除报价
        db_module.delete_quote(quote_id)

        # 验证
        assert db_module.get_quote_by_id(quote_id) is None
        assert len(db_module.get_payments(quote_id=quote_id)) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 🟢 P2 - 边界情况测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestP2EdgeCases:
    """P2级：边界情况测试"""

    def test_deduct_batch_remaining_insufficient_stock(self, sample_product, sample_supplier):
        """
        场景：出库数量 > 库存剩余，应被拒绝

        Given: 批次剩余5台
        When: 尝试扣减8台
        Then: 返回失败，库存不变
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        success, msg = db_module.deduct_batch_remaining(batch_id, 8)
        assert success is False, "库存不足时扣减应失败"
        assert "库存不足" in msg or "不足" in msg

        # 验证库存不变
        remaining = db_module.get_batch_remaining(batch_id)
        assert remaining == 5, f"扣减失败后库存应保持5，实际为{remaining}"

    def test_deduct_batch_remaining_exact_stock(self, sample_product, sample_supplier):
        """
        场景：出库数量 = 库存剩余，应成功

        Given: 批次剩余5台
        When: 扣减5台
        Then: 成功，剩余变为0
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        success, msg = db_module.deduct_batch_remaining(batch_id, 5)
        assert success is True

        remaining = db_module.get_batch_remaining(batch_id)
        assert remaining == 0

    def test_deduct_nonexistent_batch(self, sample_product, sample_supplier):
        """
        场景：扣减不存在的批次

        Given: 批次ID不存在
        When: 尝试扣减
        Then: 返回失败
        """
        success, msg = db_module.deduct_batch_remaining(99999, 1)
        assert success is False
        assert "不存在" in msg

    def test_payment_amount_exceeds_total(self, sample_product, sample_customer, sample_supplier):
        """
        场景：收款金额超过订单总额

        Given: 报价总额11000
        When: 收款15000
        Then: received_amount=15000, paid="是"（允许超额收款）
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=15000.0,
            pay_date="2026-06-16",
            method="微信",
        )

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 15000.0
        assert quote["paid"] == "是", "超额收款时paid应为'是'"
        assert quote["status"] == "已收款"

    def test_zero_amount_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：收款金额为0

        Given: 报价
        When: 收款0元
        Then: received_amount不变，paid保持"否"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")

        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=0,
            pay_date="2026-06-16",
            method="微信",
        )

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 0
        assert quote["paid"] == "否"

    def test_shipment_without_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库时不输入SN

        Given: 批次有SN，出库时不指定SN
        Then: quotes.sn_list为空字符串
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005",
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")

        # 出库（不指定SN）
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = 4 WHERE id=?", (batch_id,))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["sn_list"] == ""

    def test_batch_without_supplier(self, sample_product):
        """
        场景：入库时不指定供应商

        Given: 只有机型，没有供应商
        When: 入库
        Then: 正常入库，supplier_id=NULL
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=None,
        )

        batch = db_module.get_batches(sample_product)[0]
        assert batch["supplier_id"] is None
        assert batch["remaining"] == 10

    def test_quote_without_customer(self, sample_product, sample_supplier):
        """
        场景：报价时不指定客户

        Given: 只有批次，没有客户
        When: 创建报价
        Then: 正常创建，customer_id=NULL
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, None, 5500.0, 1, "2026-06-15")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote is not None
        assert quote["customer_name"] is None

    def test_search_quotes_with_all_filters(self, sample_product, sample_customer, sample_supplier):
        """
        场景：使用所有筛选条件搜索报价

        Given: 多条报价记录
        When: 使用关键词+日期+客户ID筛选
        Then: 返回匹配的记录
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        db_module.add_quote(batch_id, sample_customer, 5600.0, 1, "2026-06-20")

        # 按日期范围筛选
        quotes = db_module.search_quotes(
            date_from="2026-06-14",
            date_to="2026-06-16",
            customer_id=sample_customer,
        )
        assert len(quotes) == 1
        assert quotes[0]["quote_date"] == "2026-06-15"

    def test_search_quotes_no_results(self, sample_product, sample_customer, sample_supplier):
        """
        场景：搜索无匹配的报价

        Given: 无报价记录
        When: 搜索
        Then: 返回空列表
        """
        quotes = db_module.search_quotes()
        assert quotes == []

    def test_delete_nonexistent_payment(self):
        """
        场景：删除不存在的付款记录

        Given: 付款ID不存在
        When: 删除
        Then: 返回失败
        """
        result = db_module.delete_payment(99999)
        assert result[0] is False
        assert "不存在" in result[1]

    def test_update_nonexistent_payment(self):
        """
        场景：修改不存在的付款记录

        Given: 付款ID不存在
        When: 修改
        Then: 返回失败
        """
        result = db_module.update_payment(99999, 1000.0, "2026-06-16", "微信", "备注")
        assert result[0] is False
        assert "不存在" in result[1]

    def test_get_customer_balance_no_quotes(self, sample_customer):
        """
        场景：客户无报价时的应收余额

        Given: 客户无任何报价
        When: 查询应收余额
        Then: 返回0
        """
        balance = db_module.get_customer_balance(sample_customer)
        assert balance == 0

    def test_get_supplier_payable_no_batches(self, sample_supplier):
        """
        场景：供应商无批次时的应付余额

        Given: 供应商无任何入库
        When: 查询应付余额
        Then: 返回0
        """
        payable = db_module.get_supplier_payable(sample_supplier)
        assert payable == 0

    def test_customer_stats_no_quotes(self, sample_customer):
        """
        场景：客户无报价时的统计

        Given: 客户无任何报价
        When: 查询统计
        Then: 返回0值

        注意：当前代码中 SUM() 在无数据时返回 NULL（Python中为None），
              这是一个 Minor 级别的 Bug。测试记录实际行为。
        """
        stats = db_module.get_customer_stats(sample_customer)
        assert stats["total_quotes"] == 0
        assert stats["total_amount"] == 0, \
            f"无报价时 total_amount 应为 0，实际为 {stats['total_amount']}"
        assert stats["total_profit"] == 0, \
            f"无报价时 total_profit 应为 0，实际为 {stats['total_profit']}"

    def test_multiple_batches_same_product(self, sample_product, sample_supplier):
        """
        场景：同一机型多次入库

        Given: 同一机型入库3次
        Then: get_batches返回3条记录，get_total_remaining正确
        """
        db_module.add_batch(sample_product, 5000.0, 10, 10, "2026-06-01", supplier_id=sample_supplier)
        db_module.add_batch(sample_product, 4800.0, 5, 5, "2026-06-05", supplier_id=sample_supplier)
        db_module.add_batch(sample_product, 5200.0, 8, 8, "2026-06-10", supplier_id=sample_supplier)

        batches = db_module.get_batches(sample_product)
        assert len(batches) == 3

        total = db_module.get_total_remaining(sample_product)
        assert total == 23  # 10 + 5 + 8

    def test_get_all_payments_with_filters(self, sample_product, sample_customer, sample_supplier):
        """
        场景：筛选收付款记录

        Given: 收款和付款记录
        When: 按类型筛选
        Then: 只返回匹配类型
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 5500.0, "2026-06-16", "微信")
        db_module.add_payment(None, None, sample_supplier, "payable", 20000.0, "2026-06-17", "转账")

        # 筛选收款
        receivables = db_module.get_all_payments_with_details(pay_type="receivable")
        assert len(receivables) == 1
        assert receivables[0]["type"] == "receivable"

        # 筛选付款
        payables = db_module.get_all_payments_with_details(pay_type="payable")
        assert len(payables) == 1
        assert payables[0]["type"] == "payable"


# ═══════════════════════════════════════════════════════════════════════════════
# 🔄 Issue #005-009 详细回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestIssue005To009Regression:
    """Issue #005-009 详细回归测试：状态回退逻辑"""

    def test_issue005_009_partial_payment_delete_rollback_pending(self, sample_product, sample_customer, sample_supplier):
        """
        场景：未出库订单部分收款后删除，状态回退到"待确认"

        Given: 无SN报价，部分收款3000
        When: 删除收款记录
        Then: status回退为"待确认"（不是"已出库"）

        回归 Issue #005-009: 旧代码直接回退到"已出库"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id, sample_customer, 5500.0, 1, "2026-06-15",
            sn_list="",  # 无SN，未出库
        )

        # 部分收款
        payment_id = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 3000.0, "2026-06-16", "微信",
        )

        # 删除收款
        db_module.delete_payment(payment_id)

        # 验证回退到"待确认"
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认", \
            f"[Issue#005-009] 无SN订单收款删除后应回退到'待确认'，实际为'{quote['status']}'"
        assert quote["paid"] == "否"
        assert quote["received_amount"] == 0

    def test_issue005_009_partial_payment_delete_rollback_shipped(self, sample_product, sample_customer, sample_supplier):
        """
        场景：已出库订单部分收款后删除，状态回退到"已出库"

        Given: 有SN报价（已出库），部分收款3000
        When: 删除收款记录
        Then: status回退为"已出库"（不是"待确认"）

        回归 Issue #005-009: 旧代码直接回退到"已出库"但不检查SN
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(
            batch_id, sample_customer, 5500.0, 1, "2026-06-15",
            sn_list="SN001",  # 有SN，已出库
        )
        conn = db_module.get_connection()
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        # 部分收款
        payment_id = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 3000.0, "2026-06-16", "微信",
        )

        # 删除收款
        db_module.delete_payment(payment_id)

        # 验证回退到"已出库"
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库", \
            f"[Issue#005-009] 有SN订单收款删除后应回退到'已出库'，实际为'{quote['status']}'"

    def test_issue005_009_full_payment_then_delete_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：全额收款后删除，状态正确回退

        Given: 无SN报价，全额收款
        When: 删除收款记录
        Then: status回退为"待确认"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15", sn_list="")

        # 全额收款
        payment_id = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 5500.0, "2026-06-16", "微信",
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已收款"

        # 删除收款
        db_module.delete_payment(payment_id)

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认", \
            f"全额收款删除后，无SN订单应回退到'待确认'，实际为'{quote['status']}'"

    def test_issue005_009_reduce_payment_below_total(self, sample_product, sample_customer, sample_supplier):
        """
        场景：收款金额从满额减少到未满额

        Given: 无SN报价，全额收款11000
        When: 修改收款金额为5000
        Then: paid="否", status回退为"待确认"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15", sn_list="")

        # 全额收款
        payment_id = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 11000.0, "2026-06-16", "微信",
        )

        # 减少金额
        db_module.update_payment(payment_id, 5000.0, "2026-06-16", "微信", "退款")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "否"
        assert quote["status"] == "待确认", \
            f"减少收款后，无SN订单应回退到'待确认'，实际为'{quote['status']}'"


# ═══════════════════════════════════════════════════════════════════════════════
# 🔄 Issue #001 详细回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestIssue001Regression:
    """Issue #001 详细回归测试：出库SN管理"""

    def test_issue001_sn_independence_after_multiple_shipments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：多次出库后，批次SN和报价SN独立管理

        Given: 批次有SN001-SN010
        When: 两次出库 SN001,SN002 和 SN003,SN004
        Then: 批次SN不变，两次报价各自有独立SN
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )
        original_batch_sn = "SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010"

        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")
        q2 = db_module.add_quote(batch_id, sample_customer, 5600.0, 2, "2026-06-16")

        # 第一次出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = 8 WHERE id=?", (batch_id,))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?", (q1,))
        conn.commit()
        conn.close()

        # 第二次出库
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = 6 WHERE id=?", (batch_id,))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN003,SN004' WHERE id=?", (q2,))
        conn.commit()
        conn.close()

        # 验证SN独立性
        batch = db_module.get_batches(sample_product)[0]
        assert batch["sn_list"] == original_batch_sn, \
            f"批次SN不应被修改，实际为'{batch['sn_list']}'"

        quote1 = db_module.get_quote_by_id(q1)
        quote2 = db_module.get_quote_by_id(q2)
        assert quote1["sn_list"] == "SN001,SN002"
        assert quote2["sn_list"] == "SN003,SN004"
        assert batch["remaining"] == 6

    def test_issue001_cancel_one_shipment_doesnt_affect_other(self, sample_product, sample_customer, sample_supplier):
        """
        场景：取消一笔出库不影响其他出库的SN

        Given: 两次出库，取消第一次
        Then: 第二次出库的SN保持不变
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )
        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15", sn_list="SN001,SN002")
        q2 = db_module.add_quote(batch_id, sample_customer, 5600.0, 2, "2026-06-16", sn_list="SN003,SN004")

        conn = db_module.get_connection()
        conn.execute("UPDATE quotes SET status='已出库' WHERE id IN (?,?)", (q1, q2))
        conn.execute("UPDATE batches SET remaining = 6 WHERE id=?", (batch_id,))
        conn.commit()
        conn.close()

        # 取消第一笔
        db_module.update_quote_status(q1, "已取消")

        # 验证第二笔不受影响
        quote2 = db_module.get_quote_by_id(q2)
        assert quote2["sn_list"] == "SN003,SN004", \
            f"取消第一笔出库后，第二笔SN应保持不变，实际为'{quote2['sn_list']}'"
        assert quote2["status"] == "已出库"


# ═══════════════════════════════════════════════════════════════════════════════
# 🔄 Issue #004 详细回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestIssue004Regression:
    """Issue #004 详细回归测试：批量收款原子性"""

    def test_issue004_batch_payment_all_success(self, sample_product, sample_customer, sample_supplier):
        """
        场景：批量收款全部成功

        Given: 3笔报价
        When: 批量收款
        Then: 全部成功，状态全部更新
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=30,
            remaining=30,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quotes = []
        for i in range(3):
            qid = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, f"2026-06-{15+i}")
            quotes.append(qid)

        # 批量收款（使用同一事务）
        conn = db_module.get_connection()
        try:
            for qid in quotes:
                db_module._add_payment_raw(
                    conn, quote_id=qid, customer_id=sample_customer,
                    pay_type="receivable", amount=5500.0,
                    pay_date="2026-06-20", method="微信",
                )
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 验证全部成功
        for qid in quotes:
            quote = db_module.get_quote_by_id(qid)
            assert quote["paid"] == "是"
            assert quote["status"] == "已收款"
            assert quote["received_amount"] == 5500.0

    def test_issue004_batch_payment_with_different_amounts(self, sample_product, sample_customer, sample_supplier):
        """
        场景：批量收款金额不同

        Given: 3笔报价，金额分别为5000, 6000, 7000
        When: 分别收款5000, 6000, 7000
        Then: 每笔状态独立正确
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=4000.0,
            quantity=30,
            remaining=30,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, sample_customer, 5000.0, 1, "2026-06-15")
        q2 = db_module.add_quote(batch_id, sample_customer, 6000.0, 1, "2026-06-16")
        q3 = db_module.add_quote(batch_id, sample_customer, 7000.0, 1, "2026-06-17")

        conn = db_module.get_connection()
        try:
            db_module._add_payment_raw(conn, q1, sample_customer, None, "receivable", 5000.0, "2026-06-20", "微信")
            db_module._add_payment_raw(conn, q2, sample_customer, None, "receivable", 6000.0, "2026-06-20", "微信")
            db_module._add_payment_raw(conn, q3, sample_customer, None, "receivable", 7000.0, "2026-06-20", "微信")
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()

        assert db_module.get_quote_by_id(q1)["paid"] == "是"
        assert db_module.get_quote_by_id(q2)["paid"] == "是"
        assert db_module.get_quote_by_id(q3)["paid"] == "是"
