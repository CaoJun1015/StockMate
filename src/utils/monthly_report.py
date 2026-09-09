"""Actual monthly activity using the same ledger as the finance workspace."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from src.models.finance_queries import get_monthly_activity, get_profit_report
from src.models.queries import get_slow_movers
from src.utils.money import format_yuan


def get_monthly_report(year=None, month=None, db_path=None):
    today = date.today()
    start = date(year or today.year, month or today.month, 1)
    end = start.replace(day=monthrange(start.year, start.month)[1])
    previous_end = start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    activity = get_monthly_activity(start.isoformat(), end.isoformat(), db_path)
    enabled = activity["enabled_at"]
    complete = bool(enabled and enabled <= start.isoformat())
    available = bool(enabled and enabled <= end.isoformat())
    rows = get_profit_report(date_from=start.isoformat(), date_to=end.isoformat(),
                             group_by="product", db_path=db_path) if available else []
    previous = get_profit_report(date_from=previous_start.isoformat(),
                                 date_to=previous_end.isoformat(), group_by="product",
                                 db_path=db_path) if enabled and enabled <= previous_start.isoformat() else []
    revenue = sum(row["sales_cents"] for row in rows)
    profit = sum(row["sales_cents"] - row["cogs_cents"] for row in rows)
    previous_revenue = sum(row["sales_cents"] for row in previous)
    previous_profit = sum(row["sales_cents"] - row["cogs_cents"] for row in previous)
    def change(current, old):
        return (current - old) / old * 100 if complete and old > 0 else None
    top = [{"series": row["label"], "cpu": "",
            "sale_count": activity["quantities"].get(row["group_key"], 0),
            "revenue_cents": row["sales_cents"],
            "profit_cents": row["sales_cents"] - row["cogs_cents"]}
           for row in rows if row["sales_cents"] or row["cogs_cents"]]
    return {
        "year": start.year, "month": start.month, "period": f"{start.year}年{start.month}月",
        "available": available, "complete_month": complete,
        "statistics_from": max(start.isoformat(), enabled) if available else None,
        "order_count": activity["order_count"] if available else 0,
        "total_revenue_cents": revenue, "total_profit_cents": profit,
        "total_received_cents": activity["cash_cents"] if available else 0,
        "profit_margin": profit / revenue * 100 if revenue > 0 else None,
        "revenue_change": change(revenue, previous_revenue),
        "profit_change": change(profit, previous_profit),
        "top_products": sorted(top, key=lambda item: item["profit_cents"], reverse=True)[:5],
        "slow_movers": get_slow_movers(start.isoformat(), (end + timedelta(days=1)).isoformat(), db_path),
    }


def format_report_text(report):
    parts = [f"📊 {report['period']} 实际经营摘要", "=" * 30]
    if not report["available"]:
        parts.append("无完整账本数据：该月份尚未启用经营记账。")
    else:
        parts.append(f"统计起始日：{report['statistics_from']}（按账务发生日期）")
        if not report["complete_month"]:
            parts.append("本月中途启用记账，仅包含启用后的业绩，环比不可比。")
        margin = f"{report['profit_margin']:.1f}%" if report["profit_margin"] is not None else "不可比"
        parts.extend([
            f"本月出库订单数：{report['order_count']} 单",
            f"净销售额：{format_yuan(report['total_revenue_cents'])}",
            f"毛利：{format_yuan(report['total_profit_cents'])}",
            f"毛利率：{margin}",
            f"当月客户净收款：{format_yuan(report['total_received_cents'])}",
            "（实际收款减现金退款，含历史订单回款及预收款）",
        ])
        for label, key in (("销售额", "revenue_change"), ("毛利", "profit_change")):
            value = report[key]
            parts.append(f"{label}环比：" + (f"{value:+.1f}%" if value is not None else "不可比"))
        if report["top_products"]:
            parts.append("机型毛利 TOP5：")
            for item in report["top_products"]:
                parts.append(f"  {item['series']} | 净销售 {item['sale_count']} 台 | 毛利 {format_yuan(item['profit_cents'])}")
    if report["slow_movers"]:
        parts.append("当前库存预警（所选月份无实际出库）：")
        for item in report["slow_movers"]:
            parts.append(f"  {item['series']} {item.get('cpu') or ''} | 库存 {item['stock']} 台 | 占用资金 {format_yuan(item['tied_capital_cents'])}")
    return "\n".join(parts)
