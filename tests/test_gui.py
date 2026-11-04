import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from cdripper.core.toc import Toc  # noqa: F401
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    w = mw.MainWindow()
    d = w.drive()
    w._disc_loaded((d.read_toc(), {}))
    yield w, app, tmp_path
    w.close()


def test_auto_fill_tracks(win):
    w, app, _ = win
    assert w.auto_fill.isChecked()                      # default on
    w.album.tracks[1].artist = "Guest"
    w.album_edits["albumartist"].setText("아이유")
    w._album_edited("albumartist", "아이유")
    assert [t.artist for t in w.album.tracks] == ["아이유", "Guest", "아이유", "아이유"]
    w._album_edited("albumartist", "IU")                # follows edits, keeps custom value
    assert [t.artist for t in w.album.tracks] == ["IU", "Guest", "IU", "IU"]
    w._album_edited("genre", "K-Pop")
    assert all(t.genre == "K-Pop" for t in w.album.tracks)
    w.auto_fill.setChecked(False)
    w._album_edited("genre", "Ballad")
    assert all(t.genre == "K-Pop" for t in w.album.tracks)


def test_delete_and_restore(win):
    w, app, _ = win
    w.table.selectRow(1)
    w.delete_tracks()
    assert [t.number for t in w.album.tracks] == [1, 3, 4]
    assert w.table.rowCount() == 3 and w.restore_btn.isEnabled()
    w.restore_tracks()
    assert [t.number for t in w.album.tracks] == [1, 2, 3, 4]
    assert not w.restore_btn.isEnabled()


def test_outdir_and_template_saved_as_default(win):
    w, app, tmp = win
    w.outdir.setText(str(tmp / "MyMusic"))
    w._outdir_changed()
    w.tpl.setEditText("{album}/{track:02} {title}")
    w._save_timer.timeout.emit()                        # flush the debounced save
    saved = json.load(open(tmp / "CDRipperStudio" / "settings.json", encoding="utf-8"))
    assert saved["output_dir"] == os.path.normpath(str(tmp / "MyMusic"))
    assert saved["template"] == "{album}/{track:02} {title}"
    from cdripper.gui.main_window import MainWindow
    w2 = MainWindow()                                   # new launch picks them up
    assert w2.outdir.text() == saved["output_dir"] and w2.tpl.currentText() == saved["template"]
    w2.close()
