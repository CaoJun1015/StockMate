"""Repeatable synthetic search/list benchmark; it never touches application data."""

from __future__ import annotations

import tempfile
import time
from math import ceil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from src.models.connection import connect
from src.models.migrations import migrate_database
from src.models.queries import search_quotes


def _p95(values: list[float]) -> float:
    return sorted(values)[ceil(len(values) * 0.95) - 1]


def _seed(path: Path, count: int) -> None:
    migrate_database(path)
    conn = connect(path)
    try:
        product = conn.execute("INSERT INTO products(series,cpu) VALUES ('性能机型','i7')").lastrowid
        customer = conn.execute("INSERT INTO customers(name) VALUES ('性能客户')").lastrowid
        batch = conn.execute(
            "INSERT INTO batches(product_id,purchase_price_cents,quantity,remaining,date,sn_list) VALUES (?,?,?,?,?,?)",
            (product, 1, count, count, "2026-09-01", ""),
        ).lastrowid
        conn.executemany(
            "INSERT INTO quotes(batch_id,customer_id,quote_price_cents,quote_quantity,quote_date,remark,status) VALUES (?,?,?,?,?,?,?)",
            [(batch, customer, 2, 1, "2026-09-02", f"性能 {index}", "已报价") for index in range(count)],
        )
        conn.commit()
    finally:
        conn.close()


def _fill(rows: list[dict]) -> None:
    table = QTableWidget(len(rows), 17)
    for index, row in enumerate(rows):
        values = (
            row["id"], row["quote_date"], row["customer_name"], row["series"], row["cpu"],
            row["ram"], row["storage"], row["gpu"], row["supplier_name"] or "",
            row["purchase_price_cents"], row["quote_quantity"], row["quote_price_cents"],
            row["status"], row["received_amount_cents"], row["sn_list"], row["remark"], row["paid"] or "",
        )
        for column, value in enumerate(values):
            table.setItem(index, column, QTableWidgetItem(str(value)))


def run(count: int, runs: int = 10) -> tuple[float, float, float, float]:
    with tempfile.TemporaryDirectory(prefix="stockmate-rev011-") as directory:
        path = Path(directory) / "benchmark.db"
        _seed(path, count)
        started = time.perf_counter()
        search_quotes("性能", "2026-09-01", "2026-09-30", db_path=path)
        cold_query = (time.perf_counter() - started) * 1000
        queries, full_fills, page_fills = [], [], []
        for _ in range(runs):
            started = time.perf_counter()
            rows = search_quotes("性能", "2026-09-01", "2026-09-30", db_path=path)
            queries.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            _fill(rows)
            full_fills.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            _fill(rows[:250])
            page_fills.append((time.perf_counter() - started) * 1000)
    return cold_query, _p95(queries), _p95(full_fills), _p95(page_fills)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    for size in (1_000, 10_000):
        cold_query, query_p95, full_fill_p95, page_fill_p95 = run(size)
        print(
            f"{size:,} rows | cold query {cold_query:.1f} ms | hot query p95 {query_p95:.1f} ms | "
            f"old full-fill p95 {full_fill_p95:.1f} ms | "
            f"new 250-row page p95 {page_fill_p95:.1f} ms | "
            f"new display p95 {query_p95 + page_fill_p95:.1f} ms"
        )
