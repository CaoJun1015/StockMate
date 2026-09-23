"""Small checks for release validation's artifact discovery and DLL guard."""

from __future__ import annotations

import pytest

from scripts.release_validate import (
    contains_bytes,
    find_executable,
    write_portable_metadata,
)
from scripts import validate_packaged_startup


def test_release_validator_uses_the_built_exe_name_and_rejects_ambiguity(tmp_path):
    artifact = tmp_path / "StockMate v99.exe"
    artifact.write_bytes(b"release")
    assert find_executable(tmp_path) == artifact
    assert not contains_bytes(artifact, b"external marker")
    (tmp_path / "other.exe").write_bytes(b"other")
    with pytest.raises(RuntimeError, match="恰好有一个"):
        find_executable(tmp_path)


def test_portable_metadata_matches_the_copied_executable(tmp_path):
    executable = tmp_path / "build" / "StockMate v1.17.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"portable-exe")
    report = {
        "source_commit": "abc123",
        "python": "Python 3.11",
        "build_time": "2026-09-22T10:00:00",
        "candidate_status": "local-validation-only",
    }

    write_portable_metadata(tmp_path, executable, report)

    artifact = tmp_path / executable.name
    assert artifact.read_bytes() == executable.read_bytes()
    assert report["artifact"]["sha256"]
    assert f"{report['artifact']['sha256']}  {artifact.name}" in (
        tmp_path / "SHA256SUMS.txt"
    ).read_text(encoding="utf-8")
    metadata = __import__("json").loads((tmp_path / "version.json").read_text(encoding="utf-8"))
    assert metadata["candidate_status"] == "local-validation-only"
    assert metadata["sha256"] == report["artifact"]["sha256"]


def test_packaged_startup_cleanup_removes_only_new_mei_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(validate_packaged_startup.tempfile, "gettempdir", lambda: str(tmp_path))
    existing = tmp_path / "_MEI_existing"
    created = tmp_path / "_MEI_created"
    existing.mkdir()
    created.mkdir()

    validate_packaged_startup.cleanup_created_mei({existing.resolve()})

    assert existing.exists()
    assert not created.exists()
