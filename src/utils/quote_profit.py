"""Profit presentation for quote records and their workbook export."""
from src.utils.tax import calc_tax_adjusted_profit_cents


def quote_profit(quote):
    status = quote.get("status", "待确认")
    if status == "已取消":
        return "不适用", None
    if status in ("待确认", "已报价"):
        return "预计", calc_tax_adjusted_profit_cents(
            quote.get("purchase_price_cents", 0), quote.get("quote_price_cents", 0),
            quote.get("quote_quantity", 1), quote.get("tax_rate"),
            quote.get("purchase_tax_inclusive"), quote.get("quote_tax_inclusive"),
        )
    sales = quote.get("actual_sales_cents")
    cost = quote.get("actual_cost_cents")
    if sales is None or cost is None:
        return "待核对", None
    return "实际", sales - cost


def summarize_quotes(quotes):
    totals = dict(count=0, sales=0, cost=0, actual=0, estimated=0, actual_count=0,
                  estimated_count=0, pending_count=0)
    for quote in quotes:
        totals["count"] += 1
        basis, profit = quote_profit(quote)
        if basis == "实际":
            totals["sales"] += quote["actual_sales_cents"]
            totals["cost"] += quote["actual_cost_cents"]
            totals["actual"] += profit
            totals["actual_count"] += 1
        elif basis == "预计":
            totals["estimated"] += profit
            totals["estimated_count"] += 1
        elif basis == "待核对":
            totals["pending_count"] += 1
    return totals
