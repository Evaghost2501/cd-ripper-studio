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


def test_info_entered_before_cd_is_kept(tmp_path, monkeypatch):
    """Webcam/typing first, CD second: nothing is lost, web lookup only fills blanks."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from cdripper.core.metadata import AlbumMeta, TrackMeta
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    monkeypatch.setattr(mw.MainWindow, "load_disc", lambda self: None)   # start with "no CD"
    w = mw.MainWindow()
    assert w.toc is None and not w.album.tracks
    # 1) "webcam" result before inserting: track count comes from the text
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setText("1. 이 지금\n2. 팔레트\n3. 이런 엔딩\n4. Black Out")
    w.paste_titles()
    assert [t.title for t in w.album.tracks] == ["이 지금", "팔레트", "이런 엔딩", "Black Out"]
    w.album_edits["album"].setText("Palette"); w._album_edited("album", "Palette")
    # 2) insert the CD (it has CD-Text "Test Album" / "First Song")
    d = w.drives[0]
    w._disc_loaded((d.read_toc(), d.read_cdtext()))
    assert w.album.album == "Palette"
    assert [t.title for t in w.album.tracks] == ["이 지금", "팔레트", "이런 엔딩", "Black Out"]
    assert all(t.length for t in w.album.tracks)                 # lengths from the CD
    assert w.album.albumartist == "Test Artist"                  # blank filled from CD-Text
    # 3) web lookup result only fills blanks
    web = AlbumMeta(albumartist="IU", album="Web Album", date="2017", genre="K-Pop",
                    tracks=[TrackMeta(i + 1, title=f"web {i + 1}", composer="IU") for i in range(4)])
    w._lookup_done([web])
    assert w.album.album == "Palette" and w.album.date == "2017"
    assert w.album.tracks[0].title == "이 지금" and w.album.tracks[0].composer == "IU"
    # 4) eject → sheet cleared for the next CD, but this disc's info is remembered
    w.eject()
    assert w.toc is None and not w.album.tracks and not w.album.album
    w._disc_loaded((d.read_toc(), {}))
    assert w.album.album == "Palette" and w.album.tracks[1].title == "팔레트"
    w.close()


def test_ocr_dialog_without_cd_counts_tracks():
    from cdripper.gui.ocr_dialog import OcrDialog
    dlg = OcrDialog(None, 0)
    dlg.text.setPlainText("01. A 3:00\n02. B\n03. C\n04. D\n05. E")
    assert dlg.table.rowCount() == 5
    dlg._accept()
    assert dlg.n_tracks == 5 and dlg.result[5]["title"] == "E"
