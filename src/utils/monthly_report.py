"""Monthly business report calculated exclusively with integer cents."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from src.models.queries import get_monthly_business_data
from src.utils.money import format_yuan
from src.utils.tax import calc_tax_adjusted_profit_cents


def _calc_sales_profit(rows):
    revenue_cents = 0
    profit_cents = 0
    for row in rows:
        quantity = row["quote_quantity"] or 1
        revenue_cents += (row["quote_price_cents"] or 0) * quantity
        profit_cents += calc_tax_adjusted_profit_cents(
            row["purchase_price_cents"] or 0,
            row["quote_price_cents"] or 0,
            quantity,
            row["tax_rate"],
            bool(row["purchase_tax_inclusive"]),
            bool(row["quote_tax_inclusive"]),
        )
    return revenue_cents, profit_cents


def get_monthly_report(year=None, month=None, db_path=None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    date_from = f"{year:04d}-{month:02d}-01"
    date_to = (
        f"{year + 1:04d}-01-01"
        if month == 12
        else f"{year:04d}-{month + 1:02d}-01"
    )
    prev_from = (
        f"{year - 1:04d}-12-01"
        if month == 1
        else f"{year:04d}-{month - 1:02d}-01"
    )
    data = get_monthly_business_data(
        date_from, date_to, prev_from, date_from, db_path
    )
    sales_rows = data["current"]
    total_revenue, total_profit = _calc_sales_profit(sales_rows)
    prev_revenue, prev_profit = _calc_sales_profit(data["previous"])
    total_received = sum(row["received_amount_cents"] or 0 for row in sales_rows)

    top_agg = defaultdict(
        lambda: {"sale_count": 0, "revenue_cents": 0, "profit_cents": 0}
    )
    for row in sales_rows:
        key = (row["series"], row["cpu"] or "")
        item = top_agg[key]
        item["series"], item["cpu"] = key
        quantity = row["quote_quantity"] or 1
        item["sale_count"] += quantity
        item["revenue_cents"] += (row["quote_price_cents"] or 0) * quantity
        item["profit_cents"] += calc_tax_adjusted_profit_cents(
            row["purchase_price_cents"] or 0,
            row["quote_price_cents"] or 0,
            quantity,
            row["tax_rate"],
            bool(row["purchase_tax_inclusive"]),
            bool(row["quote_tax_inclusive"]),
        )

    def change(current, previous):
        return ((current - previous) / previous * 100) if previous > 0 else 0

    return {
        "year": year,
        "month": month,
        "period": f"{year}年{month}月",
        "order_count": len(sales_rows),
        "total_revenue_cents": total_revenue,
        "total_profit_cents": total_profit,
        "total_received_cents": total_received,
        "collection_rate": (total_received / total_revenue * 100)
        if total_revenue > 0
        else 0,
        "profit_margin": (total_profit / total_revenue * 100)
        if total_revenue > 0
        else 0,
        "revenue_change": change(total_revenue, prev_revenue),
        "profit_change": change(total_profit, prev_profit),
        "top_products": sorted(
            top_agg.values(), key=lambda item: item["profit_cents"], reverse=True
        )[:5],
        "slow_movers": data["slow_movers"],
    }


def format_report_text(report):
    parts = [
        f"📊 {report['period']} 经营摘要",
        "=" * 30,
        "",
        f"📦 成交订单: {report['order_count']} 单",
        f"💰 总销售额: {format_yuan(report['total_revenue_cents'])}",
        f"📈 总毛利: {format_yuan(report['total_profit_cents'])}",
        f"📊 毛利率: {report['profit_margin']:.1f}%",
        f"💵 已收款: {format_yuan(report['total_received_cents'])}",
        f"📋 回款率: {report['collection_rate']:.1f}%",
        "",
    ]
    rev_arrow = "↑" if report["revenue_change"] >= 0 else "↓"
    prof_arrow = "↑" if report["profit_change"] >= 0 else "↓"
    parts.extend(
        [
            f"📉 环比: 销售额 {rev_arrow}{abs(report['revenue_change']):.1f}%  |  "
            f"毛利 {prof_arrow}{abs(report['profit_change']):.1f}%",
            "",
        ]
    )
    if report["top_products"]:
        parts.append("🏆 最赚钱机型 TOP5:")
        for index, item in enumerate(report["top_products"], 1):
            name = item["series"] + (f" {item['cpu']}" if item.get("cpu") else "")
            parts.append(
                f"  {index}. {name} | 卖{item['sale_count']}台 | "
                f"毛利 {format_yuan(item['profit_cents'])}"
            )
        parts.append("")
    if report["slow_movers"]:
        parts.append("⚠️ 滞销库存预警:")
        for item in report["slow_movers"]:
            name = item["series"] + (f" {item['cpu']}" if item.get("cpu") else "")
            parts.append(
                f"  • {name} | 库存{item['stock']}台 | "
                f"占资金 {format_yuan(item['tied_capital_cents'])} | "
                f"最早入库 {item['oldest_batch_date']}"
            )
        parts.extend(["", "建议: 考虑降价促销或联系上游调货"])
    return "\n".join(parts)
