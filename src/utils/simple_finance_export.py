"""Export business receipts and payments without cash-account balances."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from src.models.finance_queries import list_operating_entries, get_profit_report


def export_business_finance(path, *, date_from, date_to, db_path=None):
    book=Workbook()
    sheet=book.active;sheet.title='业务收支'
    sheet.append(['日期','业务','对象','收入（元）','支出（元）','状态','备注','原始流水编号'])
    labels={'customer_receipt':'客户收款','supplier_payment':'供应商付款',
            'customer_receipt_reversal':'收款冲销','supplier_payment_reversal':'付款冲销',
            'sales_return':'销售退货','purchase_return':'采购退货'}
    states={'normal':'正常','reversed':'已冲销','reversal':'冲销记录','corrected':'更正记录'}
    for r in list_operating_entries(date_from=date_from,date_to=date_to,business_cash_only=True,limit=None,db_path=db_path):
        value=r['cash_change_cents']
        is_receipt=r['event_type'] in {'customer_receipt','customer_receipt_reversal','sales_return'}
        sheet.append([r['entry_date'],labels.get(r['event_type'],r['event_type']),r.get('customer_name') or r.get('supplier_name') or '',value/100 if is_receipt else 0,0 if is_receipt else -value/100,states.get(r['status'],r['status']),r.get('remark') or r.get('reason') or '',r['source_id']])
    profit=book.create_sheet('利润分析')
    profit.append(['月份','销售额（元）','商品成本（元）','毛利润（元）','历史费用（元）','历史其他收入（元）','净利润（元）'])
    for r in get_profit_report(date_from=date_from,date_to=date_to,group_by='month',db_path=db_path):
        gross=r['sales_cents']-r['cogs_cents']
        profit.append([r['label'],r['sales_cents']/100,r['cogs_cents']/100,gross/100,r['expense_cents']/100,r['other_income_cents']/100,(gross-r['expense_cents']+r['other_income_cents'])/100])
    for tab in book:
        tab.freeze_panes='A2';tab.auto_filter.ref=tab.dimensions
        for cell in tab[1]:
            cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='335C81')
        for col in tab.columns:
            tab.column_dimensions[col[0].column_letter].width=24
        for row in tab.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,(float,int)):cell.number_format='#,##0.00'
                # Excel must not interpret user remarks as executable formulas.
                elif isinstance(cell.value,str):cell.data_type='s'
    book.save(path)
