import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

# one QApplication for the whole session (widgets need QApplication, not QGuiApplication)
_app = QApplication.instance() or QApplication([])
