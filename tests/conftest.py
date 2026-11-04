import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")   # CI containers run as root

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
try:
    import PySide6.QtWebEngineWidgets  # noqa: F401,E402
except ImportError:
    pass

# one QApplication for the whole session (widgets need QApplication, not QGuiApplication)
_app = QApplication.instance() or QApplication([])
