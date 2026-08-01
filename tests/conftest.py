"""
调货助手测试共享配置

提供内存数据库 fixture，所有测试在隔离的内存数据库中运行，
确保测试之间互不干扰，且不会污染生产数据。
"""

import pytest
import sys
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 将 src 目录加入 Python 路径，确保可以导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# 导入 database 模块
import models.database as db_module
import src.models.database as src_db_module


# 建表 SQL（与 database.py 中的 init_db 保持一致）
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series TEXT NOT NULL,
    cpu TEXT,
    ram TEXT,
    storage TEXT,
    gpu TEXT,
    screen TEXT,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    purchase_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    remaining INTEGER NOT NULL,
    date TEXT NOT NULL,
    remark TEXT,
    supplier_id INTEGER,
    sn_list TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wechat TEXT,
    qq TEXT,
    phone TEXT,
    note TEXT,
    default_tax_rate REAL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wechat TEXT,
    qq TEXT,
    phone TEXT,
    note TEXT,
    balance REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    customer_id INTEGER,
    quote_price REAL NOT NULL,
    quote_quantity INTEGER NOT NULL,
    quote_date TEXT NOT NULL,
    remark TEXT,
    paid TEXT,
    status TEXT DEFAULT '待确认',
    received_amount REAL DEFAULT 0,
    sn_list TEXT,
    tax_rate REAL DEFAULT NULL,
    purchase_tax_inclusive INTEGER DEFAULT 0,
    quote_tax_inclusive INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    customer_id INTEGER,
    supplier_id INTEGER,
    type TEXT,
    amount REAL,
    pay_date TEXT,
    method TEXT,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT,
    table_name TEXT,
    record_id INTEGER,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# v1.13: keep the fixture schema sourced from the production schema instead of
# maintaining a second hand-written copy.
from models.schema import CURRENT_SCHEMA_SQL
CREATE_TABLES_SQL = CURRENT_SCHEMA_SQL


# 模块级别的变量，用于持有内存数据库连接
_test_conn = None


def _init_memory_db():
    """创建并初始化内存数据库，返回连接对象"""
    global _test_conn
    _test_conn = sqlite3.connect(":memory:")
    _test_conn.row_factory = sqlite3.Row
    _test_conn.execute("PRAGMA journal_mode=WAL")
    _test_conn.execute("PRAGMA foreign_keys=ON")
    _test_conn.executescript(CREATE_TABLES_SQL)
    _test_conn.commit()
    return _test_conn


class _ConnectionWrapper:
    """
    连接包装器：拦截 close() 调用，防止内存数据库连接被意外关闭

    database.py 中的每个函数在操作完后都会调用 conn.close()，
    但在测试模式下我们需要保持连接打开以保留数据。
    """
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        # 拦截 close()，其他方法透传给真实连接
        if name == "close":
            return lambda: None  # 空操作
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _get_test_connection():
    """返回被包装的内存数据库连接（close() 被拦截）"""
    global _test_conn
    if _test_conn is None:
        _test_conn = _init_memory_db()
    return _ConnectionWrapper(_test_conn)


@pytest.fixture(autouse=True)
def memory_db(monkeypatch):
    """
    自动应用于所有测试的 fixture

    将 database 模块的 get_connection 替换为返回内存数据库连接的函数。
    每个测试函数获得全新的内存数据库，包含完整表结构。
    """
    global _test_conn
    # 确保每个测试开始前都有全新的内存数据库
    if _test_conn is not None:
        _test_conn.close()
        _test_conn = None

    _init_memory_db()

    # 替换模块级别的 get_connection 函数
    monkeypatch.setattr(db_module, "get_connection", _get_test_connection)
    monkeypatch.setattr(src_db_module, "get_connection", _get_test_connection)
    # 同时替换 DB_PATH 防止意外
    monkeypatch.setattr(db_module, "DB_PATH", ":memory:")
    monkeypatch.setattr(src_db_module, "DB_PATH", ":memory:")

    # MainWindow binds init_db at import time and the new FinanceTab uses
    # read-model functions. Keep UI tests fully isolated from production data.
    try:
        import src.main as main_module
        import src.ui.finance_tab as finance_module
        monkeypatch.setattr(main_module, "init_db", lambda: (True, "测试数据库"))
        monkeypatch.setattr(finance_module, "get_receivables", lambda *_: [])
        monkeypatch.setattr(finance_module, "get_payables", lambda *_: [])
        monkeypatch.setattr(finance_module, "get_payment_flow", lambda *_, **__: [])
    except ImportError:
        pass

    yield _test_conn

    # 测试结束后清理
    if _test_conn is not None:
        _test_conn.close()
        _test_conn = None


@pytest.fixture(scope="session")
def qapp():
    """Minimal pytest-qt compatible QApplication fixture."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_product():
    """
    提供一条标准机型数据

    场景：测试需要至少一条机型记录作为前置条件
    """
    product_id = db_module.add_product(
        series="Y7000P",
        cpu="i7-13700H",
        ram="16G",
        storage="512G",
        gpu="4060",
        screen="16",
        note="碳晶灰"
    )
    return product_id


@pytest.fixture
def sample_customer():
    """
    提供一条标准客户数据

    场景：测试需要至少一条客户记录作为前置条件
    """
    customer_id = db_module.add_customer(
        name="测试客户",
        wechat="test_wechat",
        qq="123456",
        phone="13800138000"
    )
    return customer_id


@pytest.fixture
def sample_supplier():
    """
    提供一条标准上游供应商数据

    场景：测试需要至少一条供应商记录作为前置条件
    """
    supplier_id = db_module.add_supplier(
        name="测试供应商",
        wechat="supplier_wx",
        qq="654321",
        phone="13900139000"
    )
    return supplier_id


@pytest.fixture
def sample_batch(sample_product, sample_supplier):
    """
    提供一条标准库存批次数据

    场景：测试需要至少一条有库存的批次作为前置条件
    """
    batch_id = db_module.add_batch(
        product_id=sample_product,
        purchase_price=5000.0,
        quantity=10,
        remaining=10,
        date_str="2026-06-01",
        supplier_id=sample_supplier,
        sn_list="SN001,SN002,SN003,SN004,SN005,SN006,SN007,SN008,SN009,SN010",
        remark="含税"
    )
    return batch_id


@pytest.fixture
def sample_quote(sample_batch, sample_customer):
    """
    提供一条标准报价记录

    场景：测试需要至少一条报价记录作为前置条件
    """
    quote_id = db_module.add_quote(
        batch_id=sample_batch,
        customer_id=sample_customer,
        quote_price=5500.0,
        quote_quantity=2,
        quote_date="2026-06-15",
        remark="测试报价"
    )
    return quote_id
