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
    w._album_edited("comment", "CD rip")
    assert all(t.comment == "CD rip" for t in w.album.tracks)
    w.auto_fill.setChecked(False)
    w._album_edited("comment", "other")
    assert all(t.comment == "CD rip" for t in w.album.tracks)


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


def test_album_form_order_and_removed_fields(win):
    w, app, _ = win
    from PySide6.QtWidgets import QFormLayout, QLabel
    form = w.album_edits["album"].parentWidget().layout()
    labels = [form.itemAt(i, QFormLayout.LabelRole).widget().text() for i in range(4)]
    assert labels == ["앨범", "앨범 아티스트", "레이블", "장르"]
    assert "catalog" not in w.album_edits and "barcode" not in w.album_edits
    assert "genre" not in w.album_edits                       # buttons only, no typing


def test_genre_buttons(win, tmp_path):
    w, app, tmp = win
    assert w.album.genre == "Music" and w.genre_btns["Music"].isChecked()   # default
    w.genre_btns["Audiobook"].click()
    assert w.album.genre == "Audiobook" and not w.genre_btns["Music"].isChecked()
    assert w.s.genre == "Audiobook"                            # default for the next CD
    from cdripper.core.metadata import AlbumMeta
    w.apply_album(AlbumMeta(album="X", genre="K-Pop"))        # web genres are ignored
    assert w.album.genre == "Audiobook"
    w.eject()
    w._disc_loaded((w.drive().read_toc(), {}))
    w.album = type(w.album)(genre=w.s.genre); w.refresh_all()
    assert w.genre_btns["Audiobook"].isChecked()


def test_album_subfolder_option(win):
    w, app, tmp = win
    w.outdir.setText(str(tmp / "Out")); w._outdir_changed()
    w.album_edits["album"].setText("Palette"); w._album_edited("album", "Palette")
    w.tpl.setEditText("{track:02}. {title}")
    assert w.album_folder.isChecked()                          # default on
    assert w.preview.text() == os.path.join(str(tmp / "Out"), "Palette", "01. Track 01.mp3")
    w.album_folder.setChecked(False)
    assert w.preview.text() == os.path.join(str(tmp / "Out"), "01. Track 01.mp3")
    assert w.s.album_folder is False


def test_ui_size_slider_live(win):
    w, app, _ = win
    w.set_ui_size(12)
    app.processEvents()
    assert app.font().pointSize() == 12 and w.s.font_pt == 12
    small = w.rip_btn.minimumHeight()
    w.set_ui_size(24)
    app.processEvents()
    assert w.rip_btn.minimumHeight() > small                  # sizes follow the font
    w.set_ui_size(9)
    app.processEvents()


