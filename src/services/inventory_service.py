"""Inventory use cases and their transaction boundaries."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    adjust_supplier_balance,
    audit,
    decrement_batch_remaining,
    get_active_entity,
    insert_batch,
    list_active_batch_quotes,
    log_operation,
    set_quote_status,
    soft_delete,
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
                    "total_cents": purchase_price_cents * quantity,
                    "date": date,
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
            log_operation(conn, "出库", "quotes", quote_id, f"SN={sn_list}")

    def delete_batch(self, batch_id: int, reason: str = "用户删除批次") -> None:
        with transaction(self.db_path) as conn:
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
                    f"批次存在已报价、已出库或已收款记录，不能删除（报价 {blocking[0]['id']}）"
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

