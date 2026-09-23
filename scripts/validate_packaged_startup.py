"""Windows EXE startup smoke check using only a disposable database."""

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models.schema import SCHEMA_VERSION


def transient_mei_dirs():
    root = Path(tempfile.gettempdir()).resolve()
    return {
        path.resolve()
        for path in root.glob("_MEI*")
        if path.is_dir() and path.parent == root
    }


def cleanup_created_mei(existing):
    root = Path(tempfile.gettempdir()).resolve()
    for path in transient_mei_dirs() - existing:
        if path.parent == root:
            shutil.rmtree(path, ignore_errors=True)


def process_ids_for_executable(exe):
    """Return visible PIDs for this exact candidate EXE, without matching other apps."""
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-Process | Where-Object { $_.Path -eq $env:STOCKMATE_VALIDATION_EXE } | "
        "ForEach-Object { $_.Id }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "STOCKMATE_VALIDATION_EXE": str(exe)},
    )
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def stop_created_processes(pids):
    if not pids:
        return
    script = (
        "$ids = $env:STOCKMATE_VALIDATION_PIDS -split ',' | "
        "ForEach-Object { [int]$_ }; "
        "$processes = Get-Process -Id $ids -ErrorAction SilentlyContinue; "
        "$processes | ForEach-Object { "
        "  if ($_.MainWindowHandle -ne 0) { [void]$_.CloseMainWindow() } "
        "}; "
        "$deadline = (Get-Date).AddSeconds(5); "
        "do { "
        "  Start-Sleep -Milliseconds 250; "
        "  $remaining = @(Get-Process -Id $ids -ErrorAction SilentlyContinue); "
        "} while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline); "
        "if ($remaining.Count -gt 0) { "
        "  Stop-Process -Id ($remaining | ForEach-Object Id) -Force -ErrorAction Stop "
        "}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        check=True,
        env={**os.environ, "STOCKMATE_VALIDATION_PIDS": ",".join(map(str, sorted(pids)))},
    )


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    existing_pids = process_ids_for_executable(exe)
    existing_mei = transient_mei_dirs()
    with tempfile.TemporaryDirectory(prefix="diaohuo-startup-") as folder:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            [str(exe)], startupinfo=startup,
            # Use the native Windows platform so CloseMainWindow can request a
            # graceful Qt shutdown and let the PyInstaller bootloader clean _MEI.
            env={**os.environ, "DIAOHUO_DATA_DIR": folder, "QT_QPA_PLATFORM": "windows"},
        )
        print(f"Isolated test PID: {process.pid}", flush=True)
        try:
            database = Path(folder) / "diaohuo.db"
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError(f"EXE exited: {process.returncode}")
                if database.exists():
                    try:
                        with closing(sqlite3.connect(database, timeout=1)) as conn:
                            version = conn.execute("PRAGMA user_version").fetchone()[0]
                            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                        if version == SCHEMA_VERSION and integrity == "ok":
                            break
                    except sqlite3.Error:
                        pass
                time.sleep(0.5)
            else:
                raise RuntimeError("EXE did not initialize database")
            time.sleep(3)
            if process.poll() is not None:
                raise RuntimeError(f"EXE exited after initialization: {process.returncode}")
            print(f"EXE startup passed; schema={version}; integrity={integrity}", flush=True)
        finally:
            stop_created_processes(process_ids_for_executable(exe) - existing_pids)
            process.wait(timeout=10)
            cleanup_created_mei(existing_mei)


if __name__ == "__main__":
    main()
