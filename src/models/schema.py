"""Current (schema v4) SQLite schema."""

SCHEMA_VERSION = 5

CURRENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series TEXT NOT NULL,
    cpu TEXT,
    ram TEXT,
    storage TEXT,
    gpu TEXT,
    screen TEXT,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wechat TEXT,
    qq TEXT,
    phone TEXT,
    note TEXT,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    default_tax_rate REAL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wechat TEXT,
    qq TEXT,
    phone TEXT,
    note TEXT,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    purchase_price_cents INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    remaining INTEGER NOT NULL,
    date TEXT NOT NULL,
    remark TEXT,
    supplier_id INTEGER,
    sn_list TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    customer_id INTEGER,
    quote_price_cents INTEGER NOT NULL,
    quote_quantity INTEGER NOT NULL DEFAULT 1,
    quote_date TEXT NOT NULL,
    remark TEXT,
    paid TEXT,
    status TEXT DEFAULT '待确认',
    received_amount_cents INTEGER NOT NULL DEFAULT 0,
    sn_list TEXT,
    tax_rate REAL DEFAULT NULL,
    purchase_tax_inclusive INTEGER DEFAULT 0,
    quote_tax_inclusive INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    customer_id INTEGER,
    supplier_id INTEGER,
    type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    entry_kind TEXT NOT NULL DEFAULT 'payment',
    reversal_of_id INTEGER,
    supersedes_id INTEGER,
    pay_date TEXT,
    method TEXT,
    account_id INTEGER,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_id) REFERENCES quotes(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (reversal_of_id) REFERENCES payments(id),
    FOREIGN KEY (supersedes_id) REFERENCES payments(id),
    FOREIGN KEY (account_id) REFERENCES ledger_accounts(id)
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL,
    quote_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payment_id) REFERENCES payments(id),
    FOREIGN KEY (quote_id) REFERENCES quotes(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled_at TEXT,
    initialized_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ledger_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL
        CHECK (account_type IN ('asset','liability','equity','revenue','expense')),
    is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0,1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
    affects_profit INTEGER NOT NULL DEFAULT 1 CHECK (affects_profit IN (0,1)),
    is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0,1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    UNIQUE(name, kind)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'normal'
        CHECK (status IN ('normal','reversed','corrected','reversal')),
    reversal_of_id INTEGER,
    supersedes_id INTEGER,
    reason TEXT,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (reversal_of_id) REFERENCES ledger_entries(id),
    FOREIGN KEY (supersedes_id) REFERENCES ledger_entries(id)
);

CREATE TABLE IF NOT EXISTS ledger_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    debit_cents INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
    credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
    customer_id INTEGER,
    supplier_id INTEGER,
    quote_id INTEGER,
    batch_id INTEGER,
    category_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (debit_cents > 0 AND credit_cents = 0)
        OR (credit_cents > 0 AND debit_cents = 0)
    ),
    FOREIGN KEY (entry_id) REFERENCES ledger_entries(id),
    FOREIGN KEY (account_id) REFERENCES ledger_accounts(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (quote_id) REFERENCES quotes(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    FOREIGN KEY (category_id) REFERENCES finance_categories(id)
);

CREATE TABLE IF NOT EXISTS shipment_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL UNIQUE,
    shipped_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_sale_cents INTEGER NOT NULL,
    unit_cost_cents INTEGER,
    revenue_cents INTEGER NOT NULL,
    cost_cents INTEGER NOT NULL,
    ledger_entry_id INTEGER NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_id) REFERENCES quotes(id),
    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
);

CREATE TABLE IF NOT EXISTS shipment_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_snapshot_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
    sn_list TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (shipment_snapshot_id) REFERENCES shipment_snapshots(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    UNIQUE(shipment_snapshot_id, batch_id)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_date TEXT NOT NULL,
    movement_type TEXT NOT NULL CHECK (movement_type IN (
        'purchase_receipt','sales_shipment','sales_return',
        'purchase_return','migration_adjustment'
    )),
    product_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    quantity_delta INTEGER NOT NULL CHECK (quantity_delta != 0),
    unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
    total_cost_cents INTEGER NOT NULL CHECK (total_cost_cents >= 0),
    source_type TEXT NOT NULL,
    source_id TEXT,
    shipment_allocation_id INTEGER,
    ledger_entry_id INTEGER,
    sn_list TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    FOREIGN KEY (shipment_allocation_id) REFERENCES shipment_allocations(id),
    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
);

