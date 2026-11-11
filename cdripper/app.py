"""Application entry point."""
import os
import sys


def _setup_logging():
    """Windowed exe has no console: send stdout/stderr to a log file."""
    from .core.settings import config_dir
    if sys.stderr is None or sys.stdout is None:
        f = open(os.path.join(config_dir(), "cdripper.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _excepthook(exc_type, exc, tb):
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    print(text, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "오류", f"예기치 않은 오류가 발생했습니다:\n\n{exc}\n\n자세한 내용은 로그 파일을 확인하세요.")
    except Exception:  # noqa: BLE001
        pass


def main():
    _setup_logging()
    sys.excepthook = _excepthook
    if sys.platform == "win32":
        # crisp UI on high-DPI screens & proper taskbar icon grouping
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CDRipperStudio")
        except Exception:  # noqa: BLE001
            pass
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow

    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)   # required by the embedded browser
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401  (must be loaded before QApplication)
    except ImportError:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("CD Ripper Studio")
    app.setStyle("Fusion")
    from .core.settings import Settings
    from .gui import theme
    settings = Settings.load()
    pt = max(7, min(32, settings.font_pt))
    if theme.load_fonts():                  # Purple Green Flux: Pretendard + JetBrains Mono (bundled)
        font = theme.ui_font(pt)
    else:
        font = QFont("Malgun Gothic") if sys.platform == "win32" else app.font()
        font.setPointSize(pt)
    app.setFont(font)
    theme.apply(settings.theme)
    icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__))), "assets", "icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
