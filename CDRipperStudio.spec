# PyInstaller spec — build with:  pyinstaller CDRipperStudio.spec
# Produces dist/CDRipperStudio.exe (single file).
import os
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

hidden = []
binaries = []
try:  # Windows OCR (pywinrt) is a namespace package with many .pyd files
    hidden += collect_submodules("winrt")
    binaries += collect_dynamic_libs("winrt")
except Exception:
    pass

datas = [("assets/icon.ico", "assets"), ("assets/icon.png", "assets"), ("assets/fonts", "assets/fonts")]
if os.path.exists("ffmpeg.exe"):          # optional: bundle ffmpeg for AAC/ALAC
    binaries.append(("ffmpeg.exe", "."))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["mutagen.id3", "mutagen.flac", "mutagen.mp4", "mutagen.oggopus", "bs4",
                            "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore"],
    # QtWebEngine (embedded browser for the cover search) is needed; it pulls in QtQuick/QtQml/QtPdf
    excludes=["tkinter", "matplotlib", "PySide6.Qt3DCore", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtBluetooth"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="CDRipperStudio",
    icon="assets/icon.ico",
    console=False,
    upx=False,
    version=None,
)
