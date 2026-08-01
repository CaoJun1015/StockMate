"""Current (schema v2) SQLite schema."""

SCHEMA_VERSION = 2

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
    balance REAL DEFAULT 0,
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
    balance REAL DEFAULT 0,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    purchase_price REAL NOT NULL,
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
    quote_price REAL NOT NULL,
    quote_price_cents INTEGER NOT NULL,
    quote_quantity INTEGER NOT NULL DEFAULT 1,
    quote_date TEXT NOT NULL,
    remark TEXT,
    paid TEXT,
    status TEXT DEFAULT '待确认',
    received_amount REAL DEFAULT 0,
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
    amount REAL NOT NULL,
    amount_cents INTEGER NOT NULL,
    entry_kind TEXT NOT NULL DEFAULT 'payment',
    reversal_of_id INTEGER,
    supersedes_id INTEGER,
    pay_date TEXT,
    method TEXT,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_id) REFERENCES quotes(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (reversal_of_id) REFERENCES payments(id),
    FOREIGN KEY (supersedes_id) REFERENCES payments(id)
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
CREATE INDEX IF NOT EXISTS idx_logs_time ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON price_snapshots(import_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_items ON price_snapshot_items(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_normkey ON price_snapshot_items(norm_key);
"""

