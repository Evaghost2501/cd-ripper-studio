from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt
from PySide6.QtGui import QImage, QPixmap

_alive: set = set()


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.sig = _Signals()

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.sig.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.sig.done.emit(res)


def run_async(fn, *args, on_done=None, on_fail=None, **kwargs):
    """Run fn(*args) in the thread pool, deliver result on the GUI thread."""
    task = _Task(fn, args, kwargs)
    task.setAutoDelete(False)
    _alive.add(task)

    def cleanup(*_):
        _alive.discard(task)

    if on_done:
        task.sig.done.connect(on_done, Qt.QueuedConnection)
    if on_fail:
        task.sig.failed.connect(on_fail, Qt.QueuedConnection)
    task.sig.done.connect(cleanup)
    task.sig.failed.connect(cleanup)
    QThreadPool.globalInstance().start(task)
    return task


def pixmap_from_bytes(data: bytes | None, size: int | None = None) -> QPixmap:
    img = QImage.fromData(data or b"")
    if img.isNull():
        return QPixmap()
    pm = QPixmap.fromImage(img)
    if size:
        pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


def image_size(data: bytes | None) -> tuple[int, int]:
    img = QImage.fromData(data or b"")
    return (img.width(), img.height()) if not img.isNull() else (0, 0)
