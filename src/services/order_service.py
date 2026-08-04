"""Order/quote use cases."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    audit,
    get_active_entity,
    get_quote,
    increment_batch_remaining,
    insert_quote,
    set_quote_status,
    soft_delete,
)
from src.services.exceptions import InvalidTransitionError, NotFoundError, ValidationError


VALID_TRANSITIONS = {
    "待确认": {"已报价", "已取消"},
    "已报价": {"已出库", "已取消"},
    "已出库": {"已收款", "已取消"},
    "已收款": set(),
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
            audit(conn, "quotes", quote_id, "create", after={"status": "待确认"})
            return quote_id

    def transition(self, quote_id: int, new_status: str, reason: str = "") -> None:
        with transaction(self.db_path) as conn:
            quote = get_active_entity(conn, "quotes", quote_id)
            if not quote:
                raise NotFoundError("报价记录不存在")
            old_status = quote["status"]
            if new_status not in VALID_TRANSITIONS.get(old_status, set()):
                raise InvalidTransitionError(f"不允许从「{old_status}」变更为「{new_status}」")
            if old_status == "已出库" and new_status == "已取消":
                increment_batch_remaining(
                    conn,
                    quote["batch_id"],
                    quote["quote_quantity"],
                )
                set_quote_status(conn, quote_id, new_status, sn_list="")
            else:
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

