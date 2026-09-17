"""Read-only historical anomaly review with narrowly-scoped cache repair."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.models.connection import (
    BackupInfo,
    create_backup,
    database_version_and_integrity,
    file_sha256,
    transaction,
)
from src.models.finance_repository import get_historical_quote_cache
from src.models.schema import SCHEMA_VERSION
from src.models.repositories import audit, log_operation, update_quote_repair_cache
from src.services.exceptions import DataConflictError, ValidationError
from src.services.reconciliation_service import ReconciliationReport, ReconciliationService


@dataclass(frozen=True)
class HistoricalAnomaly:
    code: str
    classification: str
    entity_type: str | None
    entity_id: int | None
    related_objects: str
    evidence: str
    current_value: str
    proposed_value: str
    money_impact_cents: int | None
    inventory_impact: str
    action: str | None = None


@dataclass(frozen=True)
class HistoricalRepairPlan:
    report: ReconciliationReport
    anomalies: tuple[HistoricalAnomaly, ...]

    @property
    def repairable(self) -> tuple[HistoricalAnomaly, ...]:
        return tuple(item for item in self.anomalies if item.classification == "repairable")

    def format_text(self) -> str:
        labels = {
            "repairable": "可确定修正",
            "manual_review": "需人工核对",
            "unresolvable": "无法推断",
        }
        lines = [f"历史异常核对时间：{self.report.checked_at}"]
        for classification in labels:
            rows = [item for item in self.anomalies if item.classification == classification]
            lines.append(f"{labels[classification]}：{len(rows)} 项")
            for item in rows:
                impact = (
                    f"金额影响 {item.money_impact_cents} 分"
                    if item.money_impact_cents is not None
                    else "金额影响：未知"
                )
                lines.append(
                    f"- [{item.code}] {item.related_objects}；当前：{item.current_value}；"
                    f"拟修改：{item.proposed_value}；{impact}；库存影响：{item.inventory_impact}；"
                    f"证据：{item.evidence}"
                )
        return "\n".join(lines)


@dataclass(frozen=True)
class HistoricalRepairResult:
    backup: BackupInfo
    applied: tuple[HistoricalAnomaly, ...]
    remaining_report: ReconciliationReport


class HistoricalRepairService:
    """Repair only quote cache fields that can be recomputed from immutable evidence."""

    _MANUAL_CODES = {
        "CUSTOMER_PAYMENT_NET",
        "SUPPLIER_PAYMENT_NET",
        "QUOTE_NET_ALLOCATION",
    }
    _UNRESOLVABLE_CODES = {
        "REFUND_SOURCE",
        "RETURN_SN_OWNERSHIP",
        "SHIPMENT_SNAPSHOT",
        "RETURN_QUANTITY",
        "SHIPMENT_ALLOCATION",
        "INVENTORY_MOVEMENT",
        "INVENTORY_SN",
        "INVENTORY_BALANCE",
        "INVENTORY_SUBLEDGER",
        "INVENTORY_LEDGER",
        "FOREIGN_KEY",
        "SQLITE_INTEGRITY",
    }

    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _quote_cache(conn, quote_id: int) -> dict | None:
        row = get_historical_quote_cache(conn, quote_id)
        if not row or not row["shipped_quantity"] or not row["shipment_ledger"]:
            return None
        expected_received = int(row["allocated_cents"])
        expected_paid = "是" if row["net_cents"] > 0 and expected_received >= row["net_cents"] else "否"
        if row["returned_quantity"] >= row["shipped_quantity"]:
            expected_status = "已全退"
        elif row["net_cents"] > 0 and expected_received >= row["net_cents"]:
            expected_status = "已收款"
        else:
            expected_status = "已出库"
        return {
            "id": row["id"],
            "current": {
                "received_amount_cents": row["received_amount_cents"],
                "paid": row["paid"],
                "status": row["status"],
            },
            "proposed": {
                "received_amount_cents": expected_received,
                "paid": expected_paid,
                "status": expected_status,
            },
        }

    @staticmethod
    def _related(issue) -> str:
        return f"{issue.entity_type or '数据库'}#{issue.entity_id}" if issue.entity_id is not None else (issue.entity_type or "数据库")

    def audit(self) -> HistoricalRepairPlan:
        report = ReconciliationService(self.db_path).run()
        quote_issue_ids = {
            issue.entity_id
            for issue in report.issues
            if issue.code in {"QUOTE_ALLOCATION", "QUOTE_STATUS"} and issue.entity_id is not None
        }
        candidates = {}
        if quote_issue_ids:
            with transaction(self.db_path, immediate=False) as conn:
                for quote_id in quote_issue_ids:
                    cache = self._quote_cache(conn, quote_id)
                    if cache and cache["current"] != cache["proposed"]:
                        candidates[quote_id] = cache

        anomalies: list[HistoricalAnomaly] = []
        represented_quotes: set[int] = set()
        for issue in report.issues:
            if issue.entity_id in candidates and issue.code in {"QUOTE_ALLOCATION", "QUOTE_STATUS"}:
                if issue.entity_id in represented_quotes:
                    continue
                cache = candidates[issue.entity_id]
                anomalies.append(HistoricalAnomaly(
                    code="QUOTE_CACHE",
                    classification="repairable",
                    entity_type="quotes",
                    entity_id=issue.entity_id,
                    related_objects=f"报价#{issue.entity_id} / 出库快照与销售账本",
                    evidence=(
                        f"付款分配合计 {cache['proposed']['received_amount_cents']} 分；"
                        f"{issue.evidence or '存在已验证出库快照与销售账本'}"
                    ),
                    current_value=str(cache["current"]),
                    proposed_value=str(cache["proposed"]),
                    money_impact_cents=(
                        cache["proposed"]["received_amount_cents"]
                        - cache["current"]["received_amount_cents"]
                    ),
                    inventory_impact="0（不修改库存事件）",
                    action="refresh_quote_cache",
                ))
                represented_quotes.add(issue.entity_id)
                continue
            classification = (
                "manual_review" if issue.code in self._MANUAL_CODES
                else "unresolvable" if issue.code in self._UNRESOLVABLE_CODES
                else "manual_review"
            )
            anomalies.append(HistoricalAnomaly(
                code=issue.code,
                classification=classification,
                entity_type=issue.entity_type,
                entity_id=issue.entity_id,
                related_objects=self._related(issue),
                evidence=issue.evidence or issue.recommendation or "缺少可重算的原始证据",
                current_value=issue.message,
                proposed_value="不自动修改；导出后人工核对",
                money_impact_cents=issue.difference_cents,
                inventory_impact="未知（保留原库存事件）",
            ))
        return HistoricalRepairPlan(report, tuple(anomalies))

    def _create_verified_backup(self) -> BackupInfo:
        backup = create_backup(self.db_path, prefix="pre_historical_repair", retain=False)
        if backup is None:
            raise DataConflictError("历史修复前安全备份失败：数据库文件不存在")
        version, integrity = database_version_and_integrity(backup.path)
        if version != SCHEMA_VERSION or integrity != "ok" or file_sha256(backup.path) != backup.sha256:
            raise DataConflictError("历史修复前安全备份验证失败")
        return backup

    def apply(self, plan: HistoricalRepairPlan, *, reason: str) -> HistoricalRepairResult:
        if not reason.strip():
            raise ValidationError("历史修复必须填写审计原因")
        if not plan.repairable:
            raise ValidationError("没有证据充分的可修复异常")
        backup = self._create_verified_backup()
        applied: list[HistoricalAnomaly] = []
        with transaction(self.db_path) as conn:
            for item in plan.repairable:
                if item.action != "refresh_quote_cache" or item.entity_id is None:
                    continue
                cache = self._quote_cache(conn, item.entity_id)
                if cache is None:
                    raise DataConflictError(f"报价#{item.entity_id} 的出库证据已变化，请重新核对")
                if cache["current"] == cache["proposed"]:
                    continue
                if str(cache["current"]) != item.current_value:
                    raise DataConflictError(f"报价#{item.entity_id} 已被其他操作修改，请重新核对")
                update_quote_repair_cache(
                    conn,
                    item.entity_id,
                    received_amount_cents=cache["proposed"]["received_amount_cents"],
                    paid=cache["proposed"]["paid"],
                    status=cache["proposed"]["status"],
                )
                audit(
                    conn,
                    "quotes",
                    item.entity_id,
                    "historical_repair",
                    before=cache["current"],
                    after=cache["proposed"],
                    reason=reason,
                )
                log_operation(
                    conn,
                    "历史异常修复",
                    "quotes",
                    item.entity_id,
                    f"重算报价缓存；{reason}",
                )
                applied.append(item)
        return HistoricalRepairResult(
            backup=backup,
            applied=tuple(applied),
            remaining_report=ReconciliationService(self.db_path).run(),
        )

    @staticmethod
    def export(plan: HistoricalRepairPlan, output_path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(plan.format_text(), encoding="utf-8")
        return output
