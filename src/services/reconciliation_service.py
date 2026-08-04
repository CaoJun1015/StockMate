"""Automatic inventory and ledger reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.models.queries import collect_reconciliation_snapshot


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    message: str
    entity_type: str | None = None
    entity_id: int | None = None


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
            lines.extend(
                f"{index}. [{issue.code}] {issue.message}"
                for index, issue in enumerate(self.issues, 1)
            )
        return "\n".join(lines)


class ReconciliationService:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def run(self) -> ReconciliationReport:
        snapshot = collect_reconciliation_snapshot(self.db_path)
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
                        f"批次 {row['id']} 库存不守恒：入库 {row['quantity']}，"
                        f"剩余 {row['remaining']}，现存已出库 {row['shipped']}"
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
                    "CENTS_MISMATCH",
                    f"{row['entity_type']}#{row['id']} 的 {row['field']} 元/分字段不一致",
                    row["entity_type"],
                    row["id"],
                )
            )
        for row in snapshot["receivables"]:
            issues.append(
                ReconciliationIssue(
                    "RECEIVABLE_RANGE",
                    (
                        f"报价 {row['id']} 总额 {row['total_cents']} 分，"
                        f"已收 {row['received_amount_cents']} 分"
                    ),
                    "quotes",
                    row["id"],
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

        return ReconciliationReport(
            checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            issues=tuple(issues),
            metrics={
                "table_counts": snapshot["table_counts"],
                "historical_stock_batches": snapshot["historical_stock_batches"],
                "historical_stock_units": snapshot["historical_stock_units"],
            },
        )
