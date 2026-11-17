"""Automatic, transactional setup for business receipts without account UI.

The existing v4 ledger remains balanced. A private clearing dimension is used
internally; its balance is never presented as cash or bank funds. Historical
entries are paired with explicit opening bridges so importing them cannot
double-count an existing opening balance.
"""
from datetime import date

from src.models.connection import connect, create_backup, transaction
from src.models.finance_repository import (
    find_ledger_entry_by_key, insert_ledger_account, insert_shipment_snapshot,
    set_finance_enabled,
    get_ledger_account_by_code, get_finance_settings, set_customer_balance,
    list_opening_inventory_batches, set_ledger_entry_status,
)
from src.models import simple_finance_repository as repo
from src.models.repositories import audit
from src.services.exceptions import DataConflictError
from src.services.ledger_posting_service import LedgerPostingService as Ledger

FLOW_CODE = 'FUNDS:BUSINESS_FLOW'


def default_flow_account(conn):
    row = get_ledger_account_by_code(conn, FLOW_CODE)
    return row['id'] if row else None


def flow_account_id(db_path=None):
    conn = connect(db_path, read_only=True)
    try:
        return default_flow_account(conn)
    finally:
        conn.close()


def _pair(debit, credit, amount, **dimensions):
    if not amount:
        return []
    if amount < 0:
        return _pair(credit, debit, -amount, **dimensions)
    def line(code, side):
        return {('account_id' if isinstance(code, int) else 'account_code'): code,
                side: amount, **dimensions}
    return [line(debit, 'debit_cents'), line(credit, 'credit_cents')]


def _post(conn, key, when, lines, *, event='historical_bridge', source='simple_opening', source_id=None):
    if not lines:
        return None
    return Ledger.post(conn, entry_date=when, event_type=event, source_type=source,
                       source_id=source_id, idempotency_key=key, lines=lines,
                       remark='历史数据衔接' if source == 'simple_opening' else '')


def _opening(conn, when):
    # Quotes and immutable receipts are the sources of truth. Cancelled orders
    # carry no sales, but their unrefunded receipts still belong to the customer.
    for c in repo.opening_customers(conn):
        balance = c['calculated_cents']
        if c['balance_cents'] != balance:
            audit(conn, 'customers', c['id'], 'rebuild_business_balance',
                  before={'balance_cents': c['balance_cents']}, after={'balance_cents': balance},
                  reason='由现存有效销售和全部净收款重建往来余额；原订单、流水及分配未修改')
            set_customer_balance(conn,c['id'],balance)
        _post(conn, f'simple:opening:customer:{c["id"]}', when,
              _pair('AR','OWNER_EQUITY',balance,customer_id=c['id']))
    for s in repo.opening_suppliers(conn):
        _post(conn, f'simple:opening:supplier:{s["id"]}', when,
              _pair('OWNER_EQUITY','AP',s['balance_cents'] or 0,supplier_id=s['id']))
    for b in list_opening_inventory_batches(conn):
        _post(conn, f'simple:opening:batch:{b["id"]}', when,
              _pair('INVENTORY','OWNER_EQUITY',b['remaining']*b['purchase_price_cents'],batch_id=b['id']))


def _historical_shipments(conn):
    rows = repo.shipments_without_snapshot(conn)
    for q in rows:
        dims = dict(customer_id=q['customer_id'],quote_id=q['id'],batch_id=q['batch_id'])
        revenue = q['quote_price_cents']*q['quote_quantity']
        cost = q['purchase_price_cents']*q['quote_quantity']
        lines = _pair('AR','SALES',revenue,**dims) + _pair('COGS','INVENTORY',cost,**dims)
        when = q['quote_date']
        # Prefer the recorded shipment date. Quotes without a log retain their
        # original order date; this limitation is documented in the delivery.
        log = repo.historical_shipment_date(conn,q['id'])
        if log:
            when = log
        existing = find_ledger_entry_by_key(conn, f'quote:{q["id"]}:ship')
        if existing:
            entry = existing['id']
        else:
            entry = _post(conn, f'quote:{q["id"]}:ship',when,lines,event='sales_shipment',source='quote',source_id=q['id'])
            _post(conn, f'simple:bridge:quote:{q["id"]}',when,
                  _pair('OWNER_EQUITY','AR',revenue,**dims)+_pair('INVENTORY','OWNER_EQUITY',cost,**dims))
        if entry:
            insert_shipment_snapshot(conn,quote_id=q['id'],shipped_date=when,
                quantity=q['quote_quantity'],unit_sale_cents=q['quote_price_cents'],
                unit_cost_cents=q['purchase_price_cents'],ledger_entry_id=entry)


