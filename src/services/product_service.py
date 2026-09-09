"""Product catalog write use cases."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    adjust_supplier_balance,
    audit,
    get_active_entity,
    insert_product,
    list_active_batch_quotes,
    list_active_product_batches,
    list_product_batch_ids,
    log_operation,
    soft_delete,
    update_product,
)
from src.services.inventory_service import InventoryService
from src.services.exceptions import (
    NotFoundError,
    ValidationError,
)


class ProductService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _series(value: str) -> str:
        series = (value or "").strip()
        if not series:
            raise ValidationError("系列名称不能为空")
        return series

    def create(
        self,
        *,
        series: str,
        cpu: str = "",
        ram: str = "",
        storage: str = "",
        gpu: str = "",
        screen: str = "",
        note: str = "",
    ) -> int:
        series = self._series(series)
        with transaction(self.db_path) as conn:
            product_id = insert_product(
                conn,
                series=series,
                cpu=cpu,
                ram=ram,
                storage=storage,
                gpu=gpu,
                screen=screen,
                note=note,
            )
            audit(conn, "products", product_id, "create", after={"series": series})
            log_operation(conn, "新增机型", "products", product_id, f"系列={series}")
            return product_id

    def update(
        self,
        product_id: int,
        *,
        series: str,
        cpu: str = "",
        ram: str = "",
        storage: str = "",
        gpu: str = "",
        screen: str = "",
        note: str = "",
    ) -> None:
        series = self._series(series)
        with transaction(self.db_path) as conn:
            before = get_active_entity(conn, "products", product_id)
            if not before:
                raise NotFoundError("机型不存在或已删除")
            update_product(
                conn,
                product_id,
                series=series,
                cpu=cpu,
                ram=ram,
                storage=storage,
                gpu=gpu,
                screen=screen,
                note=note,
            )
            after = {
                "series": series,
                "cpu": cpu,
                "ram": ram,
                "storage": storage,
                "gpu": gpu,
                "screen": screen,
                "note": note,
            }
            audit(conn, "products", product_id, "update", before=before, after=after)
            log_operation(conn, "编辑机型", "products", product_id, f"系列={series}")

    def delete(self, product_id: int, reason: str = "用户删除机型") -> None:
        with transaction(self.db_path) as conn:
            product = get_active_entity(conn, "products", product_id)
            if not product:
                raise NotFoundError("机型不存在或已删除")
            batches = list_active_product_batches(conn, product_id)
            for batch_id in list_product_batch_ids(conn, product_id):
                InventoryService.require_batch_deletable(conn, batch_id)

            for batch in batches:
                for quote in list_active_batch_quotes(conn, batch["id"]):
                    soft_delete(conn, "quotes", quote["id"], "所属机型已删除")
                if batch["supplier_id"]:
                    adjust_supplier_balance(
                        conn,
                        batch["supplier_id"],
                        -(batch["purchase_price_cents"] * batch["quantity"]),
                    )
                soft_delete(conn, "batches", batch["id"], "所属机型已删除")
            soft_delete(conn, "products", product_id, reason)
            log_operation(
                conn,
                "删除机型",
                "products",
                product_id,
                f"系列={product['series']}",
            )
