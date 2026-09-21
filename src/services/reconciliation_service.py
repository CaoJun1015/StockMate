"""Automatic inventory and ledger reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.models.queries import collect_reconciliation_snapshots


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    message: str
    entity_type: str | None = None
    entity_id: int | None = None
    difference_cents: int | None = None
    evidence: str = ""
    recommendation: str = ""


@dataclass(frozen=True)
class ReconciliationReport:
    checked_at: str
    issues: tuple[ReconciliationIssue, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def format_text(self) -> str:
        counts = self.metrics.get("table_counts", {})
        count_text = "、".join(f"{name} {value}" for name, value in counts.items())
        lines = [
            f"检查时间：{self.checked_at}",
            f"基础记录：{count_text}",
            (
                "历史库存差异："
                f"{self.metrics.get('historical_stock_batches', 0)} 个批次 / "
                f"{self.metrics.get('historical_stock_units', 0)} 台"
                "（仅记录，不视为故障）"
            ),
        ]
        if self.is_clean:
            lines.append("结果：库存、账务、外键和金额一致性检查全部通过。")
        else:
            lines.append(f"结果：发现 {len(self.issues)} 个需要处理的问题：")
            for index, issue in enumerate(self.issues, 1):
                detail = f"{index}. [{issue.code}] {issue.message}"
                if issue.difference_cents is not None:
                    detail += f"；差异 {issue.difference_cents} 分"
                if issue.evidence:
                    detail += f"；证据：{issue.evidence}"
                if issue.recommendation:
                    detail += f"；建议：{issue.recommendation}"
                lines.append(detail)
        return "\n".join(lines)


class ReconciliationService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def run(self) -> ReconciliationReport:
        snapshot, finance = collect_reconciliation_snapshots(self.db_path)
        issues: list[ReconciliationIssue] = []

        for result in snapshot["integrity"]:
            if result != "ok":
                issues.append(
                    ReconciliationIssue(
                        "SQLITE_INTEGRITY",
                        f"SQLite 完整性检查失败：{result}",
                    )
                )
        for row in snapshot["foreign_keys"]:
            issues.append(
                ReconciliationIssue(
                    "FOREIGN_KEY",
                    f"外键失联：表 {row['table']}，行 {row['rowid']}，父表 {row['parent']}",
                    row["table"],
                    row["rowid"],
                )
            )
        for row in snapshot["inventory"]:
            issues.append(
                ReconciliationIssue(
                    "INVENTORY_BALANCE",
                    (
                        f"批次 {row['id']} 库存不守恒：缓存剩余 {row['remaining']}，"
                        f"流水余额 {row['movement_balance']}"
                    ),
                    "batches",
                    row["id"],
                )
            )
        for row in snapshot["quote_allocations"]:
            issues.append(
                ReconciliationIssue(
                    "QUOTE_ALLOCATION",
                    (
                        f"报价 {row['id']} 已收 {row['received_amount_cents']} 分，"
                        f"分配合计 {row['allocated_cents']} 分"
                    ),
                    "quotes",
                    row["id"],
                )
            )
        for row in snapshot["payment_allocations"]:
            issues.append(
                ReconciliationIssue(
                    "PAYMENT_ALLOCATION",
                    (
                        f"收款 {row['id']} 应分配 {row['expected_cents']} 分，"
                        f"实际分配 {row['allocated_cents']} 分"
                    ),
                    "payments",
                    row["id"],
                )
            )
        for row in snapshot["supplier_balances"]:
            issues.append(
                ReconciliationIssue(
                    "SUPPLIER_BALANCE",
                    (
                        f"上游 {row['id']}（{row['name']}）余额 {row['balance_cents']} 分，"
                        f"按采购与付款重算应为 {row['expected_cents']} 分"
                    ),
                    "suppliers",
                    row["id"],
                )
            )
        for row in snapshot["amounts"]:
            issues.append(
                ReconciliationIssue(
                    "CENTS_TYPE",
                    f"{row['entity_type']}#{row['id']} 的 {row['field']} 不是有效整数分",
                    row["entity_type"],
                    row["id"],
                )
            )
        for row in snapshot["receivables"]:
            issues.append(
                ReconciliationIssue(
                    "RECEIVABLE_RANGE",
                    f"报价 {row['id']} 净应收 {row['net_cents']} 分，"
                    f"已收 {row['received_amount_cents']} 分",
                    "quotes",
                    row["id"],
                    row["received_amount_cents"] - row["net_cents"],
                    f"原额 {row['total_cents']} 分，退货 {row['returned_cents']} 分",
                    "核对退货、收款分配和订单结清状态",
                )
            )
        for row in snapshot["net_allocations"]:
            issues.append(
                ReconciliationIssue(
                    "QUOTE_NET_ALLOCATION",
                    f"报价 {row['id']} 的收款分配超过净应收上限",
                    "quotes",
                    row["id"],
                    row["allocated_cents"] - row["net_cents"],
                    f"原额 {row['total_cents']} 分，退货 {row['returned_cents']} 分，"
                    f"分配 {row['allocated_cents']} 分",
                    "核对退货释放的收款来源，再按净应收重建分配",
                )
            )
        for row in snapshot["payment_owners"]:
            issues.append(
                ReconciliationIssue(
                    "PAYMENT_OWNER",
                    f"流水 {row['id']} 类型或关联对象无效",
                    "payments",
                    row["id"],
                )
            )

        subledger_matches_batches = (
            finance["movement_inventory_cents"]
            == finance["inventory"]["business_cents"]
        )
        if not finance["enabled"]:
            for key in (
                "unbalanced_entries",
                "customer_balances",
                "supplier_balances",
                "shipment_snapshots",
                "sales_returns",
                "customer_allocations",
                "supplier_allocations",
                "refund_sources",
                "shipment_allocations",
                "inventory_movements",
                "inventory_sns",
                "sn_ownership",
                "quote_statuses",
            ):
                finance[key] = []
            finance["inventory"] = {"business_cents": 0, "ledger_cents": 0}
        for row in finance["unbalanced_entries"]:
            issues.append(
                ReconciliationIssue(
                    "LEDGER_UNBALANCED",
                    f"账本#{row['id']}借方与贷方不平衡",
                    "ledger_entries",
                    row["id"],
                )
            )
        for row in finance["customer_balances"]:
            issues.append(
                ReconciliationIssue(
                    "CUSTOMER_LEDGER",
                    f"客户#{row['id']}业务余额与账本余额不一致",
                    "customers",
                    row["id"],
                )
            )
        for row in finance["supplier_balances"]:
            issues.append(
                ReconciliationIssue(
                    "SUPPLIER_LEDGER",
                    f"供应商#{row['id']}业务余额与账本余额不一致",
                    "suppliers",
                    row["id"],
                )
            )
        inventory = finance["inventory"]
        if inventory["business_cents"] != inventory["ledger_cents"]:
            issues.append(
                ReconciliationIssue(
                    "INVENTORY_LEDGER",
                    "库存业务金额与账本库存金额不一致",
                )
            )
        for row in finance["refund_sources"]:
            entity = "sales_returns" if row["owner_type"] == "customer" else "purchase_returns"
            issues.append(
                ReconciliationIssue(
                    "REFUND_SOURCE",
                    (
                        f"{row['owner_type']}#{row['owner_id']} 的退货#{row['id']}"
                        f"现金退款 {row['cash_refund_cents']} 分，"
                        f"仅能解释 {row['attributed_cents']} 分"
                    ),
                    entity,
                    row["id"],
                    row["cash_refund_cents"] - row["attributed_cents"],
                    f"退款来源已归因 {row['attributed_cents']} 分",
                    "补充原付款来源后再允许自动抵扣",
                )
            )
        for rows, code, label in (
            (finance["customer_allocations"], "CUSTOMER_PAYMENT_NET", "客户收款"),
            (finance["supplier_allocations"], "SUPPLIER_PAYMENT_NET", "供应商付款"),
        ):
            for row in rows:
                limit = (
                    -row["amount_cents"]
                    if row["entry_kind"] == "reversal"
                    else row["amount_cents"] - row["refunded_cents"]
                )
                issues.append(
                    ReconciliationIssue(
                        code,
                        f"{label}#{row['id']} 的可分配净额不足",
                        "payments",
                        row["id"],
                        row["allocated_cents"] - limit,
                        f"付款 {row['amount_cents']} 分，退款 {row['refunded_cents']} 分，"
                        f"实际分配 {row['allocated_cents']} 分",
                        "核对退款来源和付款分配，避免已退款金额再次抵扣",
                    )
                )
        for row in finance["quote_statuses"]:
            issues.append(
                ReconciliationIssue(
                    "QUOTE_STATUS",
                    f"报价#{row['id']} 状态“{row['status']}”缺少对应业务事实",
                    "quotes",
                    row["id"],
                    None,
                    f"出库快照={row['shipment_snapshot_id'] or '无'}，"
                    f"出库账本={'有' if row['shipment_ledger'] else '无'}，"
                    f"已收 {row['received_amount_cents']} / 净应收 {row['net_cents']} 分",
                    "通过出库、收款或退货事务更正；迁移历史请先人工核对证据",
                )
            )
        for row in finance["sn_ownership"]:
            issues.append(
                ReconciliationIssue(
                    "RETURN_SN_OWNERSHIP",
                    f"销售退货#{row['sales_return_id']} 的回库SN无法确认原出库归属",
                    "sales_return_allocations",
                    row["id"],
                    row["difference"],
                    f"原出库分配#{row['shipment_allocation_id']} SN={row['shipment_sn_list']}，"
                    f"退货SN={row['return_sn_list'] or '未知'}",
                    "核对实物SN；确认前不要把该设备作为可售SN处理",
                )
            )
        if not subledger_matches_batches:
            issues.append(
                ReconciliationIssue(
                    "INVENTORY_SUBLEDGER",
                    "库存流水成本余额与批次库存金额不一致",
                )
            )
        for key, code, entity in (
            ("shipment_snapshots", "SHIPMENT_SNAPSHOT", "quotes"),
            ("sales_returns", "RETURN_QUANTITY", "quotes"),
            ("shipment_allocations", "SHIPMENT_ALLOCATION", "shipment_snapshots"),
            ("inventory_movements", "INVENTORY_MOVEMENT", "batches"),
            ("inventory_sns", "INVENTORY_SN", "shipment_allocations"),
        ):
            for row in finance[key]:
                entity_id = row.get("id", row.get("quote_id"))
                issues.append(
                    ReconciliationIssue(
                        code,
                        f"{entity}#{entity_id}账务关系不守恒",
                        entity,
                        entity_id,
                    )
                )

        return ReconciliationReport(
            checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            issues=tuple(issues),
            metrics={
                "table_counts": snapshot["table_counts"],
                "historical_stock_batches": snapshot["historical_stock_batches"],
                "historical_stock_units": snapshot["historical_stock_units"],
            },
        )
