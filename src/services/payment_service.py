"""Immutable payment ledger, FIFO allocation, correction, and reversal."""

from __future__ import annotations

from dataclasses import dataclass

from src.models.connection import transaction
from src.models.repositories import (
    add_allocation,
    audit,
    insert_payment,
    sync_quote_payment_state,
)
from src.services.exceptions import DataConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class PaymentReceipt:
    payment_id: int
    allocated_cents: int


class PaymentService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def _allocate_customer_payment(
        self,
        conn,
        payment_id: int,
        customer_id: int,
        amount_cents: int,
    ) -> None:
        quotes = conn.execute(
            "SELECT id, quote_price_cents, quote_quantity, received_amount_cents "
            "FROM quotes WHERE customer_id=? AND deleted_at IS NULL "
            "AND status IN ('待确认','已报价','已出库') "
            "AND quote_price_cents*quote_quantity>received_amount_cents "
            "ORDER BY quote_date,id",
            (customer_id,),
        ).fetchall()
        available = sum(
            row["quote_price_cents"] * row["quote_quantity"] - row["received_amount_cents"]
            for row in quotes
        )
        if amount_cents > available:
            raise ValidationError("收款金额超过客户待收金额")
        remaining = amount_cents
        for quote in quotes:
            pending = (
                quote["quote_price_cents"] * quote["quote_quantity"]
                - quote["received_amount_cents"]
            )
            applied = min(remaining, pending)
            if applied:
                add_allocation(conn, payment_id, quote["id"], applied)
                conn.execute(
                    "UPDATE quotes SET received_amount_cents=received_amount_cents+? "
                    "WHERE id=?",
                    (applied, quote["id"]),
                )
                sync_quote_payment_state(conn, quote["id"])
                remaining -= applied
            if remaining == 0:
                break

    def receive_customer_payment(
        self,
        customer_id: int,
        amount_cents: int,
        pay_date: str,
        method: str,
        remark: str = "",
        *,
        supersedes_id: int | None = None,
    ) -> PaymentReceipt:
        if amount_cents <= 0:
            raise ValidationError("收款金额必须大于 0")
        with transaction(self.db_path) as conn:
            customer = conn.execute(
                "SELECT id FROM customers WHERE id=? AND deleted_at IS NULL",
                (customer_id,),
            ).fetchone()
            if not customer:
                raise NotFoundError("客户不存在或已删除")
            payment_id = insert_payment(
                conn,
                customer_id=customer_id,
                pay_type="receivable",
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=method,
                remark=remark,
                supersedes_id=supersedes_id,
            )
            self._allocate_customer_payment(conn, payment_id, customer_id, amount_cents)
            audit(
                conn,
                "payments",
                payment_id,
                "receive",
                after={"amount_cents": amount_cents, "customer_id": customer_id},
            )
            return PaymentReceipt(payment_id, amount_cents)

    def record_supplier_payment(
        self,
        supplier_id: int,
        amount_cents: int,
        pay_date: str,
        method: str,
        remark: str = "",
        *,
        supersedes_id: int | None = None,
    ) -> int:
        if amount_cents <= 0:
            raise ValidationError("付款金额必须大于 0")
        with transaction(self.db_path) as conn:
            supplier = conn.execute(
                "SELECT id FROM suppliers WHERE id=? AND deleted_at IS NULL",
                (supplier_id,),
            ).fetchone()
            if not supplier:
                raise NotFoundError("供应商不存在或已删除")
            payment_id = insert_payment(
                conn,
                supplier_id=supplier_id,
                pay_type="payable",
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=method,
                remark=remark,
                supersedes_id=supersedes_id,
            )
            conn.execute(
                "UPDATE suppliers SET balance_cents=balance_cents-?, "
                "balance=balance-? WHERE id=?",
                (amount_cents, amount_cents / 100, supplier_id),
            )
            audit(
                conn,
                "payments",
                payment_id,
                "pay",
                after={"amount_cents": amount_cents, "supplier_id": supplier_id},
            )
            return payment_id

    def _void_in_transaction(self, conn, payment_id: int, reason: str) -> int:
        payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment:
            raise NotFoundError("收付款记录不存在")
        if payment["entry_kind"] != "payment":
            raise DataConflictError("冲销记录不能再次冲销")
        if conn.execute(
            "SELECT 1 FROM payments WHERE reversal_of_id=?", (payment_id,)
        ).fetchone():
            raise DataConflictError("该记录已经冲销")

        reversal_id = insert_payment(
            conn,
            quote_id=payment["quote_id"],
            customer_id=payment["customer_id"],
            supplier_id=payment["supplier_id"],
            pay_type=payment["type"],
            amount_cents=payment["amount_cents"],
            pay_date=payment["pay_date"],
            method=payment["method"],
            remark=reason,
            entry_kind="reversal",
            reversal_of_id=payment_id,
        )
        if payment["type"] == "receivable":
            allocations = conn.execute(
                "SELECT quote_id, amount_cents FROM payment_allocations WHERE payment_id=?",
                (payment_id,),
            ).fetchall()
            for allocation in allocations:
                add_allocation(
                    conn, reversal_id, allocation["quote_id"], -allocation["amount_cents"]
                )
                conn.execute(
                    "UPDATE quotes SET received_amount_cents=received_amount_cents-? "
                    "WHERE id=?",
                    (allocation["amount_cents"], allocation["quote_id"]),
                )
                sync_quote_payment_state(conn, allocation["quote_id"])
        elif payment["supplier_id"]:
            conn.execute(
                "UPDATE suppliers SET balance_cents=balance_cents+?, "
                "balance=balance+? WHERE id=?",
                (payment["amount_cents"], payment["amount"], payment["supplier_id"]),
            )
        audit(
            conn,
            "payments",
            payment_id,
            "void",
            before=dict(payment),
            after={"reversal_id": reversal_id},
            reason=reason,
        )
        return reversal_id

    def void_payment(self, payment_id: int, reason: str) -> int:
        if not reason.strip():
            raise ValidationError("作废原因不能为空")
        with transaction(self.db_path) as conn:
            return self._void_in_transaction(conn, payment_id, reason)

    def correct_payment(
        self,
        payment_id: int,
        *,
        amount_cents: int,
        pay_date: str,
        method: str,
        remark: str,
        reason: str,
    ) -> int:
        if amount_cents <= 0 or not reason.strip():
            raise ValidationError("更正金额必须大于 0，且必须填写原因")
        with transaction(self.db_path) as conn:
            original = conn.execute(
                "SELECT * FROM payments WHERE id=?", (payment_id,)
            ).fetchone()
            if not original:
                raise NotFoundError("收付款记录不存在")
            self._void_in_transaction(conn, payment_id, reason)
            replacement_id = insert_payment(
                conn,
                quote_id=original["quote_id"],
                customer_id=original["customer_id"],
                supplier_id=original["supplier_id"],
                pay_type=original["type"],
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=method,
                remark=remark,
                supersedes_id=payment_id,
            )
            if original["type"] == "receivable":
                self._allocate_customer_payment(
                    conn, replacement_id, original["customer_id"], amount_cents
                )
            elif original["supplier_id"]:
                conn.execute(
                    "UPDATE suppliers SET balance_cents=balance_cents-?, "
                    "balance=balance-? WHERE id=?",
                    (amount_cents, amount_cents / 100, original["supplier_id"]),
                )
            audit(
                conn,
                "payments",
                replacement_id,
                "correct",
                after={"supersedes_id": payment_id, "amount_cents": amount_cents},
                reason=reason,
            )
            return replacement_id

