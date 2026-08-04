"""Immutable payment ledger, FIFO allocation, correction, and reversal."""

from __future__ import annotations

from dataclasses import dataclass

from src.models.connection import transaction
from src.models.repositories import (
    add_allocation,
    add_quote_received_amount,
    adjust_supplier_balance,
    audit,
    get_active_entity,
    get_payment,
    insert_payment,
    list_fifo_receivable_quotes,
    list_payment_allocations,
    payment_has_reversal,
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
        quotes = list_fifo_receivable_quotes(conn, customer_id)
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
                add_quote_received_amount(conn, quote["id"], applied)
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
            if not get_active_entity(conn, "customers", customer_id):
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
            if not get_active_entity(conn, "suppliers", supplier_id):
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
            adjust_supplier_balance(conn, supplier_id, -amount_cents)
            audit(
                conn,
                "payments",
                payment_id,
                "pay",
                after={"amount_cents": amount_cents, "supplier_id": supplier_id},
            )
            return payment_id

    def _void_in_transaction(self, conn, payment_id: int, reason: str) -> int:
        payment = get_payment(conn, payment_id)
        if not payment:
            raise NotFoundError("收付款记录不存在")
        if payment["entry_kind"] != "payment":
            raise DataConflictError("冲销记录不能再次冲销")
        if payment_has_reversal(conn, payment_id):
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
            allocations = list_payment_allocations(conn, payment_id)
            for allocation in allocations:
                add_allocation(
                    conn, reversal_id, allocation["quote_id"], -allocation["amount_cents"]
                )
                add_quote_received_amount(
                    conn,
                    allocation["quote_id"],
                    -allocation["amount_cents"],
                )
                sync_quote_payment_state(conn, allocation["quote_id"])
        elif payment["supplier_id"]:
            adjust_supplier_balance(
                conn,
                payment["supplier_id"],
                payment["amount_cents"],
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
            original = get_payment(conn, payment_id)
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
                adjust_supplier_balance(
                    conn,
                    original["supplier_id"],
                    -amount_cents,
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

