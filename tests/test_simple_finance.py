import os
import sqlite3
from pathlib import Path

import pytest
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QLabel, QPushButton

from src.models.connection import connect, transaction
from src.models.migrations import migrate_database
from src.models.finance_queries import collect_finance_reconciliation_snapshot, list_operating_entries, list_counterparty_balances
from src.services.database_service import initialize_database
from src.services.simple_finance_service import ensure_simple_finance, flow_account_id
from src.services.finance_service import FinanceService
from src.services.payment_service import PaymentService
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.reconciliation_service import ReconciliationService
from src.ui.finance_tab import FinanceTab
from src.ui.finance_dialogs import PaymentDialog, PaymentEditDialog, BatchDialog, ReturnDialog

def copied(tmp_path):
    # The delivery snapshot is private and is deliberately not in Git.
    fixture = os.environ.get('DIAOHUO_TEST_DATABASE')
    if not fixture:
        pytest.skip('Set DIAOHUO_TEST_DATABASE to the isolated delivery regression snapshot')
    source = Path(fixture).resolve()
    if not source.is_file():
        pytest.fail(f'DIAOHUO_TEST_DATABASE does not exist: {source}')
    target = tmp_path / 'business.db'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    return target

def scalar(path, sql):
    c=sqlite3.connect(path)
    try:return c.execute(sql).fetchone()[0]
    finally:c.close()

def assert_balanced(path):
    r=collect_finance_reconciliation_snapshot(path)
    assert not r['unbalanced_entries']
    assert not r['customer_balances']
    assert not r['supplier_balances']
    assert r['inventory']['business_cents']==r['inventory']['ledger_cents']

def test_uploaded_records_and_prepayment_survive_restart(tmp_path):
    p=copied(tmp_path)
    before=[scalar(p,'SELECT COUNT(*) FROM '+t) for t in ('payments','quotes','ledger_entries','audit_events')]
    for _ in range(2):initialize_database(p)
    after=[scalar(p,'SELECT COUNT(*) FROM '+t) for t in ('payments','quotes','ledger_entries','audit_events')]
    assert before==after
    assert scalar(p,'SELECT balance_cents FROM suppliers WHERE id=78')==-60000
    assert scalar(p,'SELECT amount_cents FROM payments WHERE id=1273')==1260100
    assert scalar(p,'SELECT received_amount_cents FROM quotes WHERE id=598')==1440000
    assert_balanced(p)

def test_historical_supplier_payment_can_be_corrected_without_changing_net(tmp_path):
    p=copied(tmp_path)
    replacement=PaymentService(p).correct_payment(1273,amount_cents=1260100,pay_date='2026-11-16',remark='核对原实际付款',reason='回归验证')
    assert replacement!=1273
    assert scalar(p,'SELECT balance_cents FROM suppliers WHERE id=78')==-60000
    assert scalar(p,'SELECT COUNT(*) FROM payments WHERE reversal_of_id=1273')==1
    assert_balanced(p)
    with pytest.raises(Exception):PaymentService(p).void_payment(1273,'不能重复作废')

def test_cancelled_order_stays_cancelled_when_historical_receipt_is_corrected(tmp_path):
    p=copied(tmp_path)
    before=scalar(p,'SELECT balance_cents FROM customers WHERE id=29')
    PaymentService(p).correct_payment(876,amount_cents=1440000,pay_date='2026-09-14',remark='保留历史金额',reason='回归验证')
    assert scalar(p,'SELECT status FROM quotes WHERE id=598')=='已取消'
    assert scalar(p,'SELECT balance_cents FROM customers WHERE id=29')==before
    assert_balanced(p)

def test_backdated_business_without_account_selection(tmp_path):
    p=tmp_path/'fresh.db';initialize_database(p)
    with transaction(p) as c:
        product=c.execute("INSERT INTO products(series) VALUES('测试')").lastrowid
        supplier=c.execute("INSERT INTO suppliers(name) VALUES('上游')").lastrowid
        customer=c.execute("INSERT INTO customers(name) VALUES('下游')").lastrowid
    # Earlier than the initialization date, including payments at cent precision.
    batch=InventoryService(p).receive_batch(product_id=product,purchase_price_cents=1200100,quantity=1,date='2020-01-01',supplier_id=supplier)
    PaymentService(p).record_supplier_payment(supplier,1260100,'2020-01-01')
    assert scalar(p,f'SELECT balance_cents FROM suppliers WHERE id={supplier}')==-60000
    quote=OrderService(p).create_quote(batch_id=batch,customer_id=customer,quote_price_cents=1300001,quote_quantity=1,quote_date='2020-01-02')
    InventoryService(p).ship_quote(quote,shipped_date='2020-01-02')
    receipt=PaymentService(p).receive_customer_payment(customer,1300001,'2020-01-03')
    PaymentService(p).correct_payment(receipt.payment_id,amount_cents=1300000,pay_date='2020-01-03',remark='',reason='差一分')
    assert scalar(p,f'SELECT status FROM quotes WHERE id={quote}')=='已出库'
    assert scalar(p,f'SELECT balance_cents FROM customers WHERE id={customer}')==1
    assert ReconciliationService(p).run().is_clean

