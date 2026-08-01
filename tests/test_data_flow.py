"""
黑盒测试：完整业务流程数据流验证

验证从入库到出库到收款的全流程中，各表数据的一致性。
覆盖正向流程、逆向流程（取消/删除）、以及级联影响。
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


class TestFullBusinessFlow:
    """完整业务流程数据流测试"""

    def test_flow_inventory_to_shipment_to_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：完整正向流程
        入库(10台) → 报价(2台) → 出库 → 收款 → 验证各表数据一致性
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
            remark="含税"
        )
        conn = db_module.get_connection()
        supplier_balance = conn.execute("SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)).fetchone()[0]
        conn.close()
        assert supplier_balance == 50000.0, f"入库后供应商欠款应为50000，实际为{supplier_balance}"

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10
        assert batch["sn_list"] == "SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010"

        # Step 2: 报价
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=2,
            quote_date="2026-06-15",
            remark="测试报价"
        )
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认"
        assert quote["received_amount"] == 0
        # paid 默认为空字符串，视为未付款
        assert quote["paid"] in ("", "否"), f"新建报价paid应为空或'否'，实际为'{quote['paid']}'"

        # Step 3: 出库（模拟UI层的出库操作）
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (2, batch_id))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 8, f"出库后批次剩余应为8，实际为{batch['remaining']}"
        # 批次SN不应被修改
        assert batch["sn_list"] == "SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010"

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"
        assert quote["sn_list"] == "SN001,SN002", f"出库后报价SN应为SN001,SN002，实际为{quote['sn_list']}"

        # Step 4: 收款（全额）
        db_module.add_payment(
            quote_id=quote_id,
            customer_id=sample_customer,
            pay_type="receivable",
            amount=11000.0,  # 5500 * 2
            pay_date="2026-06-16",
            method="微信",
            remark=""
        )

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 11000.0
        assert quote["paid"] == "是", f"全额收款后paid应为'是'，实际为'{quote['paid']}'"
        assert quote["status"] == "已收款"

        # Step 5: 验证付款记录
        payments = db_module.get_payments(quote_id=quote_id)
        assert len(payments) == 1
        assert payments[0]["amount"] == 11000.0

    def test_cancel_shipped_quote_restores_all(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库后取消订单，验证数据完整回退
        """
        # 前置：入库 → 报价 → 出库 → 部分收款
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=5000.0,
            quantity=10,
            remaining=10,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003",
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
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (2, batch_id))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='SN001,SN002' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()
        # 部分收款
        db_module.add_payment(quote_id=quote_id, customer_id=sample_customer, pay_type="receivable",
                              amount=5000.0, pay_date="2026-06-16", method="微信")

        # 状态确认
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"  # 未收满，保持已出库
        assert quote["received_amount"] == 5000.0
        assert quote["sn_list"] == "SN001,SN002"

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 8

        # 执行取消
        db_module.update_quote_status(quote_id, "已取消")

        # 验证回退
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已取消"
        assert quote["sn_list"] == "", f"取消后报价SN应清空，实际为'{quote['sn_list']}'"
        # received_amount 和 paid 保持原样（取消操作不改动已收金额）
        assert quote["received_amount"] == 5000.0

        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10, f"取消后批次剩余应回补到10，实际为{batch['remaining']}"
        # 批次SN不应被修改
        assert batch["sn_list"] == "SN001,SN002,SN003"

    def test_delete_shipped_quote_restores_inventory(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除已出库的报价，验证库存回补
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
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (3, batch_id))
        conn.execute("UPDATE quotes SET status='已出库' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        db_module.delete_quote(quote_id)

        # 验证报价已删除
        assert db_module.get_quote_by_id(quote_id) is None

        # 验证库存回补
        batch = db_module.get_batches(sample_product)[0]
        assert batch["remaining"] == 10, f"删除后批次剩余应回补到10，实际为{batch['remaining']}"

        # 验证付款记录级联删除
        payments = db_module.get_payments(quote_id=quote_id)
        assert len(payments) == 0


class TestCascadeDeletionConsistency:
    """级联删除数据一致性测试"""

    def test_delete_batch_restores_supplier_balance(self, sample_product, sample_supplier):
        """
        场景：删除批次后供应商欠款应回滚
        """
        batch_id = db_module.add_batch(
            product_id=sample_product,
            purchase_price=6000.0,
            quantity=5,
            remaining=5,
            date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        conn = db_module.get_connection()
        supplier_balance = conn.execute("SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)).fetchone()[0]
        conn.close()
        assert supplier_balance == 30000.0

        db_module.delete_batch(batch_id)

        conn = db_module.get_connection()
        supplier_balance = conn.execute("SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)).fetchone()[0]
        conn.close()
        assert supplier_balance == 0.0, f"删除批次后供应商欠款应回滚到0，实际为{supplier_balance}"

    def test_delete_batch_cascades_quotes_and_payments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除批次应级联删除关联的报价和付款
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
        db_module.add_payment(quote_id=quote_id, customer_id=sample_customer, pay_type="receivable",
                              amount=5000.0, pay_date="2026-06-16", method="微信")

        # 验证存在
        assert db_module.get_quote_by_id(quote_id) is not None
        assert len(db_module.get_payments(quote_id=quote_id)) == 1

        db_module.delete_batch(batch_id)

        # 验证级联删除
        assert db_module.get_quote_by_id(quote_id) is None
        assert len(db_module.get_payments(quote_id=quote_id)) == 0


class TestPaymentDataConsistency:
    """收款数据一致性测试"""

    def test_full_payment_sets_paid_and_status(self, sample_quote):
        """
        场景：全额收款后 paid='是' 且 status='已收款'
        """
        quote = db_module.get_quote_by_id(sample_quote)
        total = quote["quote_price"] * quote["quote_quantity"]

        db_module.add_payment(quote_id=sample_quote, customer_id=quote["customer_id"],
                              pay_type="receivable", amount=total,
                              pay_date="2026-06-16", method="微信")

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["paid"] == "是", f"全额收款后paid应为'是'，实际为'{quote['paid']}'"
        assert quote["status"] == "已收款", f"全额收款后status应为'已收款'，实际为'{quote['status']}'"
        assert quote["received_amount"] == total

    def test_partial_payment_keeps_status(self, sample_quote):
        """
        场景：部分收款后 status 不变，paid='否'
        """
        quote = db_module.get_quote_by_id(sample_quote)
        total = quote["quote_price"] * quote["quote_quantity"]
        partial = total / 2

        db_module.add_payment(quote_id=sample_quote, customer_id=quote["customer_id"],
                              pay_type="receivable", amount=partial,
                              pay_date="2026-06-16", method="微信")

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["paid"] == "否", f"部分收款后paid应为'否'，实际为'{quote['paid']}'"
        assert quote["status"] == "待确认", f"部分收款后status应保持'待确认'，实际为'{quote['status']}'"
        assert quote["received_amount"] == partial

    def test_multiple_payments_accumulate(self, sample_quote):
        """
        场景：多次收款，received_amount 累加正确
        """
        quote = db_module.get_quote_by_id(sample_quote)
        total = quote["quote_price"] * quote["quote_quantity"]

        db_module.add_payment(quote_id=sample_quote, customer_id=quote["customer_id"],
                              pay_type="receivable", amount=total / 3,
                              pay_date="2026-06-16", method="微信")
        db_module.add_payment(quote_id=sample_quote, customer_id=quote["customer_id"],
                              pay_type="receivable", amount=total / 3,
                              pay_date="2026-06-17", method="支付宝")

        quote = db_module.get_quote_by_id(sample_quote)
        # v1.13 stores each individual receipt in integer cents, so each
        # installment is rounded before accumulation.
        expected = round(round(total / 3, 2) * 2, 2)
        assert round(quote["received_amount"], 2) == expected, \
            f"两次收款后received_amount应为{expected}，实际为{quote['received_amount']}"

    def test_delete_payment_restores_received_amount(self, sample_quote):
        """
        场景：删除收款记录后 received_amount 回退
        """
        quote = db_module.get_quote_by_id(sample_quote)

        payment_id = db_module.add_payment(quote_id=sample_quote, customer_id=quote["customer_id"],
                                           pay_type="receivable", amount=3000.0,
                                           pay_date="2026-06-16", method="微信")

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["received_amount"] == 3000.0

        db_module.delete_payment(payment_id)

        quote = db_module.get_quote_by_id(sample_quote)
        assert quote["received_amount"] == 0.0, \
            f"删除收款后received_amount应回退到0，实际为{quote['received_amount']}"


class TestEdgeCases:
    """边界情况数据流测试"""

    def test_shipment_without_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库时不输入SN，quotes.sn_list应为空字符串
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
        quote_id = db_module.add_quote(
            batch_id=batch_id,
            customer_id=sample_customer,
            quote_price=5500.0,
            quote_quantity=1,
            quote_date="2026-06-15",
        )
        # 出库（不输入SN）
        conn = db_module.get_connection()
        conn.execute("UPDATE batches SET remaining = remaining - ? WHERE id = ?", (1, batch_id))
        conn.execute("UPDATE quotes SET status='已出库', sn_list='' WHERE id=?", (quote_id,))
        conn.commit()
        conn.close()

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["sn_list"] == ""
        # 批次SN保持不变
        batch = db_module.get_batches(sample_product)[0]
        assert batch["sn_list"] == "SN001,SN002,SN003,SN004,SN005"

    def test_cancel_pending_quote_no_side_effects(self, sample_quote):
        """
        场景：取消待确认订单，不应影响任何库存/余额数据
        """
        quote_before = db_module.get_quote_by_id(sample_quote)
        batch_id = quote_before["batch_id"]

        conn = db_module.get_connection()
        batch_before = conn.execute("SELECT remaining, sn_list FROM batches WHERE id=?", (batch_id,)).fetchone()
        conn.close()

        db_module.update_quote_status(sample_quote, "已取消")

        conn = db_module.get_connection()
        batch_after = conn.execute("SELECT remaining, sn_list FROM batches WHERE id=?", (batch_id,)).fetchone()
        conn.close()

        assert batch_after["remaining"] == batch_before["remaining"]
        assert batch_after["sn_list"] == batch_before["sn_list"]
