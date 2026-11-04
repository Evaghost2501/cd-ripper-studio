"""Embedded browser cover picker: a real right-click on an image → '이 이미지를 커버로 사용'."""
import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMenu

from cdripper.gui import cover_dialog as cd


def test_data_url_decoding():
    img = QImage(10, 10, QImage.Format_RGB32); img.fill(QColor("red"))
    import base64
    raw = cd.image_bytes_from_qimage(img)
    assert cd.decode_data_url("data:image/jpeg;base64," + base64.b64encode(raw).decode()) == raw
    assert cd.decode_data_url("https://x/y.jpg") is None


@pytest.mark.skipif(not cd.HAS_WEBENGINE, reason="QtWebEngine not available")
def test_right_click_image_in_embedded_browser(tmp_path, monkeypatch, qtbot_wait=None):
    from PySide6.QtTest import QTest
    monkeypatch.setenv("APPDATA", str(tmp_path))
    app = QApplication.instance()
    app.setQuitOnLastWindowClosed(False)
    big = QImage(900, 900, QImage.Format_RGB32); big.fill(QColor("#3a7")); big.save(str(tmp_path / "c.png"))
    (tmp_path / "p.html").write_text('<body style="margin:0"><img src="c.png" width="300" height="300"></body>')
    dlg = cd.CoverDialog(None, "", "")
    dlg.resize(900, 700); dlg.show()
    loaded = []
    dlg.view.loadFinished.connect(loaded.append)
    dlg.view.setUrl(QUrl.fromLocalFile(str(tmp_path / "p.html")))
    for _ in range(200):
        if loaded:
            break
        QTest.qWait(50)
    if not loaded or not loaded[0]:
        pytest.skip("embedded browser could not render in this environment")
    QTest.qWait(500)
    QTest.mouseClick(dlg.view.focusProxy() or dlg.view, Qt.RightButton, Qt.NoModifier, QPoint(150, 150))
    menu = None
    for _ in range(60):
        menu = next((w for w in app.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible()), None)
        if menu:
            break
        QTest.qWait(50)
    assert menu is not None, "context menu did not open"
    use = next(a for a in menu.actions() if "커버로 사용" in a.text())
    assert menu.actions()[0] is use                      # offered first
    use.trigger(); menu.close()
    for _ in range(60):
        if dlg.result:
            break
        QTest.qWait(50)
    assert QImage.fromData(dlg.result).size().toTuple() == (900, 900)   # original, not the 300px shown
