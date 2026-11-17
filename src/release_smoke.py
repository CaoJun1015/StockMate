"""Explicit offline release check; always requires an isolated database path."""
import json
import os
from pathlib import Path


def run(output):
    if not os.environ.get('DIAOHUO_DATA_DIR'):
        raise RuntimeError('Smoke test requires DIAOHUO_DATA_DIR pointing at a disposable copy')
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PyQt6.QtCore import QDate
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication
    from src.main import MainWindow
    from src.ui.style import APP_STYLE
    from src.ui.finance_dialogs import PaymentEditDialog, BatchDialog, ReturnDialog
    from src.models.queries import get_supplier, get_payment_detail
    from src.models.finance_queries import list_counterparty_balances
    from src.models.connection import get_database_path
    from src.version import APP_DISPLAY_NAME
    app=QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyle('Fusion');app.setStyleSheet(APP_STYLE)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    window=MainWindow();window.resize(1450,900);window.show();app.processEvents()
    window.tabs.setCurrentWidget(window.finance_tab)
    tab=window.finance_tab
    tab.daily_from.setDate(QDate(2000,1,1));tab.daily_to.setDate(QDate(2099,1,1))
    tab.audit_from.setDate(QDate(2000,1,1));tab.audit_to.setDate(QDate(2099,1,1))
    tab.profit_from.setDate(QDate(2000,1,1));tab.profit_to.setDate(QDate(2099,1,1))
    tab.refresh()
    pages=[]
    for index in range(tab.section_tabs.count()):
        tab.section_tabs.setCurrentIndex(index);app.processEvents()
        window.grab().save(str(output/f'page_{index}.png'))
        pages.append(tab.section_tabs.tabText(index))
    payment=get_payment_detail(1273)
    dialog=PaymentEditDialog(window,payment);dialog.show();app.processEvents()
    dialog.grab().save(str(output/'payment.png'))
    values=dialog.get_data();dialog.close()
    for name,d in [('batch',BatchDialog(window)),('return',ReturnDialog('销售退货',window,allow_restock=True))]:
        d.show();app.processEvents();d.grab().save(str(output/f'{name}.png'));d.close()
    supplier=next(row for row in list_counterparty_balances('supplier') if row['id']==78)
    assert pages==['应收应付','日常收支','利润分析','流水审计']
    assert tab.daily_table.rowCount()==1278
    assert supplier['balance_cents']==-60000
    assert values['amount']==12601
    result={'version':APP_DISPLAY_NAME,'database':str(get_database_path()),'pages':pages,
            'business_cash_rows':tab.daily_table.rowCount(),'supplier78_balance_cents':supplier['balance_cents'],
            'payment1273_amount':values['amount'],'window_size':[window.width(),window.height()]}
    (output/'smoke_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    window.close();app.processEvents()
