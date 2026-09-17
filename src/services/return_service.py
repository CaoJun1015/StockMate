"""Sales and purchase returns with inventory, counterparty, and cash effects."""

from __future__ import annotations

from uuid import uuid4

from src.models.connection import connect, transaction
from src.models.inventory_repository import (
    apply_inventory_delta,
    insert_inventory_movement,
    list_shipment_allocations,
    normalize_sn_list,
    returned_quantity_for_allocation,
    returned_sns_for_allocation,
    insert_sales_return_allocation,
    allocated_return_quantity_for_quote,
)
from src.models.finance_repository import (
    add_supplier_payment_allocation,
    get_shipment_snapshot,
    insert_purchase_return,
    insert_payment_refund_allocation,
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
    get_active_entity,
    log_operation,
    sync_quote_payment_state,
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
    ) -> list[dict[str, int]]:
        remaining = amount_cents
        released_sources: list[dict[str, int]] = []
        rows = list_customer_allocations_for_release(conn, quote_id)
        for row in rows:
            released = min(remaining, row["allocated_cents"])
            if released:
                add_allocation(conn, row["payment_id"], quote_id, -released)
                released_sources.append(
                    {"payment_id": row["payment_id"], "amount_cents": released}
                )
                remaining -= released
            if remaining == 0:
                break
        if remaining:
            raise DataConflictError("客户收款分配不足，无法完成退货冲销")
        add_quote_received_amount(conn, quote_id, -amount_cents)
        return released_sources

    @staticmethod
    def _unallocate_supplier_payments(
        conn,
        batch_id: int,
        amount_cents: int,
    ) -> list[dict[str, int]]:
        remaining = amount_cents
        released_sources: list[dict[str, int]] = []
        rows = list_supplier_allocations_for_release(conn, batch_id)
        for row in rows:
            released = min(remaining, row["allocated_cents"])
            if released:
                add_supplier_payment_allocation(
                    conn, row["payment_id"], batch_id, -released
                )
                released_sources.append(
                    {"payment_id": row["payment_id"], "amount_cents": released}
                )
                remaining -= released
            if remaining == 0:
                break
        return released_sources

    @staticmethod
    def _allocate_refund_sources(
        conn,
        sources: list[dict[str, int]],
        amount_cents: int,
        *,
        sales_return_id: int | None = None,
        purchase_return_id: int | None = None,
    ) -> None:
        remaining = amount_cents
        for source in sources:
            applied = min(remaining, source["amount_cents"])
            if applied:
                insert_payment_refund_allocation(
                    conn,
                    source["payment_id"],
                    applied,
                    sales_return_id=sales_return_id,
                    purchase_return_id=purchase_return_id,
                )
                remaining -= applied
            if remaining == 0:
                break
        if remaining:
            raise DataConflictError("现金退款缺少可解释的原付款来源")

    def _prepare_sale_return(
        self,
        conn,
        quote_id: int,
        *,
        quantity: int,
        restock: bool,
        refund_account_id: int | None,
        cash_refund_cents: int,
        restock_allocations: list[dict] | None,
    ) -> dict:
        """Calculate and validate a sale return without changing the database."""
        self._validate_quantity(quantity)
        if cash_refund_cents < 0:
            raise ValidationError("退款金额不能为负数")
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
            account = LedgerPostingService.require_cash_account(conn, refund_account_id)

        shipment_allocations = list_shipment_allocations(conn, quote_id=quote_id)
        if not shipment_allocations:
            raise DataConflictError("出库缺少批次分配，无法确定退货成本")
        if restock_allocations is None:
            if len(shipment_allocations) != 1:
                raise ValidationError("跨批次出库退货必须选择原出库批次和数量")
            restock_allocations = [{
                "shipment_allocation_id": shipment_allocations[0]["id"],
                "quantity": quantity,
                "sn_list": "",
            }]
        if allocated_return_quantity_for_quote(conn, quote_id) != returned:
            raise DataConflictError("历史退货缺少完整批次分配，请先核对退货记录")
        by_id = {row["id"]: row for row in shipment_allocations}
        selected: list[dict] = []
        seen: set[int] = set()
        request_sns: set[str] = set()
        for item in restock_allocations:
            try:
                allocation_id = int(item["shipment_allocation_id"])
                item_quantity = int(item["quantity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("退货批次分配格式无效") from exc
            if allocation_id in seen or allocation_id not in by_id:
                raise ValidationError("退货包含重复或不属于本订单的出库分配")
            if item_quantity <= 0:
                raise ValidationError("退货批次数量必须大于 0")
            original = by_id[allocation_id]
            already_returned = returned_quantity_for_allocation(conn, allocation_id)
            if already_returned + item_quantity > original["quantity"]:
                raise ValidationError("退回原批次的数量超过该批次净出库数量")
            item_sns = normalize_sn_list(item.get("sn_list", ""))
            original_sn_list = normalize_sn_list(original.get("sn_list", ""))
            original_sns = set(original_sn_list)
            already_returned_sns = returned_sns_for_allocation(conn, allocation_id)
            if restock and original_sns and not item_sns:
                available_sns = [sn for sn in original_sn_list if sn not in already_returned_sns]
                if len(available_sns) == item_quantity:
                    item_sns = available_sns
                else:
                    raise ValidationError("原出库含多个可退SN时，回库必须明确选择本次退回的SN")
            if item_sns and len(item_sns) != item_quantity:
                raise ValidationError("退货SN数量必须等于对应批次退货数量")
            if len(set(item_sns)) != len(item_sns) or request_sns.intersection(item_sns):
                raise ValidationError("退货包含重复SN")
            if already_returned_sns.intersection(item_sns):
                raise ValidationError("SN已在该出库分配中退货")
            if item_sns and original_sns and not set(item_sns).issubset(original_sns):
                raise ValidationError("退货SN不属于所选原出库批次")
            request_sns.update(item_sns)
            selected.append({**original, "return_quantity": item_quantity,
                             "return_sn_list": ",".join(item_sns)})
            seen.add(allocation_id)
        if sum(item["return_quantity"] for item in selected) != quantity:
            raise ValidationError("各原出库批次退货数量合计必须等于本次退货数量")

        revenue_cents = snapshot["unit_sale_cents"] * quantity
        cost_cents = sum(item["unit_cost_cents"] * item["return_quantity"] for item in selected)
        net_total_before = snapshot["revenue_cents"] - return_totals["revenue_cents"]
        outstanding_before = max(net_total_before - quote["received_amount_cents"], 0)
        paid_portion = revenue_cents - min(outstanding_before, revenue_cents)
        if cash_refund_cents > paid_portion:
            raise ValidationError("现金退款不能超过本次退货形成的客户余额")
        net_total_after = net_total_before - revenue_cents
        excess_allocated = max(quote["received_amount_cents"] - net_total_after, 0)
        return {
            "quote": quote, "snapshot": snapshot, "account": account, "selected": selected,
            "revenue_cents": revenue_cents, "cost_cents": cost_cents,
            "returned_quantity": returned, "returnable_quantity": snapshot["quantity"] - returned,
            "net_total_before_cents": net_total_before, "net_total_after_cents": net_total_after,
            "net_receivable_before_cents": max(net_total_before - quote["received_amount_cents"], 0),
            "net_receivable_after_cents": max(net_total_after - (quote["received_amount_cents"] - excess_allocated), 0),
            "cash_refund_cents": cash_refund_cents,
            "released_balance_cents": excess_allocated,
            "retained_balance_cents": excess_allocated - cash_refund_cents,
            "refund_limit_cents": paid_portion,
        }

    def preview_sale_return(self, quote_id: int, **kwargs) -> dict:
        """Read-only counterpart of :meth:`return_sale` using its exact calculation."""
        conn = connect(self.db_path, read_only=True)
        try:
            return self._prepare_sale_return(conn, quote_id, **kwargs)
        finally:
            conn.close()

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
        restock_allocations: list[dict] | None = None,
    ) -> int:
        if not reason.strip():
            raise ValidationError("退货原因不能为空")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, return_date)
            prepared = self._prepare_sale_return(
                conn, quote_id, quantity=quantity, restock=restock,
                refund_account_id=refund_account_id,
                cash_refund_cents=cash_refund_cents,
                restock_allocations=restock_allocations,
            )
            quote = prepared["quote"]
            account = prepared["account"]
            selected = prepared["selected"]
            revenue_cents = prepared["revenue_cents"]
            cost_cents = prepared["cost_cents"]
            excess_allocated = prepared["released_balance_cents"]
            released_sources: list[dict[str, int]] = []
            if excess_allocated:
                released_sources = self._unallocate_customer_receipts(
                    conn, quote_id, excess_allocated
                )

            customer_id = quote["customer_id"]
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
                for item in selected:
                    if not apply_inventory_delta(
                        conn, batch_id=item["batch_id"],
                        quantity_delta=item["return_quantity"]
                    ):
                        raise DataConflictError("退货回库批次已删除")
                    item_cost = item["unit_cost_cents"] * item["return_quantity"]
                    lines.extend([
                        {
                            "account_code": "INVENTORY", "debit_cents": item_cost,
                            "customer_id": customer_id, "quote_id": quote_id,
                            "batch_id": item["batch_id"],
                        },
                        {
                            "account_code": "COGS", "credit_cents": item_cost,
                            "customer_id": customer_id, "quote_id": quote_id,
                            "batch_id": item["batch_id"],
                        },
                    ])
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
            for item in selected:
                insert_sales_return_allocation(
                    conn, return_id, item["id"], item["return_quantity"],
                    item["return_quantity"] if restock else 0, item["return_sn_list"],
                )
            self._allocate_refund_sources(
                conn,
                released_sources,
                cash_refund_cents,
                sales_return_id=return_id,
            )
            sync_quote_payment_state(conn, quote_id)
            if restock:
                for item in selected:
                    insert_inventory_movement(
                        conn,
                        movement_date=return_date,
                        movement_type="sales_return",
                        product_id=item["product_id"],
                        batch_id=item["batch_id"],
                        quantity_delta=item["return_quantity"],
                        unit_cost_cents=item["unit_cost_cents"],
                        source_type="sales_return",
                        source_id=return_id,
                        shipment_allocation_id=item["id"],
                        ledger_entry_id=entry_id,
                        sn_list=item["return_sn_list"],
                        idempotency_key=(
                            f"sales-return:{return_id}:allocation:{item['id']}"
                        ),
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
                    "allocations": [
                        {
                            "shipment_allocation_id": item["id"],
                            "batch_id": item["batch_id"],
                            "quantity": item["return_quantity"],
                            "sn_list": item["return_sn_list"],
                        }
                        for item in selected
                    ],
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
            if not apply_inventory_delta(
                conn, batch_id=batch_id, quantity_delta=-quantity
            ):
                raise InsufficientStockError("采购退货数量超过当前可用库存")
            released_sources = self._unallocate_supplier_payments(
                conn, batch_id, amount_cents
            )
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
            self._allocate_refund_sources(
                conn,
                released_sources,
                cash_refund_cents,
                purchase_return_id=return_id,
            )
            insert_inventory_movement(
                conn,
                movement_date=return_date,
                movement_type="purchase_return",
                product_id=batch["product_id"],
                batch_id=batch_id,
                quantity_delta=-quantity,
                unit_cost_cents=batch["purchase_price_cents"],
                source_type="purchase_return",
                source_id=return_id,
                ledger_entry_id=entry_id,
                idempotency_key=f"purchase-return:{return_id}:inventory",
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
