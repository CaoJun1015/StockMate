"""
智能跟单提醒 (Follow-up Reminder)

扫描报价记录，找出需要跟进的订单：
- 已报价超过 N 天未出库/收款
- 待确认超过 N 天未报价
- 已出库但未收款超过 N 天
"""

from datetime import datetime, timedelta
from src.models.queries import list_stale_quotes
from src.utils.money import format_yuan


def get_stale_quotes(stale_days=3, db_path=None):
    """
    获取需要跟进的报价记录。

    参数:
        stale_days: int — 超过多少天算"需要跟进"，默认 3 天

    返回:
        dict {
            pending_confirm: list,   # 待确认超过 N 天
            quoted_no_ship: list,    # 已报价超过 N 天未出库
            shipped_no_pay: list,    # 已出库超过 N 天未收款
            total: int,              # 总计需跟进数
        }
    """
    cutoff = (datetime.now() - timedelta(days=stale_days)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    pending_list = list_stale_quotes("待确认", cutoff, today, db_path)
    quoted_list = list_stale_quotes("已报价", cutoff, today, db_path)
    shipped_list = list_stale_quotes("已出库", cutoff, today, db_path)

    return {
        "pending_confirm": pending_list,
        "quoted_no_ship": quoted_list,
        "shipped_no_pay": shipped_list,
        "total": len(pending_list) + len(quoted_list) + len(shipped_list),
    }


def format_reminder_text(stale_quotes):
    """格式化提醒文本，用于弹窗显示。"""
    parts = []
    total = stale_quotes["total"]
    if total == 0:
        return "当前没有需要跟进的订单，一切正常 ✅"

    parts.append(f"共有 {total} 条订单需要跟进：\n")

    if stale_quotes["pending_confirm"]:
        items = stale_quotes["pending_confirm"]
        parts.append(f"📋 待确认（超过 3 天）：{len(items)} 条")
        for item in items[:5]:
            name = item.get("customer_name", "未知客户")
            series = item.get("series", "")
            days = item.get("days_ago", 0)
            price = item.get("quote_price_cents", 0)
            parts.append(f"  • {name} | {series} | {format_yuan(price)} | {days} 天前")
        if len(items) > 5:
            parts.append(f"  ... 还有 {len(items) - 5} 条")
        parts.append("")

    if stale_quotes["quoted_no_ship"]:
        items = stale_quotes["quoted_no_ship"]
        parts.append(f"📦 已报价未出库（超过 3 天）：{len(items)} 条")
        for item in items[:5]:
            name = item.get("customer_name", "未知客户")
            series = item.get("series", "")
            days = item.get("days_ago", 0)
            price = item.get("quote_price_cents", 0)
            parts.append(f"  • {name} | {series} | {format_yuan(price)} | {days} 天前")
        if len(items) > 5:
            parts.append(f"  ... 还有 {len(items) - 5} 条")
        parts.append("")

    if stale_quotes["shipped_no_pay"]:
        items = stale_quotes["shipped_no_pay"]
        parts.append(f"💰 已出库未收款（超过 3 天）：{len(items)} 条")
        for item in items[:5]:
            name = item.get("customer_name", "未知客户")
            series = item.get("series", "")
            days = item.get("days_ago", 0)
            total_amount = (item.get("quote_price_cents", 0) or 0) * (
                item.get("quote_quantity", 1) or 1
            )
            parts.append(f"  • {name} | {series} | {format_yuan(total_amount)} | {days} 天前")
        if len(items) > 5:
            parts.append(f"  ... 还有 {len(items) - 5} 条")

    return "\n".join(parts)
