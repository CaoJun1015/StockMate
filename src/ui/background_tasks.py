"""Small Qt worker wrapper for long, read-only work.  This is not a scheduler."""

from __future__ import annotations

import os
from uuid import uuid4
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot



class _Worker(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, work: Callable[[], object]):
        super().__init__()
        self.work = work

    @pyqtSlot()
    def run(self):
        try:
            self.completed.emit(self.work())
        except Exception as exc:
            self.failed.emit(str(exc))


class _CallbackProxy(QObject):
    def __init__(self, parent, on_success, on_failure):
        super().__init__(parent)
        self.on_success = on_success
        self.on_failure = on_failure

    @pyqtSlot(object)
    def succeed(self, value):
        self.on_success(value)

    @pyqtSlot(str)
    def fail(self, message):
        self.on_failure(message)


def start_background_task(owner, key: str, work, on_success, on_failure) -> bool:
    """Start one keyed task and deliver every GUI callback on the main thread."""
    tasks = getattr(owner, "_background_tasks", None)
    if tasks is None:
        tasks = owner._background_tasks = {}
    if key in tasks:
        return False
    thread = QThread(owner)
    worker = _Worker(work)
    callbacks = _CallbackProxy(owner, on_success, on_failure)
    worker.moveToThread(thread)
    tasks[key] = (thread, worker, callbacks)
    thread.started.connect(worker.run)
    worker.completed.connect(callbacks.succeed, Qt.ConnectionType.QueuedConnection)
    worker.failed.connect(callbacks.fail, Qt.ConnectionType.QueuedConnection)
    worker.completed.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(lambda: tasks.pop(key, None))
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return True


def has_background_tasks(owner) -> bool:
    return bool(getattr(owner, "_background_tasks", {}))


def write_text_atomically(path, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target
