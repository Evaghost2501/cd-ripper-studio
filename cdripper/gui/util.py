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
            self.sig.failed.emit(friendly_error(e))
        else:
            self.sig.done.emit(res)


def friendly_error(e: Exception) -> str:
    """Short Korean message instead of a raw requests/urllib3 dump."""
    try:
        import requests
        if isinstance(e, requests.Timeout):
            return "응답 시간 초과 (인터넷 연결을 확인하세요)"
        if isinstance(e, requests.ConnectionError):
            return "인터넷 연결 실패 (네트워크·방화벽·프록시를 확인하세요)"
        if isinstance(e, requests.HTTPError) and e.response is not None:
            return f"서버 오류 HTTP {e.response.status_code}"
    except ImportError:
        pass
    msg = f"{type(e).__name__}: {e}"
    return msg if len(msg) < 300 else msg[:300] + "…"


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


BASE_PT = 9.0


def ui_scale() -> float:
    """Ratio of the current UI font to the original 9 pt design size."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    pt = app.font().pointSizeF() if app else BASE_PT
    return max(1.0, pt / BASE_PT) if pt > 0 else 1.0


def px(v: int) -> int:
    """Scale a pixel size designed for 9 pt text to the current font size."""
    return int(v * ui_scale())


def fit_to_screen(widget, w: int, h: int, scale: bool = True):
    """Resize to (w, h) scaled for the font, but never larger than 92% of the screen."""
    from PySide6.QtGui import QGuiApplication
    if scale:
        w, h = px(w), px(h)
    scr = widget.screen() or QGuiApplication.primaryScreen()
    if scr:
        g = scr.availableGeometry()
        w, h = min(w, int(g.width() * 0.92)), min(h, int(g.height() * 0.92))
    widget.resize(w, h)
