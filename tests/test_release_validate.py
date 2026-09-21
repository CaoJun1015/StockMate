"""Small checks for release validation's artifact discovery and DLL guard."""

from __future__ import annotations

import pytest

from scripts.release_validate import contains_bytes, find_executable


def test_release_validator_uses_the_built_exe_name_and_rejects_ambiguity(tmp_path):
    artifact = tmp_path / "StockMate v99.exe"
    artifact.write_bytes(b"release")
    assert find_executable(tmp_path) == artifact
    assert not contains_bytes(artifact, b"external marker")
    (tmp_path / "other.exe").write_bytes(b"other")
    with pytest.raises(RuntimeError, match="恰好有一个"):
        find_executable(tmp_path)
