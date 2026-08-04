"""Sales and purchase returns with inventory, counterparty, and cash effects."""

from __future__ import annotations

from uuid import uuid4

from src.models.connection import transaction
from src.models.finance_repository import (
    add_supplier_payment_allocation,
    get_shipment_snapshot,
    insert_purchase_return,
    insert_sales_return,
    list_customer_allocations_for_release,
    list_supplier_allocations_for_release,
    sales_return_totals,
    supplier_batch_allocated_cents,
)
from src.models.repositories import (
    add_allocation,
    add_quote_received_amount,
    adjust_customer_balance,
    adjust_supplier_balance,
    audit,
    decrement_batch_remaining,
    get_active_entity,
    increment_batch_remaining,
    log_operation,
)
from src.services.exceptions import (
    DataConflictError,
    InsufficientStockError,
    NotFoundError,
    ValidationError,
)
from src.services.ledger_posting_service import LedgerPostingService


class ReturnService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("退货数量必须是大于 0 的整数")

    @staticmethod
    def _unallocate_customer_receipts(
        conn,
        quote_id: int,
        amount_cents: int,
    ) -> None:
        remaining = amount_cents
        rows = list_customer_allocations_for_release(conn, quote_id)
        for row in rows:
            released = min(remaining, row["allocated_cents"])
            if released:
                add_allocation(conn, row["payment_id"], quote_id, -released)
                remaining -= released
            if remaining == 0:
                break
        if remaining:
            raise DataConflictError("客户收款分配不足，无法完成退货冲销")
        add_quote_received_amount(conn, quote_id, -amount_cents)

    @staticmethod
    def _unallocate_supplier_payments(
        conn,
        batch_id: int,
        amount_cents: int,
    ) -> None:
        remaining = amount_cents
        rows = list_supplier_allocations_for_release(conn, batch_id)
        for row in rows:
            released = min(remaining, row["allocated_cents"])
            if released:
                add_supplier_payment_allocation(
                    conn, row["payment_id"], batch_id, -released
                )
                remaining -= released
            if remaining == 0:
                break

    def return_sale(
        self,
        quote_id: int,
        *,
        quantity: int,
        return_date: str,
        restock: bool,
        reason: str,
        refund_account_id: int | None = None,
        cash_refund_cents: int = 0,
    ) -> int:
        self._validate_quantity(quantity)
        if not reason.strip():
            raise ValidationError("退货原因不能为空")
        if cash_refund_cents < 0:
            raise ValidationError("退款金额不能为负数")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, return_date)
            quote = get_active_entity(conn, "quotes", quote_id)
            snapshot = get_shipment_snapshot(conn, quote_id)
            if not quote or not snapshot:
                raise NotFoundError("已出库报价或成本快照不存在")
            return_totals = sales_return_totals(conn, quote_id)
            returned = return_totals["quantity"]
            if returned + quantity > snapshot["quantity"]:
                raise ValidationError("退货数量超过当前净出库数量")
            account = None
            if cash_refund_cents:
                if refund_account_id is None:
                    raise ValidationError("现金退款必须选择资金账户")
                account = LedgerPostingService.require_cash_account(
                    conn, refund_account_id
                )

            revenue_cents = snapshot["unit_sale_cents"] * quantity
            cost_cents = snapshot["unit_cost_cents"] * quantity
            previous_return_revenue = return_totals["revenue_cents"]
            net_total_before = snapshot["revenue_cents"] - previous_return_revenue
            outstanding_before = max(
                net_total_before - quote["received_amount_cents"], 0
            )
            paid_portion = revenue_cents - min(outstanding_before, revenue_cents)
            if cash_refund_cents > paid_portion:
                raise ValidationError("现金退款不能超过本次退货形成的客户余额")

            received_after_limit = net_total_before - revenue_cents
            excess_allocated = max(
                quote["received_amount_cents"] - received_after_limit, 0
            )
            if excess_allocated:
                self._unallocate_customer_receipts(
                    conn, quote_id, excess_allocated
                )

            customer_id = quote["customer_id"]
            if customer_id is not None:
                adjust_customer_balance(conn, customer_id, -revenue_cents)
                if cash_refund_cents:
                    adjust_customer_balance(
                        conn, customer_id, cash_refund_cents
                    )
            lines = [
                {
                    "account_code": "SALES",
                    "debit_cents": revenue_cents,
                    "customer_id": customer_id,
                    "quote_id": quote_id,
                },
                {
                    "account_code": "AR",
                    "credit_cents": revenue_cents,
                    "customer_id": customer_id,
                    "quote_id": quote_id,
                },
            ]
            if restock:
                increment_batch_remaining(conn, quote["batch_id"], quantity)
                lines.extend(
                    [
                        {
                            "account_code": "INVENTORY",
                            "debit_cents": cost_cents,
                            "customer_id": customer_id,
                            "quote_id": quote_id,
                            "batch_id": quote["batch_id"],
                        },
                        {
                            "account_code": "COGS",
                            "credit_cents": cost_cents,
                            "customer_id": customer_id,
                            "quote_id": quote_id,
                            "batch_id": quote["batch_id"],
                        },
                    ]
                )
            if cash_refund_cents:
                lines.extend(
                    [
                        {
                            "account_code": "AR",
                            "debit_cents": cash_refund_cents,
                            "customer_id": customer_id,
                            "quote_id": quote_id,
                        },
                        {
                            "account_id": refund_account_id,
                            "credit_cents": cash_refund_cents,
                            "customer_id": customer_id,
                            "quote_id": quote_id,
                        },
                    ]
                )
            token = uuid4().hex
            entry_id = LedgerPostingService.post(
                conn,
                entry_date=return_date,
                event_type="sales_return",
                source_type="sales_return",
                source_id=token,
                idempotency_key=f"sales-return:{quote_id}:{token}",
                lines=lines,
                reason=reason,
            )
            return_id = insert_sales_return(
                conn,
                quote_id=quote_id,
                return_date=return_date,
                quantity=quantity,
                revenue_cents=revenue_cents,
                cost_cents=cost_cents,
                restock_quantity=quantity if restock else 0,
                cash_refund_cents=cash_refund_cents,
                account_id=refund_account_id,
                ledger_entry_id=entry_id,
                reason=reason,
            )
            audit(
                conn,
                "sales_returns",
                return_id,
                "create",
                after={
                    "quote_id": quote_id,
                    "quantity": quantity,
                    "revenue_cents": revenue_cents,
                    "cost_cents": cost_cents,
                    "restock": restock,
                    "cash_refund_cents": cash_refund_cents,
                    "account_name": account["name"] if account else None,
                },
                reason=reason,
            )
            log_operation(conn, "销售退货", "sales_returns", return_id, reason)
            return return_id

    def return_purchase(
        self,
        batch_id: int,
        *,
        quantity: int,
        return_date: str,
        reason: str,
        refund_account_id: int | None = None,
        cash_refund_cents: int = 0,
    ) -> int:
        self._validate_quantity(quantity)
        if not reason.strip():
            raise ValidationError("退货原因不能为空")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, return_date)
            batch = get_active_entity(conn, "batches", batch_id)
            if not batch or batch["supplier_id"] is None:
                raise NotFoundError("供应商采购批次不存在")
            if quantity > batch["remaining"]:
                raise InsufficientStockError("采购退货数量超过当前可用库存")
            amount_cents = batch["purchase_price_cents"] * quantity
            if cash_refund_cents < 0 or cash_refund_cents > amount_cents:
                raise ValidationError("供应商退款必须在本次采购退货金额范围内")
            paid_portion = min(
                amount_cents,
                max(supplier_batch_allocated_cents(conn, batch_id), 0),
            )
            if cash_refund_cents > paid_portion:
                raise ValidationError("资金账户退款不能超过本次退货的已付款部分")
            if cash_refund_cents:
                if refund_account_id is None:
                    raise ValidationError("供应商退款必须选择资金账户")
                LedgerPostingService.require_cash_account(conn, refund_account_id)
            if not decrement_batch_remaining(conn, batch_id, quantity):
                raise InsufficientStockError("采购退货数量超过当前可用库存")
            self._unallocate_supplier_payments(conn, batch_id, amount_cents)
            adjust_supplier_balance(
                conn, batch["supplier_id"], -amount_cents + cash_refund_cents
            )
            lines = [
                {
                    "account_code": "AP",
                    "debit_cents": amount_cents,
                    "supplier_id": batch["supplier_id"],
                    "batch_id": batch_id,
                },
                {
                    "account_code": "INVENTORY",
                    "credit_cents": amount_cents,
                    "supplier_id": batch["supplier_id"],
                    "batch_id": batch_id,
                },
            ]
            if cash_refund_cents:
                lines.extend(
                    [
                        {
                            "account_id": refund_account_id,
                            "debit_cents": cash_refund_cents,
                            "supplier_id": batch["supplier_id"],
                            "batch_id": batch_id,
                        },
                        {
                            "account_code": "AP",
                            "credit_cents": cash_refund_cents,
                            "supplier_id": batch["supplier_id"],
                            "batch_id": batch_id,
                        },
                    ]
                )
            token = uuid4().hex
            entry_id = LedgerPostingService.post(
                conn,
                entry_date=return_date,
                event_type="purchase_return",
                source_type="purchase_return",
                source_id=token,
                idempotency_key=f"purchase-return:{batch_id}:{token}",
                lines=lines,
                reason=reason,
            )
            return_id = insert_purchase_return(
                conn,
                batch_id=batch_id,
                supplier_id=batch["supplier_id"],
                return_date=return_date,
                quantity=quantity,
                amount_cents=amount_cents,
                cash_refund_cents=cash_refund_cents,
                account_id=refund_account_id,
                ledger_entry_id=entry_id,
                reason=reason,
            )
            audit(
                conn,
                "purchase_returns",
                return_id,
                "create",
                after={
                    "batch_id": batch_id,
                    "quantity": quantity,
                    "amount_cents": amount_cents,
                    "cash_refund_cents": cash_refund_cents,
                },
                reason=reason,
            )
            log_operation(conn, "采购退货", "purchase_returns", return_id, reason)
            return return_id