def _historical_payments(conn, flow_id):
    for p in repo.historical_payments(conn):
        receive = p['type']=='receivable'
        key = f'payment:{p["id"]}:' + ('receive' if receive else 'pay')
        reversal = p['entry_kind']=='reversal'
        if reversal:
            key = f'payment:{p["id"]}:reversal'
        if find_ledger_entry_by_key(conn,key):
            continue
        dims = {'customer_id':p['customer_id']} if receive else {'supplier_id':p['supplier_id']}
        account = p['account_id'] or flow_id
        when = p['pay_date'] or p['created_at'][:10]
        amount = p['amount_cents']
        if not isinstance(amount,int) or amount<=0:
            raise DataConflictError(f'历史流水 #{p["id"]} 金额无效，未修改原数据')
        if reversal:
            original_key = f'payment:{p["reversal_of_id"]}:' + ('receive' if receive else 'pay')
            original = find_ledger_entry_by_key(conn,original_key)
            if not original:
                raise DataConflictError(f'历史冲销 #{p["id"]} 缺少原流水')
            Ledger.reverse(conn,entry_id=original['id'],entry_date=when,
                idempotency_key=key,source_type='payment_reversal',source_id=p['id'],
                reason=p['remark'] or '保留历史冲销')
        else:
            lines = _pair(account,'AR',amount,**dims) if receive else _pair('AP',account,amount,**dims)
            entry = _post(conn,key,when,lines,event='customer_receipt' if receive else 'supplier_payment',source='payment',source_id=p['id'])
            if p['supersedes_id']:
                set_ledger_entry_status(conn,entry,'corrected')
        signed = -amount if reversal else amount
        bridge = _pair('AR','OWNER_EQUITY',signed,**dims) if receive else _pair('OWNER_EQUITY','AP',signed,**dims)
        _post(conn,f'simple:bridge:payment:{p["id"]}',when,bridge)
        # Only technical linkage is added. Original amount/method/date/remark
        # and all payment allocations are preserved byte-for-byte in SQL values.
        repo.link_payment_flow(conn,p['id'],account)


def ensure_simple_finance(db_path=None):
    conn = connect(db_path)
    try:
        if repo.is_simple_ready(conn):
            return
    finally:
        conn.close()
    create_backup(db_path,prefix='before_simple_finance',retain=False)
    with transaction(db_path) as conn:
        repo.create_simple_state(conn)
        if repo.is_simple_ready(conn):
            return
        settings=get_finance_settings(conn)
        enabled = bool(settings.get('enabled_at'))
        if not enabled and repo.has_ledger_entries(conn):
            raise DataConflictError('尚未启用但已有账本记录，请先核对，自动处理已回滚')
        flow=default_flow_account(conn)
        if flow is None:
            flow=insert_ledger_account(conn,code=FLOW_CODE,name='业务收支')
        if not enabled:
            when=date.today().isoformat()
            _opening(conn,when)
            set_finance_enabled(conn,when)
        _historical_shipments(conn)
        _historical_payments(conn,flow)
        from src.services.finance_service import FinanceService
        FinanceService(db_path)._backfill_supplier_allocations(conn)
        repo.mark_simple_ready(conn)
        audit(conn,'finance_settings',1,'prepare_simple_business',
              after={'historical_payments_editable':True},reason='业务收支自动衔接，无资金账户设置')
