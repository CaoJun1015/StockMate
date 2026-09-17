"""Order/quote use cases."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    audit,
    get_active_entity,
    get_quote,
    insert_quote,
    log_operation,
    set_quote_status,
    soft_delete,
    update_quote as update_quote_record,
)
from src.services.exceptions import (
    DataConflictError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)


VALID_TRANSITIONS = {
    "待确认": {"已报价", "已取消"},
    "已报价": {"已取消"},
    "已取消": set(),
}


class OrderService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def create_quote(
        self,
        *,
        batch_id: int,
        customer_id: int | None,
        quote_price_cents: int,
        quote_quantity: int,
        quote_date: str,
        remark: str = "",
        tax_rate: float | None = None,
        purchase_tax_inclusive: bool = False,
        quote_tax_inclusive: bool = False,
    ) -> int:
        if quote_price_cents < 0 or quote_quantity <= 0:
            raise ValidationError("报价不能为负且数量必须大于 0")
        with transaction(self.db_path) as conn:
            if not get_active_entity(conn, "batches", batch_id):
                raise NotFoundError("库存批次不存在或已删除")
            quote_id = insert_quote(
                conn,
                batch_id=batch_id,
                customer_id=customer_id,
                quote_price_cents=quote_price_cents,
                quote_quantity=quote_quantity,
                quote_date=quote_date,
                remark=remark,
                tax_rate=tax_rate,
                purchase_tax_inclusive=purchase_tax_inclusive,
                quote_tax_inclusive=quote_tax_inclusive,
            )
            audit(
                conn,
                "quotes",
                quote_id,
                "create",
                after={
                    "batch_id": batch_id,
                    "customer_id": customer_id,
                    "quote_price_cents": quote_price_cents,
                    "quote_quantity": quote_quantity,
                    "total_cents": quote_price_cents * quote_quantity,
                    "status": "待确认",
                },
            )
            log_operation(conn, "新增报价", "quotes", quote_id, f"数量={quote_quantity}")
            return quote_id

    def update_quote(
        self,
        quote_id: int,
        *,
        batch_id: int,
        customer_id: int | None,
        quote_price_cents: int,
        quote_quantity: int,
        quote_date: str,
        remark: str = "",
        paid: str = "否",
        sn_list: str = "",
        tax_rate: float | None = None,
        purchase_tax_inclusive: bool = False,
        quote_tax_inclusive: bool = False,
    ) -> None:
        if quote_price_cents < 0 or quote_quantity <= 0:
            raise ValidationError("报价不能为负且数量必须大于 0")
        with transaction(self.db_path) as conn:
            before = get_active_entity(conn, "quotes", quote_id)
            if not before:
                raise NotFoundError("报价记录不存在或已删除")
            if before["status"] not in ("待确认", "已报价"):
                raise InvalidTransitionError("已出库、已收款或已取消报价不能直接编辑")
            if before["received_amount_cents"]:
                raise DataConflictError("已有收款分配的报价不能直接编辑")
            if not get_active_entity(conn, "batches", batch_id):
                raise NotFoundError("库存批次不存在或已删除")
            if customer_id is not None and not get_active_entity(
                conn, "customers", customer_id
            ):
                raise NotFoundError("客户不存在或已删除")
            update_quote_record(
                conn,
                quote_id,
                batch_id=batch_id,
                customer_id=customer_id,
                quote_price_cents=quote_price_cents,
                quote_quantity=quote_quantity,
                quote_date=quote_date,
                remark=remark,
                paid=paid,
                sn_list=sn_list,
                tax_rate=tax_rate,
                purchase_tax_inclusive=purchase_tax_inclusive,
                quote_tax_inclusive=quote_tax_inclusive,
            )
            audit(
                conn,
                "quotes",
                quote_id,
                "update",
                before=before,
                after={
                    "batch_id": batch_id,
                    "customer_id": customer_id,
                    "quote_price_cents": quote_price_cents,
                    "quote_quantity": quote_quantity,
                    "quote_date": quote_date,
                },
            )
            log_operation(conn, "编辑报价", "quotes", quote_id, f"数量={quote_quantity}")

    def transition(self, quote_id: int, new_status: str, reason: str = "") -> None:
        with transaction(self.db_path) as conn:
            quote = get_active_entity(conn, "quotes", quote_id)
            if not quote:
                raise NotFoundError("报价记录不存在")
            old_status = quote["status"]
            if new_status in ("已出库", "已收款"):
                raise InvalidTransitionError(
                    "出库和收款状态必须由完整业务事务产生，不能通过通用状态接口修改"
                )
            if old_status in ("已出库", "已收款") and new_status == "已取消":
                raise InvalidTransitionError(
                    "已出库订单不能直接取消，请使用销售退货"
                )
            if new_status not in VALID_TRANSITIONS.get(old_status, set()):
                raise InvalidTransitionError(f"不允许从「{old_status}」变更为「{new_status}」")
            set_quote_status(conn, quote_id, new_status)
            audit(
                conn,
                "quotes",
                quote_id,
                "transition",
                before={"status": old_status},
                after={"status": new_status},
                reason=reason,
            )
            log_operation(
                conn,
                "状态变更",
                "quotes",
                quote_id,
                f"{old_status}→{new_status}",
            )

    def cancel_quote(self, quote_id: int, reason: str = "") -> None:
        self.transition(quote_id, "已取消", reason)

    def delete_quote(self, quote_id: int, reason: str) -> None:
        with transaction(self.db_path) as conn:
            quote = get_quote(conn, quote_id)
            if not quote:
                raise NotFoundError("报价记录不存在")
            if quote["status"] not in ("待确认", "已取消"):
                raise InvalidTransitionError("已报价、已出库或已收款记录不能直接删除")
            soft_delete(conn, "quotes", quote_id, reason)
            log_operation(conn, "删除报价", "quotes", quote_id, reason)

