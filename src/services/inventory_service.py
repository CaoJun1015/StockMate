"""Inventory use cases and their transaction boundaries."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    adjust_supplier_balance,
    audit,
    decrement_batch_remaining,
    get_active_entity,
    insert_batch,
    set_quote_status,
)
from src.services.exceptions import (
    InsufficientStockError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)


class InventoryService:
    def __init__(self, db_path=None):
        self.db_path = db_path

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
    ) -> int:
        if purchase_price_cents < 0 or quantity <= 0:
            raise ValidationError("进价不能为负且数量必须大于 0")
        with transaction(self.db_path) as conn:
            if not get_active_entity(conn, "products", product_id):
                raise NotFoundError("机型不存在或已删除")
            if supplier_id is not None and not get_active_entity(
                conn, "suppliers", supplier_id
            ):
                raise NotFoundError("供应商不存在或已删除")
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
            if supplier_id is not None:
                adjust_supplier_balance(
                    conn,
                    supplier_id,
                    purchase_price_cents * quantity,
                )
            audit(conn, "batches", batch_id, "receive", after={"quantity": quantity})
            return batch_id

    def ship_quote(self, quote_id: int, sn_list: str = "") -> None:
        with transaction(self.db_path) as conn:
            quote = get_active_entity(conn, "quotes", quote_id)
            if not quote:
                raise NotFoundError("报价记录不存在")
            if quote["status"] not in ("待确认", "已报价"):
                raise InvalidTransitionError(f"当前状态「{quote['status']}」不允许出库")
            batch = get_active_entity(conn, "batches", quote["batch_id"])
            if not batch or batch["remaining"] < quote["quote_quantity"]:
                raise InsufficientStockError("库存不足，无法出库")
            if not decrement_batch_remaining(
                conn,
                quote["batch_id"],
                quote["quote_quantity"],
            ):
                raise InsufficientStockError("库存不足，无法出库")
            set_quote_status(conn, quote_id, "已出库", sn_list=sn_list)
            audit(
                conn,
                "quotes",
                quote_id,
                "ship",
                before=dict(quote),
                after={"status": "已出库", "sn_list": sn_list},
            )

