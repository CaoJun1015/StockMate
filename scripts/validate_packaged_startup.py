"""Windows EXE startup smoke check using only a disposable database."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models.schema import SCHEMA_VERSION


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="diaohuo-startup-") as folder:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            [str(exe)], startupinfo=startup,
            env={**os.environ, "DIAOHUO_DATA_DIR": folder, "QT_QPA_PLATFORM": "offscreen"},
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
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, check=True,
                )
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
