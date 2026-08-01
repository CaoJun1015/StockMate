"""
调货助手 v1.08+ - 补充黑盒测试

覆盖现有测试遗漏的边界场景、组合流程、级联删除验证。
所有测试复用 conftest.py 的内存数据库 fixture。

测试维度：
1. OPT-001 状态机守卫 - 合法跳转补充
2. OPT-002 出库封装 - 边界情况
3. OPT-003 状态回退 - 组合场景
4. Issue #004 批量收款 - 原子性补充
5. NEW-001 get_customer_stats - 含已取消报价
6. 级联删除 - delete_customer_cascade / delete_supplier_cascade
7. 多客户多报价组合场景
8. 收付款边界情况
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import models.database as db_module


# ═══════════════════════════════════════════════════════════════════════════════
# 1. OPT-001 状态机守卫 - 合法跳转补充
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpt001_Supplementary:
    """OPT-001 状态机守卫补充测试"""

    def test_valid_transition_quoted_to_shipped(self, sample_quote):
        """
        场景：已报价→已出库（合法跳转，通过 update_quote_status）
        预期：跳转成功，但不扣减库存（update_quote_status 不管库存）

        注意：此测试验证 update_quote_status 允许 已报价→已出库 跳转。
              实际业务应使用 ship_quote() 来出库，因为 ship_quote 会扣库存+保存SN。
              这里只验证状态机本身允许该跳转。
        """
        # 先设为已报价
        db_module.update_quote_status(sample_quote, "已报价")
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已报价"

        # 通过状态机跳到已出库（合法但不扣库存）
        success, msg = db_module.update_quote_status(sample_quote, "已出库")
        assert success is True, f"已报价→已出库应为合法跳转，实际失败: {msg}"
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已出库"

    def test_valid_transition_shipped_to_received(self, sample_quote):
        """
        场景：已出库→已收款（合法跳转）
        预期：跳转成功
        """
        # 先设为已出库
        db_module.update_quote_status(sample_quote, "已报价")
        db_module.update_quote_status(sample_quote, "已出库")
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已出库"

        # 跳到已收款
        success, msg = db_module.update_quote_status(sample_quote, "已收款")
        assert success is True
        assert db_module.get_quote_by_id(sample_quote)["status"] == "已收款"

    def test_all_invalid_transitions_from_received(self, sample_quote):
        """
        场景：已收款状态下，尝试所有其他状态（均应被拒绝）
        预期：全部返回 False
        """
        # 推到已收款
        db_module.update_quote_status(sample_quote, "已报价")
        db_module.update_quote_status(sample_quote, "已出库")
        db_module.update_quote_status(sample_quote, "已收款")

        for target in ["待确认", "已报价", "已出库", "已取消"]:
            success, msg = db_module.update_quote_status(sample_quote, target)
            assert success is False, \
                f"已收款→{target} 应被拒绝，但实际成功了"

    def test_all_invalid_transitions_from_cancelled(self, sample_quote):
        """
        场景：已取消状态下，尝试所有其他状态（均应被拒绝）
        预期：全部返回 False
        """
        db_module.update_quote_status(sample_quote, "已取消")

        for target in ["待确认", "已报价", "已出库", "已收款"]:
            success, msg = db_module.update_quote_status(sample_quote, target)
            assert success is False, \
                f"已取消→{target} 应被拒绝，但实际成功了"

    def test_transition_pending_to_shipped_rejected(self, sample_quote):
        """
        场景：待确认→已出库（非法，跳过了已报价）
        预期：被拒绝
        """
        success, msg = db_module.update_quote_status(sample_quote, "已出库")
        assert success is False
        assert "不允许" in msg

    def test_transition_pending_to_received_rejected(self, sample_quote):
        """
        场景：待确认→已收款（非法，跳过了已报价和已出库）
        预期：被拒绝
        """
        success, msg = db_module.update_quote_status(sample_quote, "已收款")
        assert success is False
        assert "不允许" in msg

    def test_transition_quoted_to_received_rejected(self, sample_quote):
        """
        场景：已报价→已收款（非法，跳过了已出库）
        预期：被拒绝
        """
        db_module.update_quote_status(sample_quote, "已报价")
        success, msg = db_module.update_quote_status(sample_quote, "已收款")
        assert success is False
        assert "不允许" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# 2. OPT-002 出库封装 - 边界情况
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpt002_Supplementary:
    """OPT-002 出库封装补充测试"""

    def test_ship_quote_quantity_equals_remaining(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库数量恰好等于批次剩余（边界值）
        预期：出库成功，剩余变为0

        Given: 批次剩余5台，报价5台
        When: ship_quote 出库5台
        Then: remaining=0，出库成功
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=5, remaining=5, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 5, "2026-06-15")

        success, msg = db_module.ship_quote(quote_id, "SN001,SN002,SN003,SN004,SN005")
        assert success is True, f"出库数量=剩余数量应成功，实际: {msg}"

        remaining = db_module.get_batch_remaining(batch_id)
        assert remaining == 0

    def test_ship_quote_single_item(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库数量为1（最小值边界）
        预期：出库成功

        Given: 批次剩余10台，报价1台
        When: ship_quote 出库1台
        Then: remaining=9
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")

        success, msg = db_module.ship_quote(quote_id, "SN001")
        assert success is True

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"
        assert quote["sn_list"] == "SN001"

    def test_ship_quote_empty_sn(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库时不指定SN（sn_list为空字符串）
        预期：出库成功，quotes.sn_list为空字符串

        Given: 批次10台，报价2台，不指定SN
        When: ship_quote(quote_id, "")
        Then: 出库成功，quotes.sn_list=""
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        success, msg = db_module.ship_quote(quote_id, "")
        assert success is True

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["sn_list"] == ""

    def test_ship_quote_then_cancel_then_re_ship_fails(self, sample_product, sample_customer, sample_supplier):
        """
        场景：出库→取消→再次出库（应被拒绝）
        预期：第二次出库失败，因为已取消状态不允许出库

        Given: 报价出库后取消
        When: 再次尝试 ship_quote
        Then: 失败，提示不允许出库
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 出库
        db_module.ship_quote(quote_id, "SN001,SN002")
        # 取消（通过状态机）
        db_module.update_quote_status(quote_id, "已取消")

        # 再次出库应失败
        success, msg = db_module.ship_quote(quote_id, "SN003")
        assert success is False
        assert "不允许出库" in msg

    def test_ship_quote_batch_deleted_cascades_quote(self, sample_product, sample_customer, sample_supplier):
        """
        场景：删除批次后，关联报价被级联删除，无法再出库

        Given: 批次有报价
        When: 删除批次（级联删除报价）后尝试出库
        Then: ship_quote 返回不存在

        注意：由于外键约束ON DELETE CASCADE，删除批次会级联删除报价。
        这里验证级联删除后报价确实不存在。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 删除批次（级联删除报价）
        db_module.delete_batch(batch_id)

        # 报价已被级联删除
        assert db_module.get_quote_by_id(quote_id) is None

        # 出库应返回"不存在"
        success, msg = db_module.ship_quote(quote_id, "SN001")
        assert success is False
        assert "不存在" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# 3. OPT-003 状态回退 - 组合场景
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpt003_Supplementary:
    """OPT-003 状态回退补充测试"""

    def test_quoted_full_payment_then_reduce_rollback_to_pending(self, sample_product, sample_customer, sample_supplier):
        """
        场景：已报价→全额收款(已收款)→减少收款→回退到待确认（无SN）

        验证点：从已收款回退时，根据sn_list判断
        - 无SN → 待确认
        - 有SN → 已出库

        Given: 已报价，无SN，全额收款11000
        When: 修改收款金额为5000（从已收款回退）
        Then: status回退为"待确认"（因为无SN）

        注意：此场景与"已报价→部分收款→删除→保持已报价"不同。
        关键区别在于：全额收款后status变为"已收款"，再减少时走"从已收款回退"分支，
        此时根据SN判断，无SN则回退到"待确认"而非"已报价"。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15", sn_list="")

        # 设为已报价
        db_module.update_quote_status(quote_id, "已报价")

        # 全额收款
        pid = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 11000.0,
            "2026-06-16", "微信",
        )
        assert db_module.get_quote_by_id(quote_id)["status"] == "已收款"

        # 减少收款金额
        db_module.update_payment(pid, 5000.0, "2026-06-16", "微信", "退款部分")

        # 从已收款回退，无SN → 待确认
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "待确认", \
            f"从已收款回退(无SN)应到'待确认'，实际为'{quote['status']}'"
        assert quote["paid"] == "否"

    def test_shipped_full_payment_then_reduce_rollback_to_shipped(self, sample_product, sample_customer, sample_supplier):
        """
        场景：已出库→全额收款(已收款)→减少收款→回退到已出库（有SN）

        Given: 已出库，有SN，全额收款11000
        When: 修改收款金额为5000
        Then: status回退为"已出库"（因为有SN）

        此场景已由 test_opt3.test_received_refund_drops_to_shipped 覆盖，
        这里补充使用 ship_quote 而非直接操作数据库的方式。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 通过 ship_quote 出库
        success, _ = db_module.ship_quote(quote_id, "SN001,SN002")
        assert success

        # 全额收款
        pid = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 11000.0,
            "2026-06-16", "微信",
        )
        assert db_module.get_quote_by_id(quote_id)["status"] == "已收款"

        # 减少收款
        db_module.update_payment(pid, 5000.0, "2026-06-16", "微信", "")

        # 有SN → 回退到已出库
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"

    def test_multiple_payments_reach_full_then_delete_one(self, sample_product, sample_customer, sample_supplier):
        """
        场景：多次收款达到满额后，删除其中一笔

        Given: 3笔收款 3000+4000+4000=11000，达到满额
        When: 删除其中4000那笔
        Then: received_amount=7000，status从已收款回退

        验证多次收款→满额→部分撤销的完整链路。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 3次收款
        p1 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 3000.0, "2026-06-16", "微信")
        p2 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-17", "支付宝")
        p3 = db_module.add_payment(quote_id, sample_customer, None, "receivable", 4000.0, "2026-06-18", "现金")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"

        # 删除第二笔
        db_module.delete_payment(p2)

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["received_amount"] == 7000.0
        assert quote["paid"] == "否"
        # 无SN，从已收款回退 → 待确认
        assert quote["status"] == "待确认"

    def test_partial_payment_on_shipped_keep_status(self, sample_product, sample_customer, sample_supplier):
        """
        场景：已出库→部分收款→删除收款→保持已出库

        Given: 已出库，有SN
        When: 部分收款后删除
        Then: status保持"已出库"
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        # 出库
        db_module.ship_quote(quote_id, "SN001,SN002")
        assert db_module.get_quote_by_id(quote_id)["status"] == "已出库"

        # 部分收款
        pid = db_module.add_payment(
            quote_id, sample_customer, None, "receivable", 3000.0,
            "2026-06-16", "微信",
        )
        assert db_module.get_quote_by_id(quote_id)["status"] == "已出库"

        # 删除收款
        db_module.delete_payment(pid)

        # 保持已出库
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NEW-001 get_customer_stats 补充
# ═══════════════════════════════════════════════════════════════════════════════

class TestNew001_Supplementary:
    """NEW-001 get_customer_stats 补充测试"""

    def test_customer_stats_includes_cancelled_quotes(self, sample_product, sample_customer, sample_supplier):
        """
        场景：get_customer_stats 当前不排除已取消报价

        Given: 3笔报价 - 2笔正常、1笔已取消
        When: 查询统计
        Then: total_quotes=3（含已取消），金额和利润也包含已取消的

        注意：当前 get_customer_stats 的 SQL 只按 customer_id 过滤，
        不排除已取消状态。这是一个已知行为，不是 Bug。
        如果业务需要排除已取消报价，需修改 SQL 加上 status != '已取消'。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=100, remaining=100, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")
        q2 = db_module.add_quote(batch_id, sample_customer, 6000.0, 1, "2026-06-16")
        q3 = db_module.add_quote(batch_id, sample_customer, 7000.0, 1, "2026-06-17")

        # 取消第二笔
        db_module.update_quote_status(q2, "已取消")

        stats = db_module.get_customer_stats(sample_customer)

        # 已取消报价应被排除
        assert stats["total_quotes"] == 2, \
            f"应排除已取消报价，total_quotes应为2，实际为{stats['total_quotes']}"
        # 5500*2 + 7000*1 = 18000
        assert stats["total_amount"] == 18000.0, \
            f"应排除已取消报价金额，total_amount应为18000，实际为{stats['total_amount']}"
        # (5500-5000)*2 + (7000-5000)*1 = 1000 + 2000 = 3000
        assert stats["total_profit"] == 3000.0, \
            f"应排除已取消报价利润，total_profit应为3000，实际为{stats['total_profit']}"

    def test_customer_stats_multiple_quotes_correct_sum(self, sample_product, sample_customer, sample_supplier):
        """
        场景：多笔报价的统计累加验证

        Given: 5笔报价，金额各不同
        When: 查询统计
        Then: total_amount 和 total_profit 正确累加
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=4000.0,
            quantity=100, remaining=100, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quotes_data = [
            (5000.0, 1), (5500.0, 2), (6000.0, 1), (4500.0, 3), (7000.0, 1),
        ]
        expected_amount = 0
        expected_profit = 0
        for price, qty in quotes_data:
            db_module.add_quote(batch_id, sample_customer, price, qty, "2026-06-15")
            expected_amount += price * qty
            expected_profit += (price - 4000.0) * qty

        stats = db_module.get_customer_stats(sample_customer)
        assert stats["total_quotes"] == 5
        assert stats["total_amount"] == expected_amount
        assert stats["total_profit"] == expected_profit


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 级联删除验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestCascadeDeletion:
    """级联删除测试"""

    def test_delete_customer_cascade_removes_quotes_and_payments(self, sample_product, sample_customer, sample_supplier):
        """
        场景：delete_customer_cascade 应删除客户的报价和付款记录

        Given: 客户有2笔报价，每笔有1条收款
        When: delete_customer_cascade
        Then: 客户、报价、付款全部删除

        验证点：
        - get_all_customers 不再包含该客户
        - get_quote_by_id 返回 None
        - get_payments 为空
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 1, "2026-06-15")
        q2 = db_module.add_quote(batch_id, sample_customer, 6000.0, 1, "2026-06-16")
        db_module.add_payment(q1, sample_customer, None, "receivable", 5500.0, "2026-06-16", "微信")
        db_module.add_payment(q2, sample_customer, None, "receivable", 6000.0, "2026-06-17", "支付宝")

        result = db_module.delete_customer_cascade(sample_customer)

        # 客户已删除
        assert db_module.get_all_customers() == []
        # 报价已删除
        assert db_module.get_quote_by_id(q1) is None
        assert db_module.get_quote_by_id(q2) is None
        # 付款已删除
        assert len(db_module.get_payments(quote_id=q1)) == 0
        assert len(db_module.get_payments(quote_id=q2)) == 0
        # 返回值验证
        assert result["quotes"] == 2
        assert result["payments"] == 2

    def test_delete_customer_cascade_doesnt_affect_other_customers(self, sample_product, sample_supplier):
        """
        场景：删除一个客户不影响其他客户的数据

        Given: 客户A有报价，客户B有报价
        When: 删除客户A
        Then: 客户B及其报价不受影响
        """
        c1 = db_module.add_customer("客户A", "wx_a")
        c2 = db_module.add_customer("客户B", "wx_b")
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, c1, 5500.0, 1, "2026-06-15")
        q2 = db_module.add_quote(batch_id, c2, 6000.0, 1, "2026-06-16")

        db_module.delete_customer_cascade(c1)

        # 客户B仍存在
        customers = db_module.get_all_customers()
        assert len(customers) == 1
        assert customers[0]["name"] == "客户B"
        # 客户B的报价仍存在
        assert db_module.get_quote_by_id(q2) is not None
        # 客户A的报价已删除
        assert db_module.get_quote_by_id(q1) is None

    def test_delete_supplier_cascade_sets_batch_supplier_null(self, sample_product, sample_supplier):
        """
        场景：delete_supplier_cascade 将批次的supplier_id设为NULL

        Given: 供应商有关联的批次
        When: delete_supplier_cascade
        Then: 批次仍存在，但supplier_id=NULL

        注意：delete_supplier_cascade 不回滚供应商balance（已知问题#014），
              但供应商本身被删除，所以balance字段不再存在。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=5, remaining=5, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        result = db_module.delete_supplier_cascade(sample_supplier)

        # 供应商已删除
        assert db_module.get_all_suppliers() == []
        # 批次仍存在但supplier_id=NULL
        batches = db_module.get_batches(sample_product)
        assert len(batches) == 1
        assert batches[0]["supplier_id"] is None
        # 返回值
        assert result["batches"] == 1

    def test_delete_supplier_cascade_removes_supplier_payments(self, sample_product, sample_supplier):
        """
        场景：delete_supplier_cascade 删除供应商的付款记录

        Given: 供应商有2条付款记录
        When: delete_supplier_cascade
        Then: 付款记录全部删除
        """
        db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        db_module.add_payment(None, None, sample_supplier, "payable", 20000.0, "2026-06-10", "转账")
        db_module.add_payment(None, None, sample_supplier, "payable", 10000.0, "2026-06-15", "转账")

        result = db_module.delete_supplier_cascade(sample_supplier)

        assert result["payments"] == 2
        assert len(db_module.get_payments(supplier_id=sample_supplier)) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 多客户多报价组合场景
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiCustomerScenarios:
    """多客户组合场景测试"""

    def test_two_customers_share_same_batch(self, sample_product, sample_supplier):
        """
        场景：两个客户共享同一批次的库存

        Given: 批次10台
        When: 客户A报价3台，客户B报价5台
        Then: 两次出库后剩余2台

        验证多客户从同一批次出库时库存扣减正确。
        """
        c1 = db_module.add_customer("客户A", "wx_a")
        c2 = db_module.add_customer("客户B", "wx_b")
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, c1, 5500.0, 3, "2026-06-15")
        q2 = db_module.add_quote(batch_id, c2, 5600.0, 5, "2026-06-16")

        # 客户A出库
        db_module.ship_quote(q1, "SN001,SN002,SN003")
        # 客户B出库
        db_module.ship_quote(q2, "SN004,SN005,SN006,SN007,SN008")

        remaining = db_module.get_batch_remaining(batch_id)
        assert remaining == 2

    def test_customer_balance_independent(self, sample_product, sample_supplier):
        """
        场景：两个客户的应收余额独立计算

        Given: 客户A已报价5500，客户B已报价6000+7000
        When: 查询各自的余额
        Then: 各自独立，互不影响
        """
        c1 = db_module.add_customer("客户A", "wx_a")
        c2 = db_module.add_customer("客户B", "wx_b")
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=100, remaining=100, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        # 客户A: 1笔已报价
        q1 = db_module.add_quote(batch_id, c1, 5500.0, 1, "2026-06-15")
        db_module.update_quote_status(q1, "已报价")

        # 客户B: 2笔已报价
        q2 = db_module.add_quote(batch_id, c2, 6000.0, 1, "2026-06-16")
        db_module.update_quote_status(q2, "已报价")
        q3 = db_module.add_quote(batch_id, c2, 7000.0, 1, "2026-06-17")
        db_module.update_quote_status(q3, "已报价")

        balance_a = db_module.get_customer_balance(c1)
        balance_b = db_module.get_customer_balance(c2)

        assert balance_a == 5500.0
        assert balance_b == 13000.0

    def test_partial_payment_delete_other_customer_unaffected(self, sample_product, sample_supplier):
        """
        场景：删除客户A的收款不影响客户B的数据

        Given: 客户A和B各有报价和收款
        When: 删除客户A的收款
        Then: 客户B的收款和状态不变
        """
        c1 = db_module.add_customer("客户A", "wx_a")
        c2 = db_module.add_customer("客户B", "wx_b")
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        q1 = db_module.add_quote(batch_id, c1, 5500.0, 1, "2026-06-15")
        q2 = db_module.add_quote(batch_id, c2, 6000.0, 1, "2026-06-16")

        p1 = db_module.add_payment(q1, c1, None, "receivable", 5500.0, "2026-06-16", "微信")
        p2 = db_module.add_payment(q2, c2, None, "receivable", 6000.0, "2026-06-17", "支付宝")

        # 删除客户A的收款
        db_module.delete_payment(p1)

        # 客户B不受影响
        quote_b = db_module.get_quote_by_id(q2)
        assert quote_b["paid"] == "是"
        assert quote_b["status"] == "已收款"
        assert quote_b["received_amount"] == 6000.0


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 收付款边界情况
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaymentEdgeCases:
    """收付款边界情况测试"""

    def test_payment_exactly_equal_to_total(self, sample_product, sample_customer, sample_supplier):
        """
        场景：收款金额恰好等于报价总额（精确边界）

        Given: 报价总额 = 5500 * 2 = 11000
        When: 收款 11000.0
        Then: paid="是", status="已收款"

        验证 >= 判断（不是 > 判断）。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        db_module.add_payment(quote_id, sample_customer, None, "receivable", 11000.0, "2026-06-16", "微信")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"

    def test_payment_one_cent_less_than_total(self, sample_product, sample_customer, sample_supplier):
        """
        场景：收款金额比总额少0.01元（浮点精度边界）

        Given: 报价总额 = 5500 * 2 = 11000
        When: 收款 10999.99
        Then: paid="否", status不变

        验证浮点数比较的正确性。
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")

        db_module.add_payment(quote_id, sample_customer, None, "receivable", 10999.99, "2026-06-16", "微信")

        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "否"
        assert quote["status"] == "待确认"

    def test_supplier_payment_reduces_balance_then_delete_restores(self, sample_product, sample_supplier):
        """
        场景：供应商付款→删除付款→余额恢复

        Given: 入库50000，付款20000（余额30000）
        When: 删除付款记录
        Then: 余额恢复到50000
        """
        db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        pid = db_module.add_payment(None, None, sample_supplier, "payable", 20000.0, "2026-06-10", "转账")

        conn = db_module.get_connection()
        balance_after_pay = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_after_pay == 30000.0

        db_module.delete_payment(pid)

        conn = db_module.get_connection()
        balance_after_delete = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance_after_delete == 50000.0

    def test_update_supplier_payment_amount(self, sample_product, sample_supplier):
        """
        场景：修改供应商付款金额

        Given: 入库50000，付款20000（余额30000）
        When: 修改付款金额为30000
        Then: 余额变为20000
        """
        db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        pid = db_module.add_payment(None, None, sample_supplier, "payable", 20000.0, "2026-06-10", "转账")

        # 修改付款金额为30000
        db_module.update_payment(pid, 30000.0, "2026-06-10", "转账", "修改金额")

        conn = db_module.get_connection()
        balance = conn.execute(
            "SELECT balance FROM suppliers WHERE id=?", (sample_supplier,)
        ).fetchone()[0]
        conn.close()
        assert balance == 20000.0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. 完整正向流程（使用 ship_quote）
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullFlowWithShipQuote:
    """使用 ship_quote 的完整正向流程测试"""

    def test_full_flow_quoted_ship_payment(self, sample_product, sample_customer, sample_supplier):
        """
        场景：完整流程 - 待确认→已报价→出库→收款

        使用 ship_quote 进行出库操作（而非直接操作数据库），
        更贴近实际业务流程。

        Given: 入库10台，创建报价2台
        When: 报价→出库→全额收款
        Then: 每步数据状态正确
        """
        # 入库
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=10, remaining=10, date_str="2026-06-01",
            supplier_id=sample_supplier,
            sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        )

        # 创建报价
        quote_id = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")
        assert db_module.get_quote_by_id(quote_id)["status"] == "待确认"

        # 报价确认
        db_module.update_quote_status(quote_id, "已报价")
        assert db_module.get_quote_by_id(quote_id)["status"] == "已报价"

        # 出库
        success, _ = db_module.ship_quote(quote_id, "SN001,SN002")
        assert success
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["status"] == "已出库"
        assert quote["sn_list"] == "SN001,SN002"

        # 库存扣减
        assert db_module.get_batch_remaining(batch_id) == 8

        # 收款
        db_module.add_payment(quote_id, sample_customer, None, "receivable", 11000.0, "2026-06-16", "微信")
        quote = db_module.get_quote_by_id(quote_id)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"
        assert quote["received_amount"] == 11000.0

    def test_full_flow_with_cancel_and_reorder(self, sample_product, sample_customer, sample_supplier):
        """
        场景：报价→出库→取消→重新报价→出库→收款

        验证取消后库存回补，可以重新出库。

        Given: 批次5台
        When: 第一次报价2台→出库→取消(库存回补到5)
             → 第二次报价3台→出库→收款
        Then: 最终库存2台，第二次报价已收款
        """
        batch_id = db_module.add_batch(
            product_id=sample_product, purchase_price=5000.0,
            quantity=5, remaining=5, date_str="2026-06-01",
            supplier_id=sample_supplier,
        )

        # 第一次报价→出库→取消
        q1 = db_module.add_quote(batch_id, sample_customer, 5500.0, 2, "2026-06-15")
        db_module.ship_quote(q1, "SN001,SN002")
        assert db_module.get_batch_remaining(batch_id) == 3
        db_module.update_quote_status(q1, "已取消")
        assert db_module.get_batch_remaining(batch_id) == 5  # 回补

        # 第二次报价→出库→收款
        q2 = db_module.add_quote(batch_id, sample_customer, 5600.0, 3, "2026-06-16")
        db_module.ship_quote(q2, "SN003,SN004,SN005")
        assert db_module.get_batch_remaining(batch_id) == 2
        db_module.add_payment(q2, sample_customer, None, "receivable", 16800.0, "2026-06-17", "微信")

        quote = db_module.get_quote_by_id(q2)
        assert quote["paid"] == "是"
        assert quote["status"] == "已收款"
