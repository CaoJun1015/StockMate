"""Payments, advances, FIFO allocations, correction, and reversal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.models.connection import transaction
from src.models.finance_repository import (
    add_supplier_payment_allocation,
    find_ledger_entry_by_key,
    list_supplier_payable_batches,
    list_supplier_payment_allocations,
)
from src.models.repositories import (
    add_allocation,
    add_quote_received_amount,
    adjust_customer_balance,
    adjust_supplier_balance,
    audit,
    get_active_entity,
    get_payment,
    insert_payment,
    list_fifo_receivable_quotes,
    list_payment_allocations,
    log_operation,
    payment_has_reversal,
    sync_quote_payment_state,
)
from src.services.exceptions import DataConflictError, NotFoundError, ValidationError
from src.services.ledger_posting_service import LedgerPostingService


@dataclass(frozen=True)
class PaymentReceipt:
    payment_id: int
    allocated_cents: int
    unapplied_cents: int = 0


class PaymentService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def _allocate_customer_payment(
        self,
        conn,
        payment_id: int,
        customer_id: int,
        amount_cents: int,
    ) -> tuple[list[dict[str, int]], int]:
        remaining = amount_cents
        allocations: list[dict[str, int]] = []
        for quote in list_fifo_receivable_quotes(conn, customer_id):
            pending = (
                quote["quote_price_cents"] * quote["quote_quantity"]
                - quote["received_amount_cents"]
            )
            applied = min(remaining, pending)
            if applied:
                add_allocation(conn, payment_id, quote["id"], applied)
                add_quote_received_amount(conn, quote["id"], applied)
                sync_quote_payment_state(conn, quote["id"])
                allocations.append(
                    {"quote_id": quote["id"], "amount_cents": applied}
                )
                remaining -= applied
            if remaining == 0:
                break
        return allocations, remaining

    def _allocate_supplier_payment(
        self,
        conn,
        payment_id: int,
        supplier_id: int,
        amount_cents: int,
    ) -> tuple[list[dict[str, int]], int]:
        rows = list_supplier_payable_batches(conn, supplier_id)
        remaining = amount_cents
        allocations: list[dict[str, int]] = []
        for batch in rows:
            pending = batch["payable_cents"] - batch["allocated_cents"]
            if pending <= 0:
                continue
            applied = min(remaining, pending)
            if applied:
                add_supplier_payment_allocation(
                    conn, payment_id, batch["id"], applied
                )
                allocations.append(
                    {"batch_id": batch["id"], "amount_cents": applied}
                )
                remaining -= applied
            if remaining == 0:
                break
        return allocations, remaining

    def receive_customer_payment(
        self,
        customer_id: int,
        amount_cents: int,
        pay_date: str,
        method: str = "",
        remark: str = "",
        *,
        account_id: int | None = None,
        supersedes_id: int | None = None,
    ) -> PaymentReceipt:
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
            raise ValidationError("收款金额必须使用整数分")
        if amount_cents <= 0:
            raise ValidationError("收款金额必须大于 0")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, pay_date)
            if account_id is None:
                raise ValidationError("请选择收款资金账户")
            account = LedgerPostingService.require_cash_account(conn, account_id)
            if not get_active_entity(conn, "customers", customer_id):
                raise NotFoundError("客户不存在或已删除")
            payment_id = insert_payment(
                conn,
                customer_id=customer_id,
                pay_type="receivable",
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=account["name"],
                account_id=account_id,
                remark=remark,
                supersedes_id=supersedes_id,
            )
            allocations, unapplied = self._allocate_customer_payment(
                conn, payment_id, customer_id, amount_cents
            )
            adjust_customer_balance(conn, customer_id, -amount_cents)
            LedgerPostingService.post(
                conn,
                entry_date=pay_date,
                event_type="customer_receipt",
                source_type="payment",
                source_id=payment_id,
                idempotency_key=f"payment:{payment_id}:receive",
                lines=[
                    {
                        "account_id": account_id,
                        "debit_cents": amount_cents,
                        "customer_id": customer_id,
                    },
                    {
                        "account_code": "AR",
                        "credit_cents": amount_cents,
                        "customer_id": customer_id,
                    },
                ],
                remark=remark,
            )
            audit(
                conn,
                "payments",
                payment_id,
                "receive",
                after={
                    "amount_cents": amount_cents,
                    "customer_id": customer_id,
                    "account_id": account_id,
                    "pay_date": pay_date,
                    "allocations": allocations,
                    "unapplied_cents": unapplied,
                },
            )
            log_operation(
                conn, "收款", "payments", payment_id, f"金额分={amount_cents}"
            )
            return PaymentReceipt(payment_id, amount_cents - unapplied, unapplied)

    def record_supplier_payment(
        self,
        supplier_id: int,
        amount_cents: int,
        pay_date: str,
        method: str = "",
        remark: str = "",
        *,
        account_id: int | None = None,
        supersedes_id: int | None = None,
    ) -> int:
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
            raise ValidationError("付款金额必须使用整数分")
        if amount_cents <= 0:
            raise ValidationError("付款金额必须大于 0")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, pay_date)
            if account_id is None:
                raise ValidationError("请选择付款资金账户")
            account = LedgerPostingService.require_cash_account(conn, account_id)
            if not get_active_entity(conn, "suppliers", supplier_id):
                raise NotFoundError("供应商不存在或已删除")
            payment_id = insert_payment(
                conn,
                supplier_id=supplier_id,
                pay_type="payable",
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=account["name"],
                account_id=account_id,
                remark=remark,
                supersedes_id=supersedes_id,
            )
            allocations, unapplied = self._allocate_supplier_payment(
                conn, payment_id, supplier_id, amount_cents
            )
            adjust_supplier_balance(conn, supplier_id, -amount_cents)
            LedgerPostingService.post(
                conn,
                entry_date=pay_date,
                event_type="supplier_payment",
                source_type="payment",
                source_id=payment_id,
                idempotency_key=f"payment:{payment_id}:pay",
                lines=[
                    {
                        "account_code": "AP",
                        "debit_cents": amount_cents,
                        "supplier_id": supplier_id,
                    },
                    {
                        "account_id": account_id,
                        "credit_cents": amount_cents,
                        "supplier_id": supplier_id,
                    },
                ],
                remark=remark,
            )
            audit(
                conn,
                "payments",
                payment_id,
                "pay",
                after={
                    "amount_cents": amount_cents,
                    "supplier_id": supplier_id,
                    "account_id": account_id,
                    "pay_date": pay_date,
                    "allocations": allocations,
                    "unapplied_cents": unapplied,
                },
            )
            log_operation(
                conn, "付款", "payments", payment_id, f"金额分={amount_cents}"
            )
            return payment_id

    def _void_in_transaction(
        self,
        conn,
        payment_id: int,
        reason: str,
        reversal_date: str,
    ) -> int:
        payment = get_payment(conn, payment_id)
        if not payment:
            raise NotFoundError("收付款记录不存在")
        if payment["entry_kind"] != "payment":
            raise DataConflictError("冲销记录不能再次冲销")
        if payment_has_reversal(conn, payment_id):
            raise DataConflictError("该记录已经冲销")
        original_key = (
            f"payment:{payment_id}:receive"
            if payment["type"] == "receivable"
            else f"payment:{payment_id}:pay"
        )
        original_entry = find_ledger_entry_by_key(conn, original_key)
        if not original_entry:
            raise ValidationError(
                "该历史流水没有资金账户，不能直接作废；请使用资金调整并填写原因"
            )

        reversal_id = insert_payment(
            conn,
            quote_id=payment["quote_id"],
            customer_id=payment["customer_id"],
            supplier_id=payment["supplier_id"],
            pay_type=payment["type"],
            amount_cents=payment["amount_cents"],
            pay_date=reversal_date,
            method=payment["method"],
            account_id=payment["account_id"],
            remark=reason,
            entry_kind="reversal",
            reversal_of_id=payment_id,
        )
        if payment["type"] == "receivable":
            allocations = list_payment_allocations(conn, payment_id)
            for allocation in allocations:
                add_allocation(
                    conn,
                    reversal_id,
                    allocation["quote_id"],
                    -allocation["amount_cents"],
                )
                add_quote_received_amount(
                    conn,
                    allocation["quote_id"],
                    -allocation["amount_cents"],
                )
                sync_quote_payment_state(conn, allocation["quote_id"])
            adjust_customer_balance(
                conn, payment["customer_id"], payment["amount_cents"]
            )
        else:
            allocations = list_supplier_payment_allocations(conn, payment_id)
            for allocation in allocations:
                add_supplier_payment_allocation(
                    conn,
                    reversal_id,
                    allocation["batch_id"],
                    -allocation["amount_cents"],
                )
            adjust_supplier_balance(
                conn, payment["supplier_id"], payment["amount_cents"]
            )
        LedgerPostingService.reverse(
            conn,
            entry_id=original_entry["id"],
            entry_date=reversal_date,
            source_type="payment_reversal",
            source_id=reversal_id,
            idempotency_key=f"payment:{reversal_id}:reversal",
            reason=reason,
        )
        audit(
            conn,
            "payments",
            payment_id,
            "void",
            before=dict(payment),
            after={
                "reversal_id": reversal_id,
                "amount_cents": payment["amount_cents"],
                "allocations": allocations,
            },
            reason=reason,
        )
        log_operation(conn, "作废流水", "payments", payment_id, reason)
        return reversal_id

    def void_payment(
        self,
        payment_id: int,
        reason: str,
        *,
        reversal_date: str | None = None,
    ) -> int:
        if not reason.strip():
            raise ValidationError("作废原因不能为空")
        reversal_date = reversal_date or date.today().isoformat()
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, reversal_date)
            return self._void_in_transaction(
                conn, payment_id, reason, reversal_date
            )

    def correct_payment(
        self,
        payment_id: int,
        *,
        amount_cents: int,
        pay_date: str,
        method: str = "",
        account_id: int | None = None,
        remark: str,
        reason: str,
    ) -> int:
        if amount_cents <= 0 or not reason.strip():
            raise ValidationError("更正金额必须大于 0，且必须填写原因")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, pay_date)
            original = get_payment(conn, payment_id)
            if not original:
                raise NotFoundError("收付款记录不存在")
            account_id = account_id or original["account_id"]
            if account_id is None:
                raise ValidationError("请选择更正后的资金账户")
            account = LedgerPostingService.require_cash_account(conn, account_id)
            self._void_in_transaction(conn, payment_id, reason, pay_date)
            replacement_id = insert_payment(
                conn,
                quote_id=original["quote_id"],
                customer_id=original["customer_id"],
                supplier_id=original["supplier_id"],
                pay_type=original["type"],
                amount_cents=amount_cents,
                pay_date=pay_date,
                method=account["name"],
                account_id=account_id,
                remark=remark,
                supersedes_id=payment_id,
            )
            if original["type"] == "receivable":
                allocations, unapplied = self._allocate_customer_payment(
                    conn,
                    replacement_id,
                    original["customer_id"],
                    amount_cents,
                )
                adjust_customer_balance(
                    conn, original["customer_id"], -amount_cents
                )
                lines = [
                    {
                        "account_id": account_id,
                        "debit_cents": amount_cents,
                        "customer_id": original["customer_id"],
                    },
                    {
                        "account_code": "AR",
                        "credit_cents": amount_cents,
                        "customer_id": original["customer_id"],
                    },
                ]
                event_type = "customer_receipt"
            else:
                allocations, unapplied = self._allocate_supplier_payment(
                    conn,
                    replacement_id,
                    original["supplier_id"],
                    amount_cents,
                )
                adjust_supplier_balance(
                    conn, original["supplier_id"], -amount_cents
                )
                lines = [
                    {
                        "account_code": "AP",
                        "debit_cents": amount_cents,
                        "supplier_id": original["supplier_id"],
                    },
                    {
                        "account_id": account_id,
                        "credit_cents": amount_cents,
                        "supplier_id": original["supplier_id"],
                    },
                ]
                event_type = "supplier_payment"
            LedgerPostingService.post(
                conn,
                entry_date=pay_date,
                event_type=event_type,
                source_type="payment",
                source_id=replacement_id,
                idempotency_key=(
                    f"payment:{replacement_id}:receive"
                    if original["type"] == "receivable"
                    else f"payment:{replacement_id}:pay"
                ),
                lines=lines,
                status="corrected",
                supersedes_id=find_ledger_entry_by_key(
                    conn,
                    (
                        f"payment:{payment_id}:receive"
                        if original["type"] == "receivable"
                        else f"payment:{payment_id}:pay"
                    ),
                )["id"],
                remark=remark,
                reason=reason,
            )
            audit(
                conn,
                "payments",
                replacement_id,
                "correct",
                after={
                    "supersedes_id": payment_id,
                    "amount_cents": amount_cents,
                    "account_id": account_id,
                    "pay_date": pay_date,
                    "allocations": allocations,
                    "unapplied_cents": unapplied,
                },
                reason=reason,
            )
            log_operation(
                conn,
                "更正流水",
                "payments",
                replacement_id,
                f"原流水 {payment_id}; {reason}",
            )
            return replacement_id
