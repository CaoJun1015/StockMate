"""Inventory receiving and shipment with atomic operating-ledger postings."""

from __future__ import annotations

from datetime import date

from src.models.connection import transaction
from src.models.finance_repository import (
    add_supplier_payment_allocation,
    customer_payment_allocated_cents,
    get_finance_enabled_at,
    insert_shipment_snapshot,
    list_active_unreversed_payments,
    supplier_payment_allocated_cents,
)
from src.models.repositories import (
    add_allocation,
    add_quote_received_amount,
    adjust_supplier_balance,
    audit,
    decrement_batch_remaining,
    get_active_entity,
    insert_batch,
    insert_payment,
    list_active_batch_quotes,
    log_operation,
    set_quote_status,
    soft_delete,
    sync_quote_payment_state,
)
from src.services.exceptions import (
    InsufficientStockError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from src.services.ledger_posting_service import LedgerPostingService


class InventoryService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _apply_supplier_advances(
        conn,
        supplier_id: int,
        batch_id: int,
        amount_cents: int,
    ) -> int:
        remaining = amount_cents
        payments = list_active_unreversed_payments(
            conn,
            owner_field="supplier_id",
            owner_id=supplier_id,
            pay_type="payable",
        )
        for payment in payments:
            allocated = supplier_payment_allocated_cents(conn, payment["id"])
            available = payment["amount_cents"] - allocated
            applied = min(remaining, max(available, 0))
            if applied:
                add_supplier_payment_allocation(
                    conn, payment["id"], batch_id, applied
                )
                remaining -= applied
            if remaining == 0:
                break
        return amount_cents - remaining

    @staticmethod
    def _apply_customer_advances(
        conn,
        customer_id: int,
        quote_id: int,
        amount_cents: int,
    ) -> int:
        remaining = amount_cents
        payments = list_active_unreversed_payments(
            conn,
            owner_field="customer_id",
            owner_id=customer_id,
            pay_type="receivable",
        )
        for payment in payments:
            allocated = customer_payment_allocated_cents(conn, payment["id"])
            available = payment["amount_cents"] - allocated
            applied = min(remaining, max(available, 0))
            if applied:
                add_allocation(conn, payment["id"], quote_id, applied)
                add_quote_received_amount(conn, quote_id, applied)
                remaining -= applied
            if remaining == 0:
                break
        sync_quote_payment_state(conn, quote_id)
        return amount_cents - remaining

    def receive_batch(
        self,
        *,
        product_id: int,
        purchase_price_cents: int,
        quantity: int,
        date: str,
        remark: str = "",
        supplier_id: int | None = None,
        sn_list: str = "",
        settlement_mode: str = "credit",
        account_id: int | None = None,
    ) -> int:
        if (
            isinstance(purchase_price_cents, bool)
            or not isinstance(purchase_price_cents, int)
            or purchase_price_cents < 0
            or quantity <= 0
        ):
            raise ValidationError("进价必须为非负整数分且数量必须大于 0")
        if settlement_mode not in ("credit", "paid"):
            raise ValidationError("结算方式必须为赊购或现付")
        if settlement_mode == "credit" and supplier_id is None:
            raise ValidationError("赊购必须选择供应商")

        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, date)
            if not get_active_entity(conn, "products", product_id):
                raise NotFoundError("机型不存在或已删除")
            if supplier_id is not None and not get_active_entity(
                conn, "suppliers", supplier_id
            ):
                raise NotFoundError("供应商不存在或已删除")
            account = None
            if settlement_mode == "paid":
                if account_id is None:
                    raise ValidationError("现付入库必须选择资金账户")
                account = LedgerPostingService.require_cash_account(conn, account_id)

            batch_id = insert_batch(
                conn,
                product_id=product_id,
                purchase_price_cents=purchase_price_cents,
                quantity=quantity,
                date=date,
                remark=remark,
                supplier_id=supplier_id,
                sn_list=sn_list,
            )
            total_cents = purchase_price_cents * quantity
            if supplier_id is not None:
                adjust_supplier_balance(conn, supplier_id, total_cents)
                purchase_lines = [
                    {
                        "account_code": "INVENTORY",
                        "debit_cents": total_cents,
                        "supplier_id": supplier_id,
                        "batch_id": batch_id,
                    },
                    {
                        "account_code": "AP",
                        "credit_cents": total_cents,
                        "supplier_id": supplier_id,
                        "batch_id": batch_id,
                    },
                ]
            else:
                purchase_lines = [
                    {
                        "account_code": "INVENTORY",
                        "debit_cents": total_cents,
                        "batch_id": batch_id,
                    },
                    {
                        "account_id": account_id,
                        "credit_cents": total_cents,
                        "batch_id": batch_id,
                    },
                ]
            LedgerPostingService.post(
                conn,
                entry_date=date,
                event_type="inventory_receipt",
                source_type="batch",
                source_id=batch_id,
                idempotency_key=f"batch:{batch_id}:receive",
                lines=purchase_lines,
                remark=remark,
            )

            advance_applied = 0
            if supplier_id is not None:
                advance_applied = self._apply_supplier_advances(
                    conn, supplier_id, batch_id, total_cents
                )
            payment_id = None
            if settlement_mode == "paid" and supplier_id is not None:
                outstanding = max(total_cents - advance_applied, 0)
                if outstanding:
                    payment_id = insert_payment(
                        conn,
                        supplier_id=supplier_id,
                        pay_type="payable",
                        amount_cents=outstanding,
                        pay_date=date,
                        method=account["name"],
                        account_id=account_id,
                        remark=f"批次#{batch_id} 入库现付",
                    )
                    add_supplier_payment_allocation(
                        conn, payment_id, batch_id, outstanding
                    )
                    adjust_supplier_balance(conn, supplier_id, -outstanding)
                    LedgerPostingService.post(
                        conn,
                        entry_date=date,
                        event_type="supplier_payment",
                        source_type="payment",
                        source_id=payment_id,
                        idempotency_key=f"payment:{payment_id}:pay",
                        lines=[
                            {
                                "account_code": "AP",
                                "debit_cents": outstanding,
                                "supplier_id": supplier_id,
                                "batch_id": batch_id,
                            },
                            {
                                "account_id": account_id,
                                "credit_cents": outstanding,
                                "supplier_id": supplier_id,
                                "batch_id": batch_id,
                            },
                        ],
                    )
            audit(
                conn,
                "batches",
                batch_id,
                "receive",
                after={
                    "product_id": product_id,
                    "supplier_id": supplier_id,
                    "purchase_price_cents": purchase_price_cents,
                    "quantity": quantity,
                    "total_cents": total_cents,
                    "date": date,
                    "settlement_mode": settlement_mode,
                    "account_id": account_id,
                    "payment_id": payment_id,
                    "advance_applied_cents": advance_applied,
                },
            )
            log_operation(
                conn,
                "入库",
                "batches",
                batch_id,
                f"数量={quantity}, 单价分={purchase_price_cents}",
            )
            return batch_id

    def ship_quote(
        self,
        quote_id: int,
        sn_list: str = "",
        shipped_date: str | None = None,
    ) -> None:
        shipped_date = shipped_date or date.today().isoformat()
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, shipped_date)
            quote = get_active_entity(conn, "quotes", quote_id)
            if not quote:
                raise NotFoundError("报价记录不存在")
            if quote["status"] not in ("待确认", "已报价"):
                raise InvalidTransitionError(
                    f"当前状态“{quote['status']}”不允许出库"
                )
            batch = get_active_entity(conn, "batches", quote["batch_id"])
            if not batch or batch["remaining"] < quote["quote_quantity"]:
                raise InsufficientStockError("库存不足，无法出库")
            if not decrement_batch_remaining(
                conn, quote["batch_id"], quote["quote_quantity"]
            ):
                raise InsufficientStockError("库存不足，无法出库")

            quantity = quote["quote_quantity"]
            revenue_cents = quote["quote_price_cents"] * quantity
            cost_cents = batch["purchase_price_cents"] * quantity
            customer_id = quote["customer_id"]
            set_quote_status(conn, quote_id, "已出库", sn_list=sn_list)
            entry_id = LedgerPostingService.post(
                conn,
                entry_date=shipped_date,
                event_type="sales_shipment",
                source_type="quote",
                source_id=quote_id,
                idempotency_key=f"quote:{quote_id}:ship",
                lines=[
                    {
                        "account_code": "AR",
                        "debit_cents": revenue_cents,
                        "customer_id": customer_id,
                        "quote_id": quote_id,
                    },
                    {
                        "account_code": "SALES",
                        "credit_cents": revenue_cents,
                        "customer_id": customer_id,
                        "quote_id": quote_id,
                    },
                    {
                        "account_code": "COGS",
                        "debit_cents": cost_cents,
                        "customer_id": customer_id,
                        "quote_id": quote_id,
                        "batch_id": batch["id"],
                    },
                    {
                        "account_code": "INVENTORY",
                        "credit_cents": cost_cents,
                        "customer_id": customer_id,
                        "quote_id": quote_id,
                        "batch_id": batch["id"],
                    },
                ],
            )
            insert_shipment_snapshot(
                conn,
                quote_id=quote_id,
                shipped_date=shipped_date,
                quantity=quantity,
                unit_sale_cents=quote["quote_price_cents"],
                unit_cost_cents=batch["purchase_price_cents"],
                ledger_entry_id=entry_id,
            )
            advance_applied = 0
            if customer_id is not None:
                advance_applied = self._apply_customer_advances(
                    conn, customer_id, quote_id, revenue_cents
                )
            audit(
                conn,
                "quotes",
                quote_id,
                "ship",
                before=dict(quote),
                after={
                    "status": "已出库",
                    "sn_list": sn_list,
                    "shipped_date": shipped_date,
                    "revenue_cents": revenue_cents,
                    "cost_cents": cost_cents,
                    "advance_applied_cents": advance_applied,
                },
            )
            log_operation(conn, "出库", "quotes", quote_id, f"SN={sn_list}")

    def delete_batch(self, batch_id: int, reason: str = "用户删除批次") -> None:
        with transaction(self.db_path) as conn:
            if get_finance_enabled_at(conn):
                raise InvalidTransitionError(
                    "财务启用后不能删除批次，请使用采购退货"
                )
            batch = get_active_entity(conn, "batches", batch_id)
            if not batch:
                raise NotFoundError("库存批次不存在或已删除")
            quotes = list_active_batch_quotes(conn, batch_id)
            blocking = [
                quote
                for quote in quotes
                if quote["status"] not in ("待确认", "已取消")
            ]
            if blocking:
                raise InvalidTransitionError(
                    f"批次存在已报价或已出库记录，不能删除（报价 {blocking[0]['id']}）"
                )
            for quote in quotes:
                soft_delete(conn, "quotes", quote["id"], "所属批次已删除")
            if batch["supplier_id"]:
                adjust_supplier_balance(
                    conn,
                    batch["supplier_id"],
                    -(batch["purchase_price_cents"] * batch["quantity"]),
                )
            soft_delete(conn, "batches", batch_id, reason)
            log_operation(conn, "删除批次", "batches", batch_id, reason)
