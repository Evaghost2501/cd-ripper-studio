"""Purple Green Flux theme."""
import json
import os

from PySide6.QtWidgets import QApplication

from cdripper.gui import theme


def test_tokens_match_design_system():
    assert theme.color("violet", "dark") == "#8f7bff" and theme.color("violet", "light") == "#5b3df5"
    assert theme.color("lime", "dark") == "#cdf56a" and theme.color("bg", "light") == "#f6f4f9"


def test_bundled_fonts_load():
    assert theme.load_fonts()
    from PySide6.QtGui import QFontDatabase
    fams = QFontDatabase.families()
    assert theme.SANS in fams and theme.MONO in fams
    assert theme.ui_font(14).family() == theme.SANS


def test_stylesheet_both_themes_and_sizes(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for t in ("dark", "light"):
        for px in (12, 24):
            qss = theme.stylesheet(t, px)
            assert theme.color("violet", t) in qss and 'variant="primary"' in qss and 'variant="chip"' in qss
            assert "{" in qss and "{{" not in qss
    # pill radius = half of the control height, so buttons are real pills at any size
    total = round(24 * 1.35) + 2 * round(24 * 0.22) + 2          # content + padding + border
    assert f"border-radius: {total // 2}px" in theme.stylesheet("dark", 24)


def test_theme_toggle_saved_and_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    monkeypatch.setattr(mw.MainWindow, "load_disc", lambda self: None)
    w = mw.MainWindow()
    assert w.s.theme == "dark" and w.theme_btns["dark"].isChecked()
    w.theme_btns["light"].click()
    assert theme.current() == "light" and theme.color("bg") in QApplication.instance().styleSheet()
    w._save_timer.timeout.emit()
    saved = json.load(open(os.path.join(str(tmp_path), "CDRipperStudio", "settings.json"), encoding="utf-8"))
    assert saved["theme"] == "light"
    assert w.rip_btn.property("variant") == "primary"
    assert w.genre_btns["Music"].property("variant") == "chip"
    w.theme_btns["dark"].click()
    QApplication.instance().setStyleSheet("")
    w.close()
