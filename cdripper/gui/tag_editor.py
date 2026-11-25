"""'음원 태그 편집' tab: edit the tags of audio files that already exist (MP3, FLAC, M4A/M4B, Opus/OGG).

Album-level fields apply to every checked file, but only the fields the user actually changed
('(여러 값)' fields that are left alone keep each file's own value). Track fields are edited per file
in the table. Tags this program does not manage are preserved (see tagger.write_tags).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import naming, tagger
from ..core.metadata import ALBUM_FIELDS, AlbumMeta, TrackMeta, track_values
from . import theme
from .util import px, run_async
from .widgets import CoverLabel, TitlePresetBar, apply_title_to_table

ALBUM_KEYS = [k for k, _ in ALBUM_FIELDS]
COLS = [("save", "✓"), ("file", "파일"), ("number", "#"), ("title", "제목"), ("artist", "아티스트"),
        ("composer", "작곡 · 낭독"), ("status", "상태")]
COL = {k: i for i, (k, _) in enumerate(COLS)}
TRACK_KEYS = ("number", "title", "artist", "composer")
MIXED = "(여러 값)"


@dataclass
class TagFile:
    path: str
    fmt: str
    orig: dict                                    # as read from the file
    cur: dict = field(default_factory=dict)       # edited track values (number/title/artist/composer)
    save: bool = True
    status: str = ""

    def value(self, key):
        return self.cur.get(key, self.orig.get(key, ""))

    @property
    def dirty(self) -> bool:
        return any(str(self.cur[k]) != str(self.orig.get(k, "")) for k in self.cur)


class TagEditorState:
    """Kept on the main window so a UI-size rebuild does not lose loaded files / edits."""

    def __init__(self):
        self.files: list[TagFile] = []
        self.album_edits: dict[str, str] = {}     # album fields the user changed
        self.cover_action: tuple | None = None     # None | ("set", bytes) | ("remove",)
        self.recursive = False
        self.rename = False


def album_value(files: list[TagFile], key: str):
    """Common value of an album field over the files, or MIXED."""
    vals = {str(f.orig.get(key, "") if key not in ("disc", "totaldiscs") else (f.orig.get(key) or ""))
            for f in files}
    return vals.pop() if len(vals) == 1 else MIXED


class TagEditorTab(QWidget):
    def __init__(self, main, state: TagEditorState):
        super().__init__()
        self.main, self.state, self.s = main, state, main.s
        self.setAcceptDrops(True)
        self._loading = False
        root = QVBoxLayout(self)

        # ---- top row -----------------------------------------------------------------
        top = QHBoxLayout()
        for text, fn in (("폴더 열기…", self.open_folder), ("파일 추가…", self.add_files),
                         ("목록 비우기", self.clear_files)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(state.recursive)
        self.recursive.toggled.connect(lambda v: setattr(state, "recursive", v))
        top.addWidget(self.recursive)
        hint = QLabel("음원 파일이나 폴더를 이 화면에 끌어다 놓아도 됩니다 (MP3 · FLAC · M4A/M4B · Opus/OGG)")
        hint.setProperty("role", "muted")
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(hint, 1)
        b = QPushButton("정보 검색…")
        b.setToolTip("웹에서 앨범·도서 정보를 찾아 선택한 파일들에 적용")
        b.clicked.connect(self.search_info)
        top.addWidget(b)
        root.addLayout(top)

        split = QSplitter()
        root.addWidget(split, 1)

        # ---- left: cover + album card (same layout as the rip tab) ----------------
        left = QWidget()
        ll = QVBoxLayout(left)
        self.left_scroll = QScrollArea()
        self.left_scroll.setWidget(left)
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_scroll.setFrameShape(QScrollArea.NoFrame)
        self.cover_lbl = CoverLabel("커버", min_side=120, max_side=4000)
        self.cover_lbl.setProperty("role", "cover")
        ll.addWidget(self.cover_lbl)
        cr = QGridLayout()
        for i, (text, fn) in enumerate((("찾기", self.find_cover), ("파일", self.cover_from_file),
                                        ("붙여넣기", self.cover_from_clipboard), ("삭제", self.remove_cover))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cr.addWidget(b, i // 2, i % 2)
        ll.addLayout(cr)
        ll.addStretch(1)

        self.album_box = QGroupBox("앨범 정보 · 선택한 파일 모두에 적용")
        form = QFormLayout(self.album_box)
        self.edits: dict[str, QLineEdit] = {}
        disc_row = None
        for key, label in ALBUM_FIELDS:
            if key == "genre":
                row = QHBoxLayout()
                self.genre_group = QButtonGroup(self)
                self.genre_btns = {}
                for g in ("Music", "Audiobook"):
                    gb = QPushButton(g, checkable=True)
                    gb.setProperty("variant", "chip")
                    self.genre_group.addButton(gb)
                    self.genre_btns[g] = gb
                    gb.clicked.connect(lambda _=False, g=g: self._album_changed("genre", g))
                    row.addWidget(gb)
                form.addRow(label, row)
                continue
            e = QLineEdit()
            e.textEdited.connect(lambda v, k=key: self._album_changed(k, v.strip()))
            self.edits[key] = e
            if key == "label":
                row = QHBoxLayout()
                row.addWidget(e, 1)
                for name in self.s.label_presets:
                    pb = QPushButton(name)
                    pb.setProperty("variant", "chip")
                    pb.clicked.connect(lambda _=False, n=name: (self.edits["label"].setText(n),
                                                                 self._album_changed("label", n)))
                    row.addWidget(pb)
                form.addRow(label, row)
            elif key == "date":
                disc_row = QHBoxLayout()
                disc_row.addWidget(e, 3)
                form.addRow(label, disc_row)
            elif key == "disc":
                dl = QLabel(" 디스크")
                dl.setProperty("role", "muted")
                disc_row.addWidget(dl)
                disc_row.addWidget(e, 1)
            elif key == "totaldiscs":
                sl = QLabel("/")
                sl.setProperty("role", "muted")
                disc_row.addWidget(sl)
                disc_row.addWidget(e, 1)
            else:
                form.addRow(label, e)
                if key == "albumartist":
                    self.albumartist_label = form.labelForField(e)
        ll.addWidget(self.album_box)
        split.addWidget(self.left_scroll)

        # ---- right: file table + quick titles ---------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLS])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for k, w in (("save", 30), ("file", 220), ("number", 40), ("title", 220), ("artist", 140),
                     ("composer", 110)):
            self.table.setColumnWidth(COL[k], px(w))
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + px(8))
        self.table.itemChanged.connect(self._cell_changed)
        QShortcut(QKeySequence.Delete, self.table, self.remove_selected, context=Qt.WidgetShortcut)
        rl.addWidget(self.table, 1)
        self.title_bar = TitlePresetBar(self.s.title_presets)
        self.title_bar.chosen.connect(lambda t: (apply_title_to_table(self.table, COL["title"], t),
                                                 self.table.setFocus()))
        self.title_bar.edited.connect(main._title_presets_edited)
        rl.addWidget(self.title_bar)
        sel = QHBoxLayout()
        for text, val in (("모두 선택", True), ("모두 해제", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, v=val: self._set_all(v))
            sel.addWidget(b)
        b = QPushButton("목록에서 빼기")
        b.setToolTip("선택한 파일을 목록에서 뺍니다 (파일은 지우지 않음). Delete 키")
        b.clicked.connect(self.remove_selected)
        sel.addWidget(b)
        sel.addStretch()
        rl.addLayout(sel)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        # the album column is never narrower than its content (otherwise the card's right edge is cut)
        min_w = max(px(260), left.minimumSizeHint().width() + self.left_scroll.verticalScrollBar().sizeHint().width()
                    + px(6))
        self.left_scroll.setMinimumWidth(min_w)
        self.left_scroll.setMaximumWidth(max(min_w, px(560)))
        split.setSizes([min_w, px(1000)])

        # ---- bottom -----------------------------------------------------------------------
        bottom = QHBoxLayout()
        self.status = QLabel("파일을 열어 주세요.")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self.status, 1)
        self.rename = QCheckBox("파일 이름도 규칙대로 변경")
        self.rename.setToolTip("저장할 때 '파일 이름 규칙'(CD 리핑 탭)의 파일 이름 부분으로 이름을 바꿉니다.")
        self.rename.setChecked(state.rename)
        self.rename.toggled.connect(lambda v: setattr(state, "rename", v))
        bottom.addWidget(self.rename)
        self.save_btn = QPushButton("변경 사항 저장")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        root.addLayout(bottom)

        self.left_scroll.viewport().installEventFilter(self)
        self.left_scroll.installEventFilter(self)
        self.refresh()

    # ================================================================= loading
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self._fit_cover)
        return super().eventFilter(obj, ev)

    def _fit_cover(self):
        sc = self.left_scroll
        m = sc.widget().layout().contentsMargins()
        bar = sc.verticalScrollBar().sizeHint().width()
        need = sc.widget().minimumSizeHint().width() + bar + 2 * sc.frameWidth()
        if sc.minimumWidth() < need:          # styled sizes are known only once shown
            sc.setMinimumWidth(need)
            sc.setMaximumWidth(max(sc.maximumWidth(), need))
        others = sc.widget().minimumSizeHint().height() - self.cover_lbl.minimumHeight()
        w = max(120, sc.width() - 2 * sc.frameWidth() - bar - m.left() - m.right())
        h = max(120, min(w, sc.viewport().height() - others - px(4)))
        if abs(w - self.cover_lbl.width()) > 1 or abs(h - self.cover_lbl.height()) > 1:
            self.cover_lbl.setFixedSize(w, h)

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "음원 폴더", self.s.output_dir)
        if d:
            self.add_paths([d])

    def add_files(self):
        exts = " ".join(f"*{e}" for e in tagger.EDITABLE_EXTS)
        files, _ = QFileDialog.getOpenFileNames(self, "음원 파일", self.s.output_dir, f"음원 ({exts})")
        if files:
            self.add_paths(files)

    def add_paths(self, paths: list[str]):
        found = []
        for p in paths:
            if os.path.isdir(p):
                if self.state.recursive:
                    for r, _, fs in os.walk(p):
                        found += [os.path.join(r, f) for f in sorted(fs)]
                else:
                    found += [os.path.join(p, f) for f in sorted(os.listdir(p))]
            else:
                found.append(p)
        have = {os.path.normcase(f.path) for f in self.state.files}
        added, bad = 0, []
        for p in found:
            if not p.lower().endswith(tagger.EDITABLE_EXTS) or os.path.normcase(p) in have:
                continue
            try:
                orig = tagger.read_tags(p)
            except Exception as e:  # noqa: BLE001
                bad.append(f"{os.path.basename(p)}: {e}")
                continue
            self.state.files.append(TagFile(p, tagger.format_of(p), orig))
            have.add(os.path.normcase(p))
            added += 1
        self.state.files.sort(key=lambda f: (os.path.dirname(f.path), f.orig.get("disc") or 0,
                                             f.orig.get("number") or 0, os.path.basename(f.path)))
        self.refresh()
        msg = f"{added}개 파일을 불러왔습니다." if added else "불러올 음원 파일이 없습니다."
        if bad:
            msg += f"  읽지 못한 파일 {len(bad)}개"
            self.main.log("태그를 읽지 못한 파일: " + "; ".join(bad[:5]))
        self.status.setText(msg)

    def clear_files(self):
        if self._dirty() and QMessageBox.question(self, "목록 비우기", "저장하지 않은 변경 사항이 있습니다. 비울까요?") \
                != QMessageBox.Yes:
            return
        self.state.files.clear()
        self.state.album_edits.clear()
        self.state.cover_action = None
        self.refresh()
        self.status.setText("파일을 열어 주세요.")

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.state.files[r]
        self.refresh()

    def _set_all(self, v):
        for f in self.state.files:
            f.save = v
        self.refresh()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        images = [p for p in paths if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp"))]
        if images and len(images) == len(paths):        # a picture dropped → cover
            with open(images[0], "rb") as fh:
                self._set_cover_bytes(fh.read())
            return
        self.add_paths(paths)

    # ================================================================= display
    def _selected_files(self) -> list[TagFile]:
        return [f for f in self.state.files if f.save]

    def refresh(self):
        self._loading = True
        files = self.state.files
        self.table.setRowCount(len(files))
        for r, f in enumerate(files):
            for key, _ in COLS:
                if key == "save":
                    it = QTableWidgetItem()
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if f.save else Qt.Unchecked)
                elif key == "file":
                    it = QTableWidgetItem(os.path.basename(f.path))
                    it.setToolTip(f.path)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                elif key == "status":
                    it = QTableWidgetItem(f.status or ("수정됨" if f.dirty else ""))
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    tone = "danger" if f.status.startswith("실패") else ("mint-text" if f.status.startswith("✓")
                                                                        else "warning" if f.dirty else "ink-muted")
                    it.setForeground(theme.qcolor(tone))
                else:
                    v = f.value(key)
                    it = QTableWidgetItem(f"{v:02d}" if key == "number" and v else str(v or ""))
                    if key == "number":
                        it.setFont(theme.mono_font())
                self.table.setItem(r, COL[key], it)
        # album fields: common value, "(여러 값)" or the user's change
        sel = self._selected_files() or files
        for key, e in self.edits.items():
            if key in self.state.album_edits:
                e.setText(self.state.album_edits[key])
                e.setPlaceholderText("")
            else:
                v = album_value(sel, key) if sel else ""
                e.setText("" if v == MIXED else str(v))
                e.setPlaceholderText(MIXED if v == MIXED else "")
        g = self.state.album_edits.get("genre", album_value(sel, "genre") if sel else "")
        self.genre_group.setExclusive(False)
        for name, b in self.genre_btns.items():
            b.setChecked(name == g)
        self.genre_group.setExclusive(True)
        book = g == "Audiobook"
        self.albumartist_label.setText("앨범 아티스트 · 작가" if book else "앨범 아티스트")
        self._show_cover(sel)
        n_dirty = sum(f.dirty for f in files)
        self.album_box.setTitle("앨범 정보 · 선택한 파일 모두에 적용" +
                                (f"  (변경 {len(self.state.album_edits)}개)" if self.state.album_edits else ""))
        self.save_btn.setEnabled(bool(files))
        if files and not self.status.text().endswith("저장했습니다."):
            self.status.setText(f"파일 {len(files)}개 · 저장 대상 {len(self._selected_files())}개"
                                + (f" · 트랙 수정 {n_dirty}개" if n_dirty else ""))
        self._loading = False
        QTimer.singleShot(0, self._fit_cover)

    def _show_cover(self, files):
        act = self.state.cover_action
        if act and act[0] == "set":
            self.cover_lbl.set_image(act[1])
            self.cover_lbl.setToolTip("새 커버 (저장하면 선택한 파일 모두에 들어갑니다)")
        elif act and act[0] == "remove":
            self.cover_lbl.set_image(None, "커버 삭제 예정")
        else:
            covers = {f.orig.get("cover") for f in files}
            first = next((c for c in covers if c), None)
            self.cover_lbl.set_image(first, "커버 없음" if files else "커버")
            self.cover_lbl.setToolTip("파일마다 커버가 다릅니다 (첫 번째 커버 표시)" if len(covers) > 1 else "")

    # ================================================================= editing
    def _album_changed(self, key, value):
        if self._loading:
            return
        old = album_value(self._selected_files() or self.state.files, "albumartist")
        self.state.album_edits[key] = value
        if key == "albumartist" and self.main.auto_fill.isChecked():
            # like the rip tab: track artists that were empty / the old album artist follow
            for f in self._selected_files():
                if f.value("artist") in ("", f.orig.get("albumartist", ""), old):
                    f.cur["artist"] = value
        self.refresh()

    def _cell_changed(self, item):
        if self._loading:
            return
        r, key = item.row(), COLS[item.column()][0]
        f = self.state.files[r]
        if key == "save":
            f.save = item.checkState() == Qt.Checked
            QTimer.singleShot(0, self.refresh)
        elif key in TRACK_KEYS:
            text = item.text().strip()
            if key == "number":
                try:
                    f.cur["number"] = int(text)
                except ValueError:
                    f.cur["number"] = f.orig.get("number", 0)
            else:
                f.cur[key] = text
            f.status = ""
            QTimer.singleShot(0, self.refresh)

    def _set_cover_bytes(self, data: bytes | None):
        if not data:
            return
        from .cover_dialog import image_bytes_from_qimage
        from PySide6.QtGui import QImage
        if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
            img = QImage.fromData(data)
            if img.isNull():
                return
            data = image_bytes_from_qimage(img)
        self.state.cover_action = ("set", data)
        self.refresh()

    def find_cover(self):
        from .cover_dialog import CoverDialog
        album = self.state.album_edits.get("album") or album_value(self.state.files, "album")
        artist = self.state.album_edits.get("albumartist") or album_value(self.state.files, "albumartist")
        dlg = CoverDialog(self, "" if artist == MIXED else artist, "" if album == MIXED else album,
                          country=self.s.store_country)
        if dlg.exec() and dlg.result:
            self._set_cover_bytes(dlg.result)

    def cover_from_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "커버 이미지", "", "이미지 (*.jpg *.jpeg *.png *.webp *.bmp)")
        if p:
            with open(p, "rb") as fh:
                self._set_cover_bytes(fh.read())

    def cover_from_clipboard(self):
        from .cover_dialog import image_bytes_from_qimage
        img = QGuiApplication.clipboard().image()
        if not img.isNull():
            self._set_cover_bytes(image_bytes_from_qimage(img))

    def remove_cover(self):
        self.state.cover_action = ("remove",)
        self.refresh()

    def search_info(self):
        from .search_dialog import SearchDialog
        files = self._selected_files() or self.state.files
        album = self.state.album_edits.get("album") or album_value(files, "album")
        artist = self.state.album_edits.get("albumartist") or album_value(files, "albumartist")
        genre = self.state.album_edits.get("genre") or album_value(files, "genre")
        dlg = SearchDialog(self, "" if artist == MIXED else artist, "" if album == MIXED else album, None,
                           self.s.store_country, genre=genre, sources=self.s.search_sources or None)
        if dlg.exec() and dlg.selected:
            self.apply_result(dlg.selected)

    def apply_result(self, a: AlbumMeta):
        """Web result → album fields (non-empty ones) + track titles by track number."""
        book = getattr(a, "_is_book", False)
        for key in ("album", "albumartist", "label", "date", "comment"):
            v = str(getattr(a, key) or "").strip()
            if v:
                self.state.album_edits[key] = v
        if book:
            self.state.album_edits["genre"] = "Audiobook"
        by_no = {t.number: t for t in a.tracks}
        files = self._selected_files()
        for i, f in enumerate(files):
            n = f.value("number") or (i + 1)
            t = by_no.get(n)
            if t and t.title:
                f.cur["title"] = t.title
            if t and t.artist:
                f.cur["artist"] = t.artist
            elif book and a.albumartist:
                f.cur["artist"] = a.albumartist
            if t and t.composer:
                f.cur["composer"] = t.composer
        if a.cover_url and not self.state.cover_action:
            run_async(self.main._fetch_best_cover, a,
                      on_done=lambda d: d and self.state.cover_action is None and self._set_cover_bytes(d),
                      on_fail=lambda e: None)
        self.refresh()

    def _dirty(self) -> bool:
        return bool(self.state.album_edits or self.state.cover_action or any(f.dirty for f in self.state.files))

    # ================================================================= saving
    def save(self):
        files = self._selected_files()
        if not files:
            return QMessageBox.information(self, "저장", "저장할 파일을 체크하세요.")
        edits, act = dict(self.state.album_edits), self.state.cover_action
        cover = tagger.prepare_cover(act[1], self.s.cover_max_px) if act and act[0] == "set" else None
        remove = bool(act and act[0] == "remove")
        file_tpl = (self.s.template.replace("\\", "/").split("/")[-1]) or "{track:02}. {title}"
        ok = failed = 0
        for f in files:
            try:
                o = f.orig
                album = AlbumMeta(**{k: str(edits.get(k, o.get(k) or "")) for k in ALBUM_KEYS})
                album.disc = album.disc or "1"
                album.totaldiscs = album.totaldiscs or "1"
                album.compilation = bool(o.get("compilation"))
                album._totaltracks = o.get("totaltracks") or len(self.state.files)
                track = TrackMeta(number=int(f.value("number") or 0) or 1, title=str(f.value("title")),
                                  artist=str(f.value("artist")), composer=str(f.value("composer")),
                                  lyricist=o.get("lyricist", ""), isrc=o.get("isrc", ""),
                                  lyrics=o.get("lyrics", ""))
                tagger.write_tags(f.path, f.fmt, album, track, cover, self.s.id3v23, remove_cover=remove)
                if self.state.rename:
                    new = naming.render(file_tpl, track_values(album, track)).split("/")[-1]
                    new_path = os.path.join(os.path.dirname(f.path), new + os.path.splitext(f.path)[1])
                    if os.path.normcase(new_path) != os.path.normcase(f.path):
                        if os.path.exists(new_path):
                            raise FileExistsError(f"같은 이름의 파일이 있습니다: {os.path.basename(new_path)}")
                        os.rename(f.path, new_path)
                        f.path = new_path
                f.orig, f.cur = tagger.read_tags(f.path), {}
                f.status = "✓ 저장됨"
                ok += 1
            except Exception as e:  # noqa: BLE001
                f.status = f"실패 · {e}"
                failed += 1
        self.state.album_edits.clear()
        self.state.cover_action = None
        self.status.setText(f"{ok}개 파일을 저장했습니다." + (f"  실패 {failed}개" if failed else ""))
        self.main.log(f"태그 편집: {ok}개 저장" + (f", {failed}개 실패" if failed else ""))
        self.refresh()
        self.status.setText(f"{ok}개 파일을 저장했습니다." + (f"  실패 {failed}개" if failed else ""))
