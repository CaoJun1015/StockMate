"""Human-readable labels for internal database identifiers shown in the UI."""

from __future__ import annotations


# value: (business label, ID noun).  A None ID noun intentionally hides an
# implementation token such as the UUID used by manual journal entries.
SOURCE_LABELS: dict[str, tuple[str, str | None]] = {
    "finance_setup": ("财务启用·期初资金", "账户"),
    "finance_setup_quote": ("财务启用·期初应收", "报价单"),
    "finance_setup_supplier": ("财务启用·期初应付", "供应商"),
    "finance_setup_batch": ("财务启用·期初库存", "批次"),
    "financial_account": ("资金账户", "账户"),
    "account_reconciliation": ("账户核对", "账户"),
    "manual": ("手工记账", None),
    "manual_void": ("手工账作废", "账务流水"),
    "batch": ("采购入库", "批次"),
    "quote": ("销售出库", "报价单"),
    "payment": ("收付款", "流水"),
    "payment_reversal": ("收付款冲销", "流水"),
    "sales_return": ("销售退货", "退货单"),
    "purchase_return": ("采购退货", "退货单"),
}


OBJECT_LABELS: dict[str, str] = {
    "products": "机型",
    "batches": "进货批次",
    "customers": "客户",
    "suppliers": "供应商",
    "quotes": "报价/出库单",
    "payments": "收付款流水",
    "sales_returns": "销售退货单",
    "purchase_returns": "采购退货单",
    "ledger_accounts": "资金账户",
    "ledger_entries": "账务流水",
    "finance_categories": "收支分类",
}


def _has_id(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def format_source_label(source_type: str | None, source_id: object = None) -> str:
    """Translate a ledger source into business Chinese while retaining useful IDs."""
    source_type = (source_type or "").strip()
    if source_type not in SOURCE_LABELS:
        suffix = source_type or "未知"
        if _has_id(source_id):
            suffix += f" #{source_id}"
        return f"其他业务来源（{suffix}）"
    label, id_noun = SOURCE_LABELS[source_type]
    if id_noun and _has_id(source_id):
        return f"{label}（{id_noun} #{source_id}）"
    return label


def format_operation_object(table_name: str | None, record_id: object = None) -> str:
    """Translate an operation-log table name into a user-facing business object."""
    table_name = (table_name or "").strip()
    label = OBJECT_LABELS.get(table_name, "其他业务对象")
    if _has_id(record_id):
        return f"{label} #{record_id}"
    if table_name not in OBJECT_LABELS and table_name:
        return f"{label}（{table_name}）"
    return label
