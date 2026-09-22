"""Build and verify a local StockMate release candidate from this checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.schema import SCHEMA_VERSION
from src.version import APP_VERSION


LOCKED_PACKAGES = (
    "PyQt6", "python-docx", "Pillow", "openpyxl", "PyInstaller", "pytest", "pytest-qt",
)


def run(command, *, env=None) -> None:
    print("+", subprocess.list2cmdline([str(part) for part in command]), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_executable(dist_path: Path) -> Path:
    executables = sorted(dist_path.rglob("*.exe"))
    if len(executables) != 1:
        raise RuntimeError(f"打包产物应恰好有一个 EXE，实际为 {len(executables)} 个")
    return executables[0]


def contains_bytes(path: Path, marker: bytes) -> bool:
    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if marker in tail + chunk:
                return True
            tail = (tail + chunk)[-max(len(marker) - 1, 0):]
    return False


def report_path(output: Path) -> Path:
    return output / "release-validation.json"


def write_portable_metadata(output: Path, executable: Path, report: dict) -> None:
    """Write only portable release files beside the candidate executable."""
    artifact = output / executable.name
    shutil.copy2(executable, artifact)
    report["artifact"] = {
        "path": str(artifact),
        "name": artifact.name,
        "size_bytes": artifact.stat().st_size,
        "sha256": sha256(artifact),
    }
    (output / "README-使用说明.txt").write_text(
        "货管家 · StockMate\n\n"
        "这是便携版程序。首次启动会在用户数据目录创建数据库。\n"
        "请不要把客户数据、数据库备份或 crash.log 放入程序发布目录。\n\n"
        f"应用版本：{APP_VERSION}\n"
        f"数据库 schema：{SCHEMA_VERSION}\n",
        encoding="utf-8",
    )
    (output / "version.json").write_text(
        json.dumps(
            {
                "app_version": APP_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source_commit": report["source_commit"],
                "python": report["python"],
                "build_time": report["build_time"],
                "candidate_status": report["candidate_status"],
                "artifact": artifact.name,
                "sha256": report["artifact"]["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output / "SHA256SUMS.txt").write_text(
        f"{report['artifact']['sha256']}  {artifact.name}\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="candidate directory (must not exist)")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("发布验证固定使用 Python 3.11；请按 requirements-release.lock 重建环境")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = (args.output or ROOT / "release-candidates" / timestamp).resolve()
    output.mkdir(parents=True, exist_ok=False)
    status = git_output("status", "--porcelain=v1")
    report = {
        "candidate_status": "local-validation-only" if status else "local-release-candidate",
        "release_eligible": not bool(status),
        "source_commit": git_output("rev-parse", "HEAD"),
        "workspace_status": status.splitlines(),
        "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "build_time": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version,
        "dependencies": {package: version(package) for package in LOCKED_PACKAGES},
        "requirements_lock_sha256": sha256(ROOT / "requirements-release.lock"),
        "validation": {},
    }
    try:
        run([sys.executable, "-m", "pytest", "-q"])
        report["validation"]["tests"] = "passed"

        with TemporaryDirectory(prefix="stockmate-release-build-") as temporary:
            work = Path(temporary)
            dist_path = output / "artifact"
            external = work / "external-dll-tool"
            external.mkdir()
            marker = f"StockMate external DLL guard {uuid4().hex}".encode()
            (external / "icuuc.dll").write_bytes(marker)
            build_env = {**os.environ, "PATH": str(external) + os.pathsep + os.environ.get("PATH", "")}
            run([
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "build.spec",
                "--distpath", str(dist_path), "--workpath", str(work / "work"),
            ], env=build_env)
            executable = find_executable(dist_path)
            if contains_bytes(executable, marker):
                raise RuntimeError("外部 DLL 工具目录的冲突文件混入了候选产物")
            report["validation"]["build"] = "passed"
            report["validation"]["dll_source"] = "passed (external icuuc.dll marker absent)"
            run([sys.executable, "scripts/validate_packaged_startup.py", str(executable)], env=build_env)
            report["validation"]["packaged_startup"] = "passed"
            write_portable_metadata(output, executable, report)
            shutil.rmtree(dist_path, ignore_errors=True)
            allowed = {
                executable.name,
                "README-使用说明.txt",
                "SHA256SUMS.txt",
                "version.json",
            }
            unexpected = [path.name for path in output.iterdir() if path.name not in allowed]
            if unexpected:
                raise RuntimeError(f"便携目录包含禁止文件：{unexpected}")
    except Exception as exc:
        report["validation"]["failure"] = str(exc)
        report_path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"发布验证失败，报告：{report_path(output)}", file=sys.stderr)
        return 1

    report_path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"发布验证通过，报告：{report_path(output)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
