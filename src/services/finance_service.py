"""Operating-finance setup, accounts, and manual bookkeeping use cases."""

from __future__ import annotations

from uuid import uuid4

from src.models.connection import transaction
from src.models.finance_repository import (
    account_balance_cents,
    add_supplier_payment_allocation,
    finance_setup_control_totals,
    get_finance_category,
    get_finance_settings,
    get_ledger_account,
    get_ledger_entry,
    has_supplier_payment_allocations,
    insert_finance_category,
    insert_ledger_account,
    list_nonzero_supplier_balances,
    list_open_customer_balances,
    list_open_receivable_quotes,
    list_opening_inventory_batches,
    list_supplier_batches_for_allocation,
    list_supplier_payment_allocations,
    list_supplier_payments_for_allocation,
    reset_customer_balances,
    set_customer_balance,
    set_finance_enabled,
    update_finance_category,
    update_ledger_account,
)
from src.models.repositories import audit
from src.services.exceptions import (
    DataConflictError,
    NotFoundError,
    ValidationError,
)
from src.services.ledger_posting_service import LedgerPostingService


DEFAULT_ACCOUNT_NAMES = ("微信", "支付宝", "银行卡", "现金", "其他")


class FinanceService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _positive_cents(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError("金额必须是大于 0 的整数分")
        return value

    @staticmethod
    def _category(conn, category_id: int, kind: str):
        row = get_finance_category(
            conn,
            category_id,
            kind=kind,
            active_only=True,
        )
        if not row:
            raise ValidationError("请选择有效的收支分类")
        return row

    @staticmethod
    def _opening_lines(
        debit_code: str,
        credit_code: str,
        amount_cents: int,
        **dimensions,
    ) -> list[dict]:
        return [
            {"account_code": debit_code, "debit_cents": amount_cents, **dimensions},
            {"account_code": credit_code, "credit_cents": amount_cents, **dimensions},
        ]

    def _backfill_supplier_allocations(self, conn) -> None:
        if has_supplier_payment_allocations(conn):
            return
        remaining_by_supplier: dict[int, list[dict]] = {}
        for row in list_supplier_batches_for_allocation(conn):
            remaining_by_supplier.setdefault(row["supplier_id"], []).append(
                {"batch_id": row["id"], "remaining": row["total_cents"]}
            )
        for payment in list_supplier_payments_for_allocation(conn):
            if payment["entry_kind"] == "reversal" and payment["reversal_of_id"]:
                originals = list_supplier_payment_allocations(
                    conn,
                    payment["reversal_of_id"],
                )
                for original in originals:
                    add_supplier_payment_allocation(
                        conn,
                        payment["id"],
                        original["batch_id"],
                        -original["amount_cents"],
                    )
                    for batch in remaining_by_supplier.get(
                        payment["supplier_id"], []
                    ):
                        if batch["batch_id"] == original["batch_id"]:
                            batch["remaining"] += original["amount_cents"]
                            break
                continue
            remaining = payment["amount_cents"]
            for batch in remaining_by_supplier.get(payment["supplier_id"], []):
                if batch["remaining"] <= 0:
                    continue
                applied = min(remaining, batch["remaining"])
                if applied:
                    add_supplier_payment_allocation(
                        conn, payment["id"], batch["batch_id"], applied
                    )
                    batch["remaining"] -= applied
                    remaining -= applied
                if remaining == 0:
                    break

    def initialize_finance(
        self,
        enabled_at: str,
        accounts: list[dict],
    ) -> list[int]:
        enabled_at = LedgerPostingService.validate_date(enabled_at)
        if not accounts:
            raise ValidationError("至少需要一个资金账户")
        with transaction(self.db_path, immediate=True) as conn:
            settings = get_finance_settings(conn)
            if settings.get("enabled_at"):
                raise DataConflictError("财务已经启用，不能重复初始化")
            account_ids: list[int] = []
            for index, item in enumerate(accounts):
                name = str(item.get("name", "")).strip()
                opening = item.get("opening_balance_cents", 0)
                if not name:
                    raise ValidationError("资金账户名称不能为空")
                if isinstance(opening, bool) or not isinstance(opening, int):
                    raise ValidationError("期初余额必须使用整数分")
                account_id = insert_ledger_account(
                    conn,
                    code=f"FUNDS:{uuid4().hex}",
                    name=name,
                )
                account_ids.append(account_id)
                if opening:
                    if opening > 0:
                        lines = [
                            {
                                "account_id": account_id,
                                "debit_cents": opening,
                            },
                            {
                                "account_code": "OWNER_EQUITY",
                                "credit_cents": opening,
                            },
                        ]
                    else:
                        amount = -opening
                        lines = [
                            {
                                "account_code": "OWNER_EQUITY",
                                "debit_cents": amount,
                            },
                            {
                                "account_id": account_id,
                                "credit_cents": amount,
                            },
                        ]
                    LedgerPostingService.post(
                        conn,
                        entry_date=enabled_at,
                        event_type="opening_funds",
                        source_type="finance_setup",
                        source_id=account_id,
                        idempotency_key=f"finance-opening:account:{account_id}",
                        lines=lines,
                        remark=f"{name}期初余额",
                    )

            reset_customer_balances(conn)
            for customer in list_open_customer_balances(conn):
                set_customer_balance(
                    conn,
                    customer["customer_id"],
                    customer["open_cents"],
                )

            for quote in list_open_receivable_quotes(conn):
                LedgerPostingService.post(
                    conn,
                    entry_date=enabled_at,
                    event_type="opening_receivable",
                    source_type="finance_setup_quote",
                    source_id=quote["id"],
                    idempotency_key=f"finance-opening:quote:{quote['id']}",
                    lines=self._opening_lines(
                        "AR",
                        "OWNER_EQUITY",
                        quote["open_cents"],
                        customer_id=quote["customer_id"],
                        quote_id=quote["id"],
                    ),
                )

            for supplier in list_nonzero_supplier_balances(conn):
                amount = abs(supplier["balance_cents"])
                if supplier["balance_cents"] > 0:
                    lines = self._opening_lines(
                        "OWNER_EQUITY",
                        "AP",
                        amount,
                        supplier_id=supplier["id"],
                    )
                else:
                    lines = self._opening_lines(
                        "AP",
                        "OWNER_EQUITY",
                        amount,
                        supplier_id=supplier["id"],
                    )
                LedgerPostingService.post(
                    conn,
                    entry_date=enabled_at,
                    event_type="opening_payable",
                    source_type="finance_setup_supplier",
                    source_id=supplier["id"],
                    idempotency_key=f"finance-opening:supplier:{supplier['id']}",
                    lines=lines,
                )

            for batch in list_opening_inventory_batches(conn):
                amount = batch["remaining"] * batch["purchase_price_cents"]
                if amount:
                    LedgerPostingService.post(
                        conn,
                        entry_date=enabled_at,
                        event_type="opening_inventory",
                        source_type="finance_setup_batch",
                        source_id=batch["id"],
                        idempotency_key=f"finance-opening:batch:{batch['id']}",
                        lines=self._opening_lines(
                            "INVENTORY",
                            "OWNER_EQUITY",
                            amount,
                            batch_id=batch["id"],
                        ),
                    )
            self._backfill_supplier_allocations(conn)
            controls = finance_setup_control_totals(conn)
            mismatches = []
            for cache_key, ledger_key, label in (
                (
                    "customer_cache_cents",
                    "customer_ledger_cents",
                    "客户应收",
                ),
                (
                    "supplier_cache_cents",
                    "supplier_ledger_cents",
                    "供应商应付",
                ),
                (
                    "inventory_expected_cents",
                    "inventory_ledger_cents",
                    "库存",
                ),
            ):
                if controls[cache_key] != controls[ledger_key]:
                    mismatches.append(
                        f"{label} {controls[cache_key]} != {controls[ledger_key]}"
                    )
            if controls["unbalanced_entries"]:
                mismatches.append(
                    f"不平衡账本 {controls['unbalanced_entries']} 条"
                )
            if mismatches:
                raise DataConflictError(
                    "财务启用对账失败：" + "；".join(mismatches)
                )
            set_finance_enabled(conn, enabled_at)
            audit(
                conn,
                "finance_settings",
                1,
                "initialize",
                after={
                    "enabled_at": enabled_at,
                    "account_ids": account_ids,
                },
            )
            return account_ids

    def create_account(
        self,
        name: str,
        *,
        opening_balance_cents: int = 0,
    ) -> int:
        name = name.strip()
        if not name:
            raise ValidationError("账户名称不能为空")
        if isinstance(opening_balance_cents, bool) or not isinstance(
            opening_balance_cents, int
        ):
            raise ValidationError("期初余额必须使用整数分")
        with transaction(self.db_path) as conn:
            settings = get_finance_settings(conn)
            if not settings.get("enabled_at"):
                raise ValidationError("请先完成财务启用")
            account_id = insert_ledger_account(
                conn,
                code=f"FUNDS:{uuid4().hex}",
                name=name,
            )
            if opening_balance_cents:
                amount = abs(opening_balance_cents)
                lines = (
                    [
                        {"account_id": account_id, "debit_cents": amount},
                        {
                            "account_code": "OWNER_EQUITY",
                            "credit_cents": amount,
                        },
                    ]
                    if opening_balance_cents > 0
                    else [
                        {
                            "account_code": "OWNER_EQUITY",
                            "debit_cents": amount,
                        },
                        {"account_id": account_id, "credit_cents": amount},
                    ]
                )
                LedgerPostingService.post(
                    conn,
                    entry_date=settings["enabled_at"],
                    event_type="account_opening",
                    source_type="financial_account",
                    source_id=account_id,
                    idempotency_key=f"account-opening:{account_id}",
                    lines=lines,
                )
            audit(
                conn,
                "ledger_accounts",
                account_id,
                "create",
                after={"name": name, "opening_balance_cents": opening_balance_cents},
            )
            return account_id

    def update_account(self, account_id: int, *, name: str, is_active: bool) -> None:
        name = name.strip()
        if not name:
            raise ValidationError("账户名称不能为空")
        with transaction(self.db_path) as conn:
            account = get_ledger_account(conn, account_id)
            if not account or account["is_system"]:
                raise NotFoundError("资金账户不存在")
            update_ledger_account(
                conn, account_id, name=name, is_active=is_active
            )
            audit(
                conn,
                "ledger_accounts",
                account_id,
                "update",
                before=account,
                after={"name": name, "is_active": is_active},
            )

    def create_category(self, name: str, kind: str) -> int:
        name = name.strip()
        if kind not in ("income", "expense") or not name:
            raise ValidationError("分类名称或类型无效")
        with transaction(self.db_path) as conn:
            category_id = insert_finance_category(
                conn, name=name, kind=kind
            )
            audit(
                conn,
                "finance_categories",
                category_id,
                "create",
                after={"name": name, "kind": kind},
            )
            return category_id

    def update_category(
        self,
        category_id: int,
        *,
        name: str,
        is_active: bool,
    ) -> None:
        name = name.strip()
        if not name:
            raise ValidationError("分类名称不能为空")
        with transaction(self.db_path) as conn:
            before = get_finance_category(conn, category_id)
            if not before:
                raise NotFoundError("收支分类不存在")
            update_finance_category(
                conn, category_id, name=name, is_active=is_active
            )
            audit(
                conn,
                "finance_categories",
                category_id,
                "update",
                before=before,
                after={"name": name, "is_active": is_active},
            )

    def record_income(
        self,
        *,
        account_id: int,
        category_id: int,
        amount_cents: int,
        entry_date: str,
        remark: str = "",
        customer_id: int | None = None,
        supplier_id: int | None = None,
        quote_id: int | None = None,
    ) -> int:
        amount_cents = self._positive_cents(amount_cents)
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            LedgerPostingService.require_cash_account(conn, account_id)
            self._category(conn, category_id, "income")
            token = uuid4().hex
            return LedgerPostingService.post(
                conn,
                entry_date=entry_date,
                event_type="manual_income",
                source_type="manual",
                source_id=token,
                idempotency_key=f"manual-income:{token}",
                lines=[
                    {
                        "account_id": account_id,
                        "debit_cents": amount_cents,
                        "customer_id": customer_id,
                        "supplier_id": supplier_id,
                        "quote_id": quote_id,
                        "category_id": category_id,
                    },
                    {
                        "account_code": "OTHER_INCOME",
                        "credit_cents": amount_cents,
                        "customer_id": customer_id,
                        "supplier_id": supplier_id,
                        "quote_id": quote_id,
                        "category_id": category_id,
                    },
                ],
                remark=remark,
            )

    def record_expense(
        self,
        *,
        account_id: int,
        category_id: int,
        amount_cents: int,
        entry_date: str,
        remark: str = "",
        customer_id: int | None = None,
        supplier_id: int | None = None,
        quote_id: int | None = None,
    ) -> int:
        amount_cents = self._positive_cents(amount_cents)
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            LedgerPostingService.require_cash_account(conn, account_id)
            self._category(conn, category_id, "expense")
            token = uuid4().hex
            return LedgerPostingService.post(
                conn,
                entry_date=entry_date,
                event_type="manual_expense",
                source_type="manual",
                source_id=token,
                idempotency_key=f"manual-expense:{token}",
                lines=[
                    {
                        "account_code": "EXPENSE",
                        "debit_cents": amount_cents,
                        "customer_id": customer_id,
                        "supplier_id": supplier_id,
                        "quote_id": quote_id,
                        "category_id": category_id,
                    },
                    {
                        "account_id": account_id,
                        "credit_cents": amount_cents,
                        "customer_id": customer_id,
                        "supplier_id": supplier_id,
                        "quote_id": quote_id,
                        "category_id": category_id,
                    },
                ],
                remark=remark,
            )

    def transfer(
        self,
        *,
        from_account_id: int,
        to_account_id: int,
        amount_cents: int,
        entry_date: str,
        remark: str = "",
    ) -> int:
        amount_cents = self._positive_cents(amount_cents)
        if from_account_id == to_account_id:
            raise ValidationError("转出和转入账户不能相同")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            LedgerPostingService.require_cash_account(conn, from_account_id)
            LedgerPostingService.require_cash_account(conn, to_account_id)
            token = uuid4().hex
            return LedgerPostingService.post(
                conn,
                entry_date=entry_date,
                event_type="account_transfer",
                source_type="manual",
                source_id=token,
                idempotency_key=f"account-transfer:{token}",
                lines=[
                    {"account_id": to_account_id, "debit_cents": amount_cents},
                    {"account_id": from_account_id, "credit_cents": amount_cents},
                ],
                remark=remark,
            )

    def adjust_account(
        self,
        *,
        account_id: int,
        delta_cents: int,
        entry_date: str,
        reason: str,
    ) -> int:
        if not delta_cents or not reason.strip():
            raise ValidationError("调整金额不能为 0，且必须填写原因")
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            LedgerPostingService.require_cash_account(conn, account_id)
            amount = abs(delta_cents)
            lines = (
                [
                    {"account_id": account_id, "debit_cents": amount},
                    {"account_code": "OWNER_EQUITY", "credit_cents": amount},
                ]
                if delta_cents > 0
                else [
                    {"account_code": "OWNER_EQUITY", "debit_cents": amount},
                    {"account_id": account_id, "credit_cents": amount},
                ]
            )
            token = uuid4().hex
            return LedgerPostingService.post(
                conn,
                entry_date=entry_date,
                event_type="funds_adjustment",
                source_type="manual",
                source_id=token,
                idempotency_key=f"funds-adjustment:{token}",
                lines=lines,
                reason=reason,
            )

    def reconcile_account(
        self,
        *,
        account_id: int,
        actual_balance_cents: int,
        entry_date: str,
        reason: str,
    ) -> int | None:
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            LedgerPostingService.require_cash_account(conn, account_id)
            current = account_balance_cents(conn, account_id)
            delta = actual_balance_cents - current
            if delta == 0:
                return None
            if not reason.strip():
                raise ValidationError("账户核对必须填写原因")
            amount = abs(delta)
            lines = (
                [
                    {"account_id": account_id, "debit_cents": amount},
                    {"account_code": "OWNER_EQUITY", "credit_cents": amount},
                ]
                if delta > 0
                else [
                    {"account_code": "OWNER_EQUITY", "debit_cents": amount},
                    {"account_id": account_id, "credit_cents": amount},
                ]
            )
            token = uuid4().hex
            return LedgerPostingService.post(
                conn,
                entry_date=entry_date,
                event_type="funds_adjustment",
                source_type="account_reconciliation",
                source_id=token,
                idempotency_key=f"account-reconciliation:{token}",
                lines=lines,
                reason=reason,
            )

    def void_entry(
        self,
        entry_id: int,
        *,
        entry_date: str,
        reason: str,
    ) -> int:
        with transaction(self.db_path) as conn:
            LedgerPostingService.require_enabled(conn, entry_date)
            entry = get_ledger_entry(conn, entry_id)
            if not entry:
                raise NotFoundError("账本记录不存在")
            if entry["source_type"] != "manual":
                raise ValidationError("自动业务账必须从原业务执行退货、作废或更正")
            return LedgerPostingService.reverse(
                conn,
                entry_id=entry_id,
                entry_date=entry_date,
                source_type="manual_void",
                source_id=entry_id,
                idempotency_key=f"manual-void:{entry_id}",
                reason=reason,
            )