def test_existing_enabled_v116_is_not_reset(tmp_path):
    p=tmp_path/'enabled.db';migrate_database(p)
    account=FinanceService(p).initialize_finance('2026-08-01',[{'name':'原微信','opening_balance_cents':12345}])[0]
    with transaction(p) as c:
        supplier=c.execute("INSERT INTO suppliers(name) VALUES('上游')").lastrowid
    PaymentService(p).record_supplier_payment(supplier,500,'2026-08-02',account_id=account)
    before=scalar(p,f'SELECT balance_cents FROM suppliers WHERE id={supplier}')
    initialize_database(p)
    assert scalar(p,f'SELECT balance_cents FROM suppliers WHERE id={supplier}')==before
    assert scalar(p,f'SELECT SUM(debit_cents-credit_cents) FROM ledger_lines WHERE account_id={account}')==11845
    PaymentService(p).record_supplier_payment(supplier,100,'2025-01-01')
    assert_balanced(p)

def test_no_account_setup_or_manual_entry_ui_and_audit_selection(qapp,tmp_path):
    p=copied(tmp_path);w=FinanceTab(db_path=p);w.refresh()
    assert [w.section_tabs.tabText(i) for i in range(w.section_tabs.count())]==['应收应付','日常收支','利润分析','流水审计']
    assert w.section_tabs.currentIndex()==0
    forbidden=['启用经营记账','经营总览','记收入','记费用','新增账户','账户转账','账户核对','资金调整']
    texts=[x.text() for x in w.findChildren(QPushButton)]+[x.text() for x in w.findChildren(QLabel)]
    assert not any(t in texts for t in forbidden)
    w.daily_from.setDate(QDate(2000,1,1));w.daily_to.setDate(QDate(2099,1,1));w._refresh_daily()
    assert w.daily_table.rowCount()==1278
    w.audit_from.setDate(QDate(2000,1,1));w.audit_to.setDate(QDate(2099,1,1));w._refresh_audit()
    w.section_tabs.setCurrentIndex(3);w.audit_table.selectRow(0)
    assert w._selected_entry_id()==int(w.audit_table.item(0,0).text())
    for d in (PaymentDialog(db_path=p),BatchDialog(db_path=p),ReturnDialog('退货',db_path=p)):
        assert not hasattr(d,'account_combo')
        assert d.account_id==flow_account_id(p)
        d.close()
    dialog=PaymentDialog(db_path=p);dialog.amount_spin.setValue(12601)
    assert dialog.get_data()['amount']==12601
    w.close()

def test_export_contains_all_receipts_but_no_bank_balance(tmp_path):
    from src.utils.simple_finance_export import export_business_finance
    from openpyxl import load_workbook
    p=copied(tmp_path);output=tmp_path/'business.xlsx'
    export_business_finance(output,date_from='2000-01-01',date_to='2099-01-01',db_path=p)
    book=load_workbook(output)
    assert book.sheetnames==['业务收支','利润分析']
    assert book['业务收支'].max_row==1279

def test_new_parties_on_converted_database_do_not_inherit_null_balances(tmp_path):
    from src.services.party_service import CustomerService, SupplierService
    p=copied(tmp_path)
    customer=CustomerService(p).create(name='新增客户测试')
    supplier=SupplierService(p).create(name='新增上游测试')
    assert scalar(p,f'SELECT balance_cents FROM customers WHERE id={customer}')==0
    assert scalar(p,f'SELECT balance_cents FROM suppliers WHERE id={supplier}')==0
    PaymentService(p).receive_customer_payment(customer,101,'2026-01-01')
    PaymentService(p).record_supplier_payment(supplier,201,'2026-01-01')
    assert scalar(p,f'SELECT balance_cents FROM customers WHERE id={customer}')==-101
    assert scalar(p,f'SELECT balance_cents FROM suppliers WHERE id={supplier}')==-201
    assert_balanced(p)
