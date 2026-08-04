"""Internal balanced-ledger writer used inside existing service transactions."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from src.models.finance_repository import (
    find_ledger_entry_by_key,
    get_finance_settings,
    get_ledger_account,
    get_ledger_account_by_code,
    get_ledger_entry,
    insert_ledger_entry,
    insert_ledger_line,
    ledger_entry_has_reversal,
    list_ledger_lines,
    set_ledger_entry_status,
)
from src.services.exceptions import (
    DataConflictError,
    NotFoundError,
    ValidationError,
)


class LedgerPostingService:
    """Post immutable, balanced entries without owning the transaction."""

    @staticmethod
    def validate_date(value: str) -> str:
        try:
            return date.fromisoformat(value).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValidationError("日期必须为 YYYY-MM-DD") from exc

    @staticmethod
    def require_enabled(conn, entry_date: str) -> dict[str, Any]:
        settings = get_finance_settings(conn)
        enabled_at = settings.get("enabled_at")
        if not enabled_at:
            raise ValidationError("请先在账款管理中完成财务启用")
        normalized = LedgerPostingService.validate_date(entry_date)
        if normalized < enabled_at:
            raise ValidationError(f"业务日期不能早于财务启用日 {enabled_at}")
        return settings

    @staticmethod
    def account_id(conn, code: str) -> int:
        account = get_ledger_account_by_code(conn, code)
        if not account:
            raise DataConflictError(f"缺少系统账本科目 {code}")
        return int(account["id"])

    @staticmethod
    def require_cash_account(conn, account_id: int) -> dict[str, Any]:
        account = get_ledger_account(conn, account_id)
        if (
            not account
            or account["is_system"]
            or not account["is_active"]
            or account["deleted_at"] is not None
            or account["account_type"] != "asset"
        ):
            raise ValidationError("请选择有效的资金账户")
        return account

    @classmethod
    def post(
        cls,
        conn,
        *,
        entry_date: str,
        event_type: str,
        source_type: str,
        source_id: str | int | None,
        idempotency_key: str,
        lines: Iterable[dict[str, Any]],
        status: str = "normal",
        reversal_of_id: int | None = None,
        supersedes_id: int | None = None,
        reason: str = "",
        remark: str = "",
    ) -> int:
        normalized_date = cls.validate_date(entry_date)
        existing = find_ledger_entry_by_key(conn, idempotency_key)
        if existing:
            return int(existing["id"])

        prepared: list[dict[str, Any]] = []
        debit_total = 0
        credit_total = 0
        for raw in lines:
            line = dict(raw)
            debit = line.get("debit_cents", 0)
            credit = line.get("credit_cents", 0)
            if (
                isinstance(debit, bool)
                or isinstance(credit, bool)
                or not isinstance(debit, int)
                or not isinstance(credit, int)
                or debit < 0
                or credit < 0
                or (debit > 0) == (credit > 0)
            ):
                raise ValidationError("账本分录必须使用正整数分且只能有借或贷一方")
            if "account_id" not in line:
                line["account_id"] = cls.account_id(conn, line.pop("account_code"))
            debit_total += debit
            credit_total += credit
            prepared.append(line)
        if not prepared or debit_total != credit_total:
            raise DataConflictError(
                f"账本分录不平衡：借方 {debit_total} 分，贷方 {credit_total} 分"
            )

        entry_id = insert_ledger_entry(
            conn,
            entry_date=normalized_date,
            event_type=event_type,
            source_type=source_type,
            source_id=str(source_id) if source_id is not None else None,
            idempotency_key=idempotency_key,
            status=status,
            reversal_of_id=reversal_of_id,
            supersedes_id=supersedes_id,
            reason=reason,
            remark=remark,
        )
        for line in prepared:
            insert_ledger_line(
                conn,
                entry_id=entry_id,
                account_id=int(line["account_id"]),
                debit_cents=int(line.get("debit_cents", 0)),
                credit_cents=int(line.get("credit_cents", 0)),
                customer_id=line.get("customer_id"),
                supplier_id=line.get("supplier_id"),
                quote_id=line.get("quote_id"),
                batch_id=line.get("batch_id"),
                category_id=line.get("category_id"),
            )
        return entry_id

    @classmethod
    def reverse(
        cls,
        conn,
        *,
        entry_id: int,
        entry_date: str,
        idempotency_key: str,
        source_type: str,
        source_id: str | int | None,
        reason: str,
    ) -> int:
        if not reason.strip():
            raise ValidationError("冲销原因不能为空")
        original = get_ledger_entry(conn, entry_id)
        if not original:
            raise NotFoundError("账本记录不存在")
        if original["status"] in ("reversed", "reversal"):
            raise DataConflictError("该账本记录不能再次冲销")
        if ledger_entry_has_reversal(conn, entry_id):
            raise DataConflictError("该账本记录已经冲销")
        reversed_lines = []
        for line in list_ledger_lines(conn, entry_id):
            reversed_lines.append(
                {
                    "account_id": line["account_id"],
                    "debit_cents": line["credit_cents"],
                    "credit_cents": line["debit_cents"],
                    "customer_id": line["customer_id"],
                    "supplier_id": line["supplier_id"],
                    "quote_id": line["quote_id"],
                    "batch_id": line["batch_id"],
                    "category_id": line["category_id"],
                }
            )
        reversal_id = cls.post(
            conn,
            entry_date=entry_date,
            event_type=f"{original['event_type']}_reversal",
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            lines=reversed_lines,
            status="reversal",
            reversal_of_id=entry_id,
            reason=reason,
        )
        set_ledger_entry_status(conn, entry_id, "reversed")
        return reversal_id