CREATE TABLE IF NOT EXISTS supplier_payment_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payment_id) REFERENCES payments(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id)
);

CREATE TABLE IF NOT EXISTS sales_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    return_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    revenue_cents INTEGER NOT NULL,
    cost_cents INTEGER NOT NULL,
    restock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (restock_quantity >= 0),
    cash_refund_cents INTEGER NOT NULL DEFAULT 0 CHECK (cash_refund_cents >= 0),
    account_id INTEGER,
    ledger_entry_id INTEGER NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_id) REFERENCES quotes(id),
    FOREIGN KEY (account_id) REFERENCES ledger_accounts(id),
    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
);

CREATE TABLE IF NOT EXISTS purchase_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    return_date TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    amount_cents INTEGER NOT NULL,
    cash_refund_cents INTEGER NOT NULL DEFAULT 0 CHECK (cash_refund_cents >= 0),
    account_id INTEGER,
    ledger_entry_id INTEGER NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (account_id) REFERENCES ledger_accounts(id),
    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id)
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    table_name TEXT NOT NULL,
    record_id INTEGER,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_date TEXT NOT NULL,
    item_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_snapshot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    series TEXT,
    cpu TEXT,
    ram TEXT,
    storage TEXT,
    gpu TEXT,
    note TEXT,
    norm_key TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES price_snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_products_series ON products(series);
CREATE INDEX IF NOT EXISTS idx_quotes_date ON quotes(quote_date);
CREATE INDEX IF NOT EXISTS idx_quotes_customer ON quotes(customer_id);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_payments_supplier ON payments(supplier_id);
CREATE INDEX IF NOT EXISTS idx_allocations_payment ON payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_allocations_quote ON payment_allocations(quote_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_reversal_per_payment
    ON payments(reversal_of_id) WHERE reversal_of_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_date ON ledger_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_source
    ON ledger_entries(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_entry ON ledger_lines(entry_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_account ON ledger_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_customer ON ledger_lines(customer_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_supplier ON ledger_lines(supplier_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lines_quote ON ledger_lines(quote_id);
CREATE INDEX IF NOT EXISTS idx_supplier_alloc_payment
    ON supplier_payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_supplier_alloc_batch
    ON supplier_payment_allocations(batch_id);
CREATE INDEX IF NOT EXISTS idx_sales_returns_quote ON sales_returns(quote_id);
CREATE INDEX IF NOT EXISTS idx_purchase_returns_batch ON purchase_returns(batch_id);
CREATE INDEX IF NOT EXISTS idx_logs_time ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON price_snapshots(import_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_items ON price_snapshot_items(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_normkey ON price_snapshot_items(norm_key);
CREATE INDEX IF NOT EXISTS idx_shipment_alloc_snapshot
    ON shipment_allocations(shipment_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_shipment_alloc_batch
    ON shipment_allocations(batch_id);
CREATE INDEX IF NOT EXISTS idx_inventory_movement_batch_date
    ON inventory_movements(batch_id, movement_date, id);
CREATE INDEX IF NOT EXISTS idx_inventory_movement_source
    ON inventory_movements(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_inventory_movement_allocation
    ON inventory_movements(shipment_allocation_id);

INSERT OR IGNORE INTO finance_settings(id) VALUES (1);
INSERT OR IGNORE INTO ledger_accounts(code,name,account_type,is_system) VALUES
    ('AR','客户应收','asset',1),
    ('AP','供应商应付','liability',1),
    ('INVENTORY','库存商品','asset',1),
    ('SALES','销售收入','revenue',1),
    ('COGS','商品成本','expense',1),
    ('EXPENSE','经营费用','expense',1),
    ('OTHER_INCOME','其他经营收入','revenue',1),
    ('OWNER_EQUITY','期初及资金调整','equity',1);
INSERT OR IGNORE INTO finance_categories(name,kind,affects_profit,is_system) VALUES
    ('运费','expense',1,1),
    ('维修及售后','expense',1,1),
    ('平台/支付手续费','expense',1,1),
    ('包装耗材','expense',1,1),
    ('办公费用','expense',1,1),
    ('差旅招待','expense',1,1),
    ('税费','expense',1,1),
    ('其他费用','expense',1,1),
    ('供应商返利','income',1,1),
    ('补贴','income',1,1),
    ('其他收入','income',1,1);
"""