def test_old_default_template_migrates(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from cdripper.core.settings import Settings, config_dir
    with open(os.path.join(config_dir(), "settings.json"), "w") as f:
        json.dump({"template": "{albumartist}/[{year} - ]{album}/{track:02}. {title}"}, f)
    assert Settings.load().template == "{track:02}. {title}"


def test_columns_resizable_and_remembered(win):
    w, app, tmp = win
    from PySide6.QtWidgets import QHeaderView
    from cdripper.gui.main_window import COL, MainWindow
    hh = w.table.horizontalHeader()
    for k in ("title", "artist", "composer", "length"):
        assert hh.sectionResizeMode(COL[k]) == QHeaderView.Interactive
    w.table.setColumnWidth(COL["title"], 333)               # = the user dragging the header edge
    w._save_timer.timeout.emit()
    w2 = MainWindow()
    assert abs(w2.table.columnWidth(COL["title"]) - 333) <= 2
    w2.close()


def test_ui_size_change_resizes_everything(win):
    w, app, _ = win
    w.album_edits["album"].setText("Keep me"); w._album_edited("album", "Keep me")
    w.log("hello log")
    w.set_ui_size(20)
    app.processEvents()
    big = {n: x.height() for n, x in (("fmt", w.fmt), ("tpl", w.tpl), ("preview", w.preview), ("log", w.logbox))}
    w.set_ui_size(10)
    app.processEvents()
    for n, x in (("fmt", w.fmt), ("tpl", w.tpl), ("preview", w.preview), ("log", w.logbox)):
        assert x.font().pointSize() == 10, n
        assert x.sizeHint().height() < big[n] or x.height() < big[n], n
    assert w.album_edits["album"].text() == "Keep me"          # data survives the rebuild
    assert "hello log" in w.logbox.toPlainText()
    assert len(w.findChildren(type(w._toolbar))) == 1          # no duplicated toolbars
    w.set_ui_size(9)


def test_untitled_tracks_unchecked_by_default(win):
    w, app, _ = win
    from cdripper.gui.main_window import COL
    from PySide6.QtCore import Qt
    d = w.drive()
    w._disc_loaded((d.read_toc(), d.read_cdtext()))           # CD-Text titles only for tracks 1, 2
    assert [t.rip for t in w.album.tracks] == [True, True, False, False]
    assert w.table.item(2, COL["rip"]).checkState() == Qt.Unchecked
    w.table.item(2, COL["title"]).setText("Third")            # typing a title ticks it
    assert w.album.tracks[2].rip and w.table.item(2, COL["rip"]).checkState() == Qt.Checked
    w.table.item(0, COL["rip"]).setCheckState(Qt.Unchecked)   # manual choice wins…
    w.album.tracks[0].title = "Renamed"; w.refresh_tracks()
    assert w.album.tracks[0].rip is False                      # …even after the title changes
    w.table.item(3, COL["rip"]).setCheckState(Qt.Checked)     # untitled but ticked by hand
    assert w.album.tracks[3].rip is True


def test_playlist_and_cue_off_by_default_and_migrated(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from cdripper.core.settings import Settings, config_dir
    s = Settings()
    assert s.write_m3u is False and s.write_cue is False
    with open(os.path.join(config_dir(), "settings.json"), "w") as f:
        json.dump({"write_m3u": True, "write_cue": True}, f)   # saved by an older version
    s = Settings.load()
    assert s.write_m3u is False and s.write_cue is False
    s.write_cue = True; s.save()                               # a later explicit choice is kept
    assert Settings.load().write_cue is True


def test_quick_title_buttons_main_table(win):
    w, app, _ = win
    from PySide6.QtWidgets import QPushButton
    from cdripper.gui.main_window import COL
    names = [b.text() for b in w.title_bar.findChildren(QPushButton)]
    assert names[:6] == ["JYbooks' Company Song", "Story Song", "Story Reading", "Echo Chant", "Kids Song",
                         "Melody"]
    w.table.selectRow(1)
    w.title_bar.chosen.emit("Story Song")              # = click
    w.title_bar.chosen.emit("Echo Chant")              # selection moved to the next track
    assert [t.title for t in w.album.tracks][1:3] == ["Story Song", "Echo Chant"]
    assert w.album.tracks[2].rip                       # a title ticks the track
    w.table.clearSelection()
    w.table.selectRow(0); w.table.selectRow(3)
    from PySide6.QtCore import QItemSelectionModel
    sm = w.table.selectionModel()
    sm.select(w.table.model().index(0, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    w.title_bar.chosen.emit("Melody")                  # several rows at once
    assert w.album.tracks[0].title == "Melody" and w.album.tracks[3].title == "Melody"


def test_quick_title_list_is_editable_and_saved(win):
    w, app, tmp = win
    import json
    w.title_bar.edited.emit(["Intro", "Story Song"])   # what the edit dialog emits
    w.title_bar.set_presets(["Intro", "Story Song"])
    w._save_timer.timeout.emit()
    saved = json.load(open(tmp / "CDRipperStudio" / "settings.json", encoding="utf-8"))
    assert saved["title_presets"] == ["Intro", "Story Song"]


def test_quick_title_in_webcam_dialog():
    from cdripper.gui.ocr_dialog import OcrDialog
    dlg = OcrDialog(None, 4, presets=["Story Song", "Melody"])
    dlg.text.setPlainText("01. 잘 인식된 곡\n02. ?!@#\n03.\n04.")   # poor recognition
    dlg.table.selectRow(1)
    dlg._quick_title("Story Song")
    dlg._quick_title("Melody")
    dlg._accept()
    assert dlg.result[1]["title"] == "잘 인식된 곡"
    assert dlg.result[2]["title"] == "Story Song" and dlg.result[3]["title"] == "Melody"
    # no CD, no text: titles picked one after another create the tracks
    dlg2 = OcrDialog(None, 0, presets=["Story Song", "Melody"])
    for t in ("Story Song", "Melody", "Story Song"):
        dlg2._quick_title(t)
    dlg2._accept()
    assert dlg2.n_tracks == 3 and [dlg2.result[i]["title"] for i in (1, 2, 3)] == ["Story Song", "Melody", "Story Song"]
