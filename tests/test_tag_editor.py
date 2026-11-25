"""'음원 태그 편집' tab: load existing files, change album / track fields, save, keep foreign tags."""
import os

import mutagen
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from cdripper.core import tagger
from cdripper.core.drive import FakeDrive
from cdripper.core.job import RipJob
from cdripper.core.metadata import AlbumMeta, TrackMeta
from cdripper.core.settings import Settings


def _make_album(folder, fmt="mp3", quality="cbr320", album="Old Album", artist="Old Artist"):
    d = FakeDrive()
    toc = d.read_toc()
    am = AlbumMeta(albumartist=artist, album=album, date="2001", genre="Music",
                   tracks=[TrackMeta(t.number, title=f"Song {t.number}", length=t.length) for t in toc.tracks[:3]])
    s = Settings(output_dir=str(folder), format=fmt, quality=quality, accuraterip=False, secure_mode=False,
                 album_folder=False)
    rep = RipJob(d, toc, am, None, s, on_log=lambda m: None).run()
    return [r.path for r in rep.tracks]


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    monkeypatch.setattr(mw.MainWindow, "load_disc", lambda self: None)
    w = mw.MainWindow()
    yield w, tmp_path
    w.close()


def test_read_tags_roundtrip(tmp_path):
    for fmt, q in (("mp3", "cbr320"), ("flac", "8"), ("opus", "0.9")):
        [p, *_] = _make_album(tmp_path / fmt, fmt, q)
        t = tagger.read_tags(p)
        assert (t["album"], t["albumartist"], t["title"], t["number"], t["totaltracks"]) == \
            ("Old Album", "Old Artist", "Song 1", 1, 3), fmt


def test_edit_album_and_titles_keeps_foreign_tags(win, tmp_path):
    w, _ = win
    paths = _make_album(tmp_path / "a")
    from mutagen.id3 import ID3, TXXX
    tags = ID3(paths[0]); tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text="-6.5 dB")); tags.save()
    ed = w.tag_editor
    ed.add_paths([str(tmp_path / "a")])
    assert [os.path.basename(f.path) for f in ed.state.files] == [os.path.basename(p) for p in paths]
    assert ed.edits["album"].text() == "Old Album"
    # album fields: change album artist (→ author) + genre, track title via table and quick title
    ed.edits["albumartist"].setText("Max Grover"); ed._album_changed("albumartist", "Max Grover")
    ed.genre_btns["Audiobook"].click()
    ed.table.item(0, 3).setText("JYbooks' Company Song")
    ed.table.selectRow(1); ed.title_bar.chosen.emit("Story Song")
    img = QImage(50, 50, QImage.Format_RGB32); img.fill(QColor("red"))
    from cdripper.gui.cover_dialog import image_bytes_from_qimage
    ed._set_cover_bytes(image_bytes_from_qimage(img))
    ed.save()
    t0, t1, t2 = (tagger.read_tags(p) for p in paths)
    assert (t0["albumartist"], t0["artist"], t0["genre"], t0["title"]) == \
        ("Max Grover", "Max Grover", "Audiobook", "JYbooks' Company Song")
    assert t1["title"] == "Story Song" and t2["title"] == "Song 3"
    assert t0["album"] == "Old Album" and t0["date"] == "2001"          # untouched fields kept
    assert t0["cover"] and t2["cover"]
    f = mutagen.File(paths[0])
    assert str(f.tags["TXXX:REPLAYGAIN_TRACK_GAIN"]) == "-6.5 dB"     # foreign tag preserved
    assert str(f.tags["TXXX:AUTHOR"]) == "Max Grover"
    assert all(x.status == "✓ 저장됨" for x in ed.state.files)


def test_mixed_values_only_changed_fields_apply(win, tmp_path):
    w, _ = win
    a = _make_album(tmp_path / "x", album="Album A")
    b = _make_album(tmp_path / "y", album="Album B")
    ed = w.tag_editor
    ed.add_paths([a[0], b[0]])
    assert ed.edits["album"].text() == "" and ed.edits["album"].placeholderText() == "(여러 값)"
    ed.edits["label"].setText("JYbooks"); ed._album_changed("label", "JYbooks")
    ed.save()
    ta, tb = tagger.read_tags(a[0]), tagger.read_tags(b[0])
    assert (ta["album"], tb["album"]) == ("Album A", "Album B")           # mixed field left alone
    assert ta["label"] == tb["label"] == "JYbooks"


def test_unchecked_files_are_not_saved_and_rename(win, tmp_path):
    w, _ = win
    paths = _make_album(tmp_path / "r")
    ed = w.tag_editor
    ed.add_paths([str(tmp_path / "r")])
    ed.table.item(2, 0).setCheckState(Qt.Unchecked)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    ed.table.item(0, 3).setText("New Title")
    ed.rename.setChecked(True)
    ed.save()
    names = sorted(os.listdir(tmp_path / "r"))
    assert "01. New Title.mp3" in names and os.path.basename(paths[2]) in names
    assert tagger.read_tags(paths[2])["title"] == "Song 3"


def test_editor_state_survives_ui_size_change(win, tmp_path):
    w, _ = win
    _make_album(tmp_path / "s")
    w.tag_editor.add_paths([str(tmp_path / "s")])
    w.tag_editor._album_changed("album", "Pending")
    w.tabs.setCurrentIndex(1)
    w.set_ui_size(12)
    assert w.tabs.currentIndex() == 1 and w.tabs.tabText(1) == "음원 태그 편집"
    assert len(w.tag_editor.state.files) == 3 and w.tag_editor.edits["album"].text() == "Pending"
    w.set_ui_size(9)


def test_editor_album_column_not_clipped(win, tmp_path):
    w, _ = win
    from PySide6.QtTest import QTest
    w.resize(1400, 900); w.show(); w.tabs.setCurrentIndex(1); QTest.qWait(200)
    sc = w.tag_editor.left_scroll
    assert sc.widget().width() <= sc.viewport().width()
