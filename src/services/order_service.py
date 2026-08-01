"""Order/quote use cases."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import audit, cents_to_yuan, soft_delete
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
            batch = conn.execute(
                "SELECT id FROM batches WHERE id=? AND deleted_at IS NULL", (batch_id,)
            ).fetchone()
            if not batch:
                raise NotFoundError("库存批次不存在或已删除")
            cursor = conn.execute(
                "INSERT INTO quotes "
                "(batch_id,customer_id,quote_price,quote_price_cents,quote_quantity,"
                "quote_date,remark,paid,status,received_amount,received_amount_cents,"
                "sn_list,tax_rate,purchase_tax_inclusive,quote_tax_inclusive) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    customer_id,
                    cents_to_yuan(quote_price_cents),
                    quote_price_cents,
                    quote_quantity,
                    quote_date,
                    remark,
                    "否",
                    "待确认",
                    0,
                    0,
                    "",
                    tax_rate,
                    int(purchase_tax_inclusive),
                    int(quote_tax_inclusive),
                ),
            )
            quote_id = int(cursor.lastrowid)
            audit(conn, "quotes", quote_id, "create", after={"status": "待确认"})
            return quote_id

    def transition(self, quote_id: int, new_status: str, reason: str = "") -> None:
        with transaction(self.db_path) as conn:
            quote = conn.execute(
                "SELECT * FROM quotes WHERE id=? AND deleted_at IS NULL", (quote_id,)
            ).fetchone()
            if not quote:
                raise NotFoundError("报价记录不存在")
            old_status = quote["status"]
            if new_status not in VALID_TRANSITIONS.get(old_status, set()):
                raise InvalidTransitionError(f"不允许从「{old_status}」变更为「{new_status}」")
            if old_status == "已出库" and new_status == "已取消":
                conn.execute(
                    "UPDATE batches SET remaining=remaining+? WHERE id=?",
                    (quote["quote_quantity"], quote["batch_id"]),
                )
                conn.execute(
                    "UPDATE quotes SET status=?, sn_list='' WHERE id=?",
                    (new_status, quote_id),
                )
            else:
                conn.execute("UPDATE quotes SET status=? WHERE id=?", (new_status, quote_id))
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
            quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
            if not quote:
                raise NotFoundError("报价记录不存在")
            if quote["status"] not in ("待确认", "已取消"):
                raise InvalidTransitionError("已报价、已出库或已收款记录不能直接删除")
            soft_delete(conn, "quotes", quote_id, reason)

