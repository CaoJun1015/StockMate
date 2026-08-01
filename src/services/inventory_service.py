"""Inventory use cases and their transaction boundaries."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import audit, cents_to_yuan
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
            product = conn.execute(
                "SELECT id FROM products WHERE id=? AND deleted_at IS NULL",
                (product_id,),
            ).fetchone()
            if not product:
                raise NotFoundError("机型不存在或已删除")
            cursor = conn.execute(
                "INSERT INTO batches "
                "(product_id,purchase_price,purchase_price_cents,quantity,remaining,"
                "date,remark,supplier_id,sn_list) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    product_id,
                    cents_to_yuan(purchase_price_cents),
                    purchase_price_cents,
                    quantity,
                    quantity,
                    date,
                    remark,
                    supplier_id,
                    sn_list,
                ),
            )
            batch_id = int(cursor.lastrowid)
            audit(conn, "batches", batch_id, "receive", after={"quantity": quantity})
            return batch_id

    def ship_quote(self, quote_id: int, sn_list: str = "") -> None:
        with transaction(self.db_path) as conn:
            quote = conn.execute(
                "SELECT * FROM quotes WHERE id=? AND deleted_at IS NULL", (quote_id,)
            ).fetchone()
            if not quote:
                raise NotFoundError("报价记录不存在")
            if quote["status"] not in ("待确认", "已报价"):
                raise InvalidTransitionError(f"当前状态「{quote['status']}」不允许出库")
            batch = conn.execute(
                "SELECT * FROM batches WHERE id=? AND deleted_at IS NULL",
                (quote["batch_id"],),
            ).fetchone()
            if not batch or batch["remaining"] < quote["quote_quantity"]:
                raise InsufficientStockError("库存不足，无法出库")
            conn.execute(
                "UPDATE batches SET remaining=remaining-? WHERE id=?",
                (quote["quote_quantity"], quote["batch_id"]),
            )
            conn.execute(
                "UPDATE quotes SET status='已出库', sn_list=? WHERE id=?",
                (sn_list, quote_id),
            )
            audit(
                conn,
                "quotes",
                quote_id,
                "ship",
                before=dict(quote),
                after={"status": "已出库", "sn_list": sn_list},
            )

