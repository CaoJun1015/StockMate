"""Shared test isolation for the v1.16 service/query architecture."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def isolated_default_database(tmp_path, monkeypatch):
    """Every default-path caller resolves to a fresh temporary SQLite database."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DIAOHUO_DATA_DIR", str(data_dir))
    from src.models.migrations import migrate_database

    migrate_database(data_dir / "diaohuo.db")
    yield data_dir / "diaohuo.db"


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
