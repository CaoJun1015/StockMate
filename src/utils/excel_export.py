"""
Excel 导出模块：将报价记录导出为 .xlsx 格式
"""

import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from src.utils.money import cents_to_yuan


def _save_workbook_atomically(workbook, output_path):
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.{uuid4().hex}{target.suffix}")
    try:
        workbook.save(temporary)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return str(target)


def export_quotes_to_excel(quotes, output_path=None):
    """
    将报价记录导出为 Excel 文件。
    
    quotes: list of dict, 包含字段:
        quote_date, customer_name, series, cpu, ram, storage, gpu,
        purchase_price, quote_price, remark, paid
    
    output_path: 输出路径，默认桌面
    """
    if not output_path:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(desktop, f"报价记录_{timestamp}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "报价记录"

    # 表头
    headers = ["日期", "客户", "机型系列", "CPU", "内存", "硬盘", "显卡",
               "上游", "购入价", "数量", "对外报价", "净金额", "毛利", "状态",
               "已收款", "待收款", "SN", "备注", "是否打款"]
    
    header_font = Font(bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # 数据
    data_font = Font(size=10)
    data_alignment = Alignment(vertical="center")
    profit_font_good = Font(size=10, color="008000", bold=True)
    profit_font_bad = Font(size=10, color="D32F2F", bold=True)
    alt_fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")

    total_purchase = 0
    total_quote = 0

    for row_idx, q in enumerate(quotes, 2):
        purchase_price = q.get("purchase_price_cents", 0) or 0
        quote_price = q.get("quote_price_cents", 0) or 0
        quantity = q.get("quote_quantity", 1) or 1
        net_total = q.get("net_total_cents", quote_price * quantity) or 0
        net_quantity = max(quantity - (q.get("returned_quantity", 0) or 0), 0)
        profit = (quote_price - purchase_price) * net_quantity
        received = q.get("received_amount_cents", 0) or 0

        total_purchase += purchase_price * net_quantity
        total_quote += net_total

        row_data = [
            q.get("quote_date", ""),
            q.get("customer_name", ""),
            q.get("series", ""),
            q.get("cpu", ""),
            q.get("ram", ""),
            q.get("storage", ""),
            q.get("gpu", ""),
            q.get("supplier_name", "") or "",
            cents_to_yuan(purchase_price),
            quantity,
            cents_to_yuan(quote_price),
            cents_to_yuan(net_total),
            cents_to_yuan(profit),
            q.get("status", "待确认"),
            cents_to_yuan(received),
            cents_to_yuan(max(net_total - received, 0)) if net_total > received else "已结清",
            q.get("sn_list", "") or "",
            q.get("remark", ""),
            q.get("paid", "否"),
        ]

        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = data_font
            cell.alignment = data_alignment
            cell.border = thin_border
            if row_idx % 2 == 0:
                cell.fill = alt_fill

        # 毛利列特殊着色
        profit_cell = ws.cell(row=row_idx, column=13)
        if profit > 0:
            profit_cell.font = profit_font_good
        elif profit < 0:
            profit_cell.font = profit_font_bad

        # 状态列着色
        status_cell = ws.cell(row=row_idx, column=14)
        status_colors = {
            "待确认": "9E9E9E", "已报价": "1976D2", "已出库": "F57C00",
            "已收款": "388E3C", "已全退": "7B1FA2", "已取消": "BDBDBD",
        }
        status_val = q.get("status", "待确认")
        if status_val in status_colors:
            status_cell.fill = PatternFill(start_color=status_colors[status_val], end_color=status_colors[status_val], fill_type="solid")
            status_cell.font = Font(size=10, color="FFFFFF", bold=True)

    # 汇总行
    summary_row = len(quotes) + 2
    ws.cell(row=summary_row, column=8, value="合计").font = Font(bold=True, size=10)
    ws.cell(row=summary_row, column=9, value=cents_to_yuan(total_purchase)).font = Font(bold=True, size=10)
    ws.cell(row=summary_row, column=11, value="").font = Font(bold=True, size=10)
    ws.cell(row=summary_row, column=12, value=cents_to_yuan(total_quote)).font = Font(bold=True, size=10)
    ws.cell(row=summary_row, column=13, value=cents_to_yuan(total_quote - total_purchase)).font = Font(bold=True, size=10, color="008000")

    for col_idx in range(1, len(headers) + 1):
        ws.cell(row=summary_row, column=col_idx).border = thin_border

    # 列宽
    col_widths = [12, 12, 16, 14, 8, 8, 10, 10, 10, 8, 10, 10, 10, 10, 10, 10, 15, 20, 10]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    # 冻结首行
    ws.freeze_panes = "A2"

    return _save_workbook_atomically(wb, output_path)


def export_finance_to_excel(
    output_path,
    *,
    date_from: str,
    date_to: str,
    db_path=None,
):
    """Export the operating ledger, counterparties, profit, and audit summary."""
    from src.models.finance_queries import (
        get_finance_dashboard,
        get_profit_report,
        list_account_transactions,
        list_counterparty_balances,
        list_financial_accounts,
        list_operating_entries,
    )

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill(
        start_color="4472C4", end_color="4472C4", fill_type="solid"
    )
    header_font = Font(color="FFFFFF", bold=True)

    def sheet(name, headers, rows):
        ws = wb.create_sheet(name)
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
        for column in ws.columns:
            width = min(
                max(len(str(cell.value or "")) for cell in column) + 2,
                36,
            )
            ws.column_dimensions[column[0].column_letter].width = width
        return ws

    dashboard = get_finance_dashboard(date_from, date_to, db_path)
    sheet(
        "经营总览",
        ["指标", "金额（元）"],
        [
            ["资金总额", float(cents_to_yuan(dashboard["funds_cents"]))],
            ["客户应收", float(cents_to_yuan(dashboard["receivable_cents"]))],
            ["客户预收", float(cents_to_yuan(dashboard["customer_advance_cents"]))],
            ["供应商应付", float(cents_to_yuan(dashboard["payable_cents"]))],
            ["供应商预付", float(cents_to_yuan(dashboard["supplier_advance_cents"]))],
            ["销售额", float(cents_to_yuan(dashboard["sales_cents"]))],
            ["商品成本", float(cents_to_yuan(dashboard["cogs_cents"]))],
            ["毛利润", float(cents_to_yuan(dashboard["gross_profit_cents"]))],
            ["费用", float(cents_to_yuan(dashboard["expense_cents"]))],
            ["净利润", float(cents_to_yuan(dashboard["net_profit_cents"]))],
            ["资金净变化", float(cents_to_yuan(dashboard["cash_change_cents"]))],
        ],
    )
    accounts = list_financial_accounts(include_inactive=True, db_path=db_path)
    sheet(
        "资金账户",
        ["账户", "余额（元）", "状态"],
        [
            [
                row["name"],
                float(cents_to_yuan(row["balance_cents"])),
                "正常" if row["is_active"] else "已停用",
            ]
            for row in accounts
        ],
    )
    account_transactions = list_account_transactions(
        date_from=date_from,
        date_to=date_to,
        db_path=db_path,
    )
    sheet(
        "账户流水",
        ["日期", "账户", "类型", "关联对象", "收入（元）", "支出（元）", "状态", "备注/原因"],
        [
            [
                row["entry_date"],
                row["account_name"],
                row["event_type"],
                row.get("customer_name") or row.get("supplier_name") or "",
                float(cents_to_yuan(row["debit_cents"])),
                float(cents_to_yuan(row["credit_cents"])),
                row["status"],
                row.get("reason") or row.get("remark") or "",
            ]
            for row in account_transactions
        ],
    )
    entries = list_operating_entries(
        date_from=date_from,
        date_to=date_to,
        limit=100_000,
        db_path=db_path,
    )
    sheet(
        "日常收支",
        ["日期", "类型", "来源", "客户", "供应商", "资金变化（元）", "状态", "备注", "原因"],
        [
            [
                row["entry_date"],
                row["event_type"],
                f"{row['source_type']}#{row.get('source_id') or ''}",
                row.get("customer_name") or "",
                row.get("supplier_name") or "",
                float(cents_to_yuan(row["cash_change_cents"])),
                row["status"],
                row.get("remark") or "",
                row.get("reason") or "",
            ]
            for row in entries
            if row["event_type"] in ("manual_income", "manual_expense")
        ],
    )
    sheet(
        "审计摘要",
        ["账本ID", "日期", "业务类型", "业务来源", "借贷总额（元）", "状态", "原因", "备注"],
        [
            [
                row["id"],
                row["entry_date"],
                row["event_type"],
                f"{row['source_type']}#{row.get('source_id') or ''}",
                float(cents_to_yuan(row["debit_total_cents"])),
                row["status"],
                row.get("reason") or "",
                row.get("remark") or "",
            ]
            for row in entries
        ],
    )
    for kind, name in (("customer", "客户往来"), ("supplier", "供应商往来")):
        rows = list_counterparty_balances(kind, db_path)
        sheet(
            name,
            ["对象", "余额（元）", "性质", "未结项目", "最早未结日", "微信", "电话"],
            [
                [
                    row["name"],
                    float(cents_to_yuan(abs(row["balance_cents"]))),
                    (
                        "应收"
                        if kind == "customer" and row["balance_cents"] > 0
                        else "预收"
                        if kind == "customer"
                        else "应付"
                        if row["balance_cents"] > 0
                        else "预付"
                    ),
                    row.get("open_item_count") or 0,
                    row.get("oldest_open_date") or "",
                    row.get("wechat") or "",
                    row.get("phone") or "",
                ]
                for row in rows
            ],
        )
    profit = get_profit_report(
        date_from=date_from,
        date_to=date_to,
        group_by="quote",
        db_path=db_path,
    )
    sheet(
        "利润明细",
        ["报价", "销售额", "成本", "毛利润", "费用", "其他收入", "净利润"],
        [
            [
                row["label"],
                float(cents_to_yuan(row["sales_cents"])),
                float(cents_to_yuan(row["cogs_cents"])),
                float(cents_to_yuan(row["sales_cents"] - row["cogs_cents"])),
                float(cents_to_yuan(row["expense_cents"])),
                float(cents_to_yuan(row["other_income_cents"])),
                float(
                    cents_to_yuan(
                        row["sales_cents"]
                        - row["cogs_cents"]
                        - row["expense_cents"]
                        + row["other_income_cents"]
                    )
                ),
            ]
            for row in profit
        ],
    )
    return _save_workbook_atomically(wb, output_path)
