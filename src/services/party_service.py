"""Customer and supplier write use cases."""

from __future__ import annotations

from src.models.connection import transaction
from src.models.repositories import (
    audit,
    get_active_entity,
    insert_customer,
    insert_supplier,
    log_operation,
    soft_delete,
    update_customer,
    update_supplier,
)
from src.services.exceptions import NotFoundError, ValidationError


def _clean_name(name: str, label: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValidationError(f"{label}名称不能为空")
    return cleaned


class CustomerService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def create(
        self,
        *,
        name: str,
        wechat: str = "",
        qq: str = "",
        phone: str = "",
        note: str = "",
        default_tax_rate: float | None = None,
    ) -> int:
        name = _clean_name(name, "客户")
        with transaction(self.db_path) as conn:
            customer_id = insert_customer(
                conn,
                name=name,
                wechat=wechat,
                qq=qq,
                phone=phone,
                note=note,
                default_tax_rate=default_tax_rate,
            )
            audit(
                conn,
                "customers",
                customer_id,
                "create",
                after={"name": name},
            )
            log_operation(conn, "新增客户", "customers", customer_id, f"名称={name}")
            return customer_id

    def update(
        self,
        customer_id: int,
        *,
        name: str,
        wechat: str = "",
        qq: str = "",
        phone: str = "",
        note: str = "",
        default_tax_rate: float | None = None,
    ) -> None:
        name = _clean_name(name, "客户")
        with transaction(self.db_path) as conn:
            before = get_active_entity(conn, "customers", customer_id)
            if not before:
                raise NotFoundError("客户不存在或已删除")
            update_customer(
                conn,
                customer_id,
                name=name,
                wechat=wechat,
                qq=qq,
                phone=phone,
                note=note,
                default_tax_rate=default_tax_rate,
            )
            audit(
                conn,
                "customers",
                customer_id,
                "update",
                before=before,
                after={
                    "name": name,
                    "wechat": wechat,
                    "qq": qq,
                    "phone": phone,
                    "note": note,
                    "default_tax_rate": default_tax_rate,
                },
            )
            log_operation(conn, "编辑客户", "customers", customer_id, f"名称={name}")

    def delete(self, customer_id: int, reason: str = "用户删除客户") -> bool:
        with transaction(self.db_path) as conn:
            customer = get_active_entity(conn, "customers", customer_id)
            if not customer:
                raise NotFoundError("客户不存在或已删除")
            deleted = soft_delete(conn, "customers", customer_id, reason)
            log_operation(
                conn,
                "删除客户",
                "customers",
                customer_id,
                f"名称={customer['name']}",
            )
            return deleted


class SupplierService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def create(
        self,
        *,
        name: str,
        wechat: str = "",
        qq: str = "",
        phone: str = "",
        note: str = "",
        **_ignored,
    ) -> int:
        name = _clean_name(name, "上游")
        with transaction(self.db_path) as conn:
            supplier_id = insert_supplier(
                conn,
                name=name,
                wechat=wechat,
                qq=qq,
                phone=phone,
                note=note,
            )
            audit(
                conn,
                "suppliers",
                supplier_id,
                "create",
                after={"name": name},
            )
            log_operation(conn, "新增供应商", "suppliers", supplier_id, f"名称={name}")
            return supplier_id

    def update(
        self,
        supplier_id: int,
        *,
        name: str,
        wechat: str = "",
        qq: str = "",
        phone: str = "",
        note: str = "",
        **_ignored,
    ) -> None:
        name = _clean_name(name, "上游")
        with transaction(self.db_path) as conn:
            before = get_active_entity(conn, "suppliers", supplier_id)
            if not before:
                raise NotFoundError("上游不存在或已删除")
            update_supplier(
                conn,
                supplier_id,
                name=name,
                wechat=wechat,
                qq=qq,
                phone=phone,
                note=note,
            )
            audit(
                conn,
                "suppliers",
                supplier_id,
                "update",
                before=before,
                after={
                    "name": name,
                    "wechat": wechat,
                    "qq": qq,
                    "phone": phone,
                    "note": note,
                },
            )
            log_operation(conn, "编辑供应商", "suppliers", supplier_id, f"名称={name}")

    def delete(self, supplier_id: int, reason: str = "用户删除供应商") -> bool:
        with transaction(self.db_path) as conn:
            supplier = get_active_entity(conn, "suppliers", supplier_id)
            if not supplier:
                raise NotFoundError("上游不存在或已删除")
            deleted = soft_delete(conn, "suppliers", supplier_id, reason)
            log_operation(
                conn,
                "删除供应商",
                "suppliers",
                supplier_id,
                f"名称={supplier['name']}",
            )
            return deleted
