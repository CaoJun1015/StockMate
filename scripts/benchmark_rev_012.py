"""Measure long read/export paths against a disposable 10,000-row database."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.connection import connect
from src.models.migrations import migrate_database
from src.models.queries import search_quotes
from src.services.reconciliation_service import ReconciliationService
from src.utils.excel_export import export_finance_to_excel, export_quotes_to_excel
from src.utils.image_gen import generate_quote_image
from src.utils.json_export import export_all_to_json


def _seed(path: Path, count: int = 10_000) -> None:
    migrate_database(path)
    conn = connect(path)
    try:
        product = conn.execute("INSERT INTO products(series,cpu) VALUES ('性能机型','i7')").lastrowid
        customer = conn.execute("INSERT INTO customers(name) VALUES ('性能客户')").lastrowid
        batch = conn.execute(
            "INSERT INTO batches(product_id,purchase_price_cents,quantity,remaining,date) VALUES (?,?,?,?,?)",
            (product, 1, count, count, "2026-09-01"),
        ).lastrowid
        conn.executemany(
            "INSERT INTO quotes(batch_id,customer_id,quote_price_cents,quote_quantity,quote_date,remark,status) VALUES (?,?,?,?,?,?,?)",
            [(batch, customer, 2, 1, "2026-09-02", f"性能 {index}", "已报价") for index in range(count)],
        )
        conn.commit()
    finally:
        conn.close()


def _measure(label, action):
    started = time.perf_counter()
    action()
    print(f"{label}: {(time.perf_counter() - started) * 1000:.1f} ms")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="stockmate-rev012-") as directory:
        root = Path(directory)
        database = root / "benchmark.db"
        _seed(database)
        rows = search_quotes("性能", "2026-09-01", "2026-09-30", db_path=database)
        _measure("manual reconciliation", lambda: ReconciliationService(database).run())
        _measure("JSON export", lambda: export_all_to_json(root / "backup.json", database))
        _measure("quote Excel export", lambda: export_quotes_to_excel(rows, root / "quotes.xlsx"))
        _measure("finance Excel export", lambda: export_finance_to_excel(
            root / "finance.xlsx", date_from="2026-09-01", date_to="2026-09-30", db_path=database,
        ))
        products = [{"series": f"性能{index}", "cpu": "i7"} for index in range(500)]
        _measure("500-row image export", lambda: generate_quote_image(products, root / "quotes.png"))
