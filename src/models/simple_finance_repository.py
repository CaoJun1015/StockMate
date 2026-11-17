"""SQL access for automatic business-finance setup; caller owns transactions."""

def is_simple_ready(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='simple_finance_state'").fetchone()
    return bool(exists and conn.execute('SELECT 1 FROM simple_finance_state WHERE id=1').fetchone())

def create_simple_state(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS simple_finance_state (id INTEGER PRIMARY KEY CHECK(id=1), prepared_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')

def mark_simple_ready(conn):
    conn.execute('INSERT INTO simple_finance_state(id) VALUES (1)')

def has_ledger_entries(conn):
    return bool(conn.execute('SELECT 1 FROM ledger_entries LIMIT 1').fetchone())

def opening_customers(conn):
    return conn.execute("""SELECT c.id,c.balance_cents,
      COALESCE((SELECT SUM(q.quote_price_cents*q.quote_quantity) FROM quotes q
        WHERE q.customer_id=c.id AND q.deleted_at IS NULL AND q.status IN ('已出库','已收款')),0)
      -COALESCE((SELECT SUM(CASE WHEN p.entry_kind='reversal' THEN -p.amount_cents ELSE p.amount_cents END)
        FROM payments p WHERE p.customer_id=c.id AND p.type='receivable'),0) AS calculated_cents
      FROM customers c""").fetchall()

def opening_suppliers(conn):
    return conn.execute('SELECT id,balance_cents FROM suppliers').fetchall()

def shipments_without_snapshot(conn):
    return conn.execute("""SELECT q.*,b.purchase_price_cents FROM quotes q
        JOIN batches b ON b.id=q.batch_id
        WHERE q.deleted_at IS NULL AND q.status IN ('已出库','已收款')
        AND NOT EXISTS(SELECT 1 FROM shipment_snapshots ss WHERE ss.quote_id=q.id)
        ORDER BY q.id""").fetchall()

def historical_shipment_date(conn,quote_id):
    row=conn.execute("SELECT substr(created_at,1,10) FROM operation_logs WHERE table_name='quotes' AND record_id=? AND operation='出库' ORDER BY id LIMIT 1",(quote_id,)).fetchone()
    return row[0] if row else None

def historical_payments(conn):
    return conn.execute('SELECT * FROM payments ORDER BY id').fetchall()

def link_payment_flow(conn,payment_id,account_id):
    conn.execute('UPDATE payments SET account_id=COALESCE(account_id,?) WHERE id=?',(account_id,payment_id))
