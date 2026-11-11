from __future__ import annotations

import copy
import json
import os
import re

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence, QFontMetrics, QShortcut, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QButtonGroup, QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..core import encoders, naming, ocr
from ..core.drive import BaseDrive, DriveError, list_drives
from ..core.job import JobReport, RipJob, TrackReport, ar_text, output_path
from ..core.metadata import ALBUM_FIELDS, GENRES, AlbumMeta, TrackMeta, track_values
from ..core.settings import Settings, config_dir, logs_dir
from ..core.toc import Toc, fmt_duration
from ..sources import web
from .cover_dialog import CoverDialog
from .ocr_dialog import OcrDialog
from .search_dialog import SearchDialog
from .settings_dialog import SettingsDialog
from .util import fit_to_screen, image_size, pixmap_from_bytes, px, run_async, ui_scale
from .widgets import CoverLabel, TitlePresetBar, apply_title_to_table

COLS = [("rip", "✔"), ("number", "#"), ("title", "제목"), ("artist", "아티스트"), ("composer", "작곡"),
        ("lyricist", "작사"), ("isrc", "ISRC"), ("length", "길이"), ("status", "상태")]
COL = {k: i for i, (k, _) in enumerate(COLS)}
EDITABLE = {"title", "artist", "composer", "lyricist", "isrc"}
# default column widths at the original 9 pt size (scaled with the UI size); the last column fills the rest
DEFAULT_COL_WIDTHS = {"rip": 30, "number": 34, "title": 230, "artist": 150, "composer": 90, "lyricist": 80,
                      "isrc": 100, "length": 48}


class RipThread(QThread):
    progress = Signal(int, float, str)
    track_done = Signal(object)
    log = Signal(str)
    report = Signal(object)

    def __init__(self, job_args):
        super().__init__()
        self.job = RipJob(*job_args, on_progress=self.progress.emit, on_track_done=self.track_done.emit,
                          on_log=self.log.emit)

    def run(self):
        try:
            self.report.emit(self.job.run())
        except Exception as e:  # noqa: BLE001
            self.log.emit(f"오류: {e}")
            self.report.emit(JobReport())


class TrackDetailDialog(QDialog):
    def __init__(self, parent, t: TrackMeta):
        super().__init__(parent)
        self.setWindowTitle(f"트랙 {t.number:02d} 상세 정보")
        fit_to_screen(self, 520, 520)
        self.t = t
        f = QFormLayout(self)
        self.edits = {}
        for key, label in (("title", "제목"), ("artist", "아티스트"), ("composer", "작곡"), ("lyricist", "작사"),
                           ("isrc", "ISRC"), ("comment", "코멘트")):
            e = QLineEdit(getattr(t, key))
            self.edits[key] = e
            f.addRow(label, e)
        self.lyrics = QPlainTextEdit(t.lyrics)
        f.addRow("가사", self.lyrics)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _ok(self):
        for k, e in self.edits.items():
            setattr(self.t, k, e.text().strip())
        self.t.lyrics = self.lyrics.toPlainText().strip()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CD Ripper Studio {__version__}")
        fit_to_screen(self, 1360, 820)
        self.s = Settings.load()
        self.drives: list[BaseDrive] = []
        self.toc: Toc | None = None
        self.album = AlbumMeta(genre=self.s.genre)
        self.cover: bytes | None = None
        self.rip_thread: RipThread | None = None
        self._media = None
        self._fill_empty_only = False
        self._font_timer = QTimer(self, singleShot=True, interval=150,
                                  timeout=lambda: self.set_ui_size(self.ui_slider.value()))
        self._build_ui()
        self.refresh_drives()
        self.poll = QTimer(self, interval=2000, timeout=self._poll_media)
        self.poll.start()
        QTimer.singleShot(200, self.load_disc)

    # ================================================================ UI
    def _build_ui(self):
        tb = QToolBar("main", movable=False)
        self._toolbar = tb
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" 드라이브 "))
        self.drive_combo = QComboBox()
        self.drive_combo.currentIndexChanged.connect(lambda *_: self.load_disc())
        tb.addWidget(self.drive_combo)

        def act(text, fn, tip="", shortcut=None):
            a = QAction(text, tb)
            a.triggered.connect(fn)
            a.setToolTip(tip or text)
            if shortcut:
                a.setShortcut(shortcut)
            tb.addAction(a)
            return a

        act("⟳ 새로고침", self.load_disc, "디스크 다시 읽기 (F5)", "F5")
        act("⏏ 꺼내기", self.eject)
        tb.addSeparator()
        act("🌐 정보 검색", self.search_metadata, "웹에서 앨범 정보 찾기: MusicBrainz · iTunes · Deezer (Ctrl+F)", "Ctrl+F")
        act("🖼 커버", self.find_cover, "Google 이미지에서 커버 찾기 (Ctrl+I)", "Ctrl+I")
        act("📷 웹캠 인식", self.ocr_tracks, "CD 뒷면을 웹캠으로 찍어 트랙 제목 인식 (Ctrl+K)", "Ctrl+K")
        tb.addSeparator()
        act("⚙ 설정", self.open_settings)
        tb.addSeparator()
        tb.addWidget(QLabel(" UI 크기 "))
        self.ui_slider = QSlider(Qt.Horizontal)
        self.ui_slider.setRange(8, 28)
        self.ui_slider.setPageStep(2)
        self.ui_slider.setValue(max(8, min(28, self.s.font_pt)))
        self.ui_slider.setToolTip("글자와 화면 요소 크기 (원래 크기 = 9)")
        self.ui_size_lbl = QLabel()
        tb.addWidget(self.ui_slider)
        tb.addWidget(self.ui_size_lbl)
        self.ui_slider.valueChanged.connect(self._ui_slider_moved)
        self.ui_slider.sliderReleased.connect(lambda: self._font_timer.start())
        self._ui_slider_moved(self.ui_slider.value(), apply=False)

        # menu
        m = self.menuBar().addMenu("파일")
        m.addAction("메타데이터 불러오기 (JSON)…", self.load_json)
        m.addAction("메타데이터 저장 (JSON)…", self.save_json)
        m.addSeparator()
        m.addAction("출력 폴더 열기", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.s.output_dir)))
        m.addAction("종료", self.close)
        t = self.menuBar().addMenu("도구")
        t.addAction("클립보드의 트랙 목록을 제목에 붙여넣기", self.paste_titles, QKeySequence("Ctrl+Shift+V"))
        t.addAction("선택한 트랙에 값 일괄 입력…", self.batch_edit)
        t.addAction("앨범 아티스트를 모든 트랙 아티스트로 복사", self.copy_albumartist)
        t.addAction("제목 앞의 번호 제거 (\"01. \" 등)", self.strip_numbers)
        t.addAction("제목에서 (feat. …) 을 아티스트로 이동", self.move_feat)
        t.addAction("영문 제목 대소문자 정리 (Title Case)", self.title_case)
        t.addAction("모든 트랙 정보 지우기", self.clear_tracks)
        h = self.menuBar().addMenu("도움말")
        h.addAction("드라이브 오프셋 찾기 (AccurateRip)",
                    lambda: QDesktopServices.openUrl(QUrl("http://www.accuraterip.com/driveoffsets.htm")))
        h.addAction("이 디스크를 MusicBrainz에 등록하기", self.submit_mb)
        h.addAction("추출 로그 폴더 열기", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(logs_dir())))
        h.addAction("정보", lambda: QMessageBox.about(
            self, "정보", f"CD Ripper Studio {__version__}\n\nMusicBrainz · Cover Art Archive · iTunes · Deezer · "
                         "AccurateRip 데이터를 사용합니다.\nMP3 인코딩: LAME"))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        split = QSplitter()
        root.addWidget(split, 1)

        # ---- album panel ----------------------------------------------
        # scrollable so the album panel can never push the bottom controls off-screen
        left = QWidget()
        ll = QVBoxLayout(left)
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        self.left_scroll = left_scroll
        self.cover_lbl = CoverLabel("커버 없음\n끌어다 놓기", min_side=170, max_side=px(220))
        self.cover_lbl.setStyleSheet("border:1px dashed #999; color:#888;")
        self.cover_lbl.setAcceptDrops(True)
        self.setAcceptDrops(True)
        # cover on the left, its buttons stacked on the right (saves vertical space)
        cover_row = QHBoxLayout()
        cover_col = QVBoxLayout()
        cover_col.addWidget(self.cover_lbl)
        self.cover_info = QLabel(alignment=Qt.AlignCenter)
        self.cover_info.setStyleSheet("color:#777")
        cover_col.addWidget(self.cover_info)
        cover_row.addLayout(cover_col)
        cr = QVBoxLayout()
        cover_btns = []
        for text, fn in (("🔍 찾기", self.find_cover), ("📂 파일", self.cover_from_file),
                         ("📋 붙여넣기", self.cover_from_clipboard), ("✖ 삭제", lambda: self.set_cover(None))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            cover_btns.append(b)
            cr.addWidget(b)
        btn_w = max(b.sizeHint().width() for b in cover_btns)     # compact, all the same width
        for b in cover_btns:
            b.setFixedWidth(btn_w)
        cr.addStretch()
        cover_row.setStretch(0, 1)                               # the cover gets the spare width
        cover_row.addLayout(cr, 0)
        ll.addLayout(cover_row)

        g = QGroupBox("앨범 정보")
        form = QFormLayout(g)
        self.album_edits: dict[str, QLineEdit] = {}
        for key, label in ALBUM_FIELDS:
            if key == "genre":           # chosen with buttons only
                row = QHBoxLayout()
                self.genre_group = QButtonGroup(self)
                self.genre_btns = {}
                for gname, glabel in (("Music", "🎵 Music"), ("Audiobook", "📖 Audiobook")):
                    b = QPushButton(glabel, checkable=True)
                    self.genre_group.addButton(b)
                    self.genre_btns[gname] = b
                    b.clicked.connect(lambda _=False, gn=gname: self._genre_chosen(gn))
                    row.addWidget(b)
                form.addRow(label, row)
                continue
            e = QLineEdit()
            e.textEdited.connect(lambda v, k=key: self._album_edited(k, v))
            self.album_edits[key] = e
            if key == "label":           # short box + one-click presets (e.g. JYbooks)
                row = QHBoxLayout()
                row.addWidget(e, 1)
                for name in self.s.label_presets:
                    b = QPushButton(name)
                    b.setToolTip(f"레이블에 '{name}' 입력")
                    b.clicked.connect(lambda _=False, n=name: self._set_label(n))
                    row.addWidget(b)
                form.addRow(label, row)
                continue
            form.addRow(label, e)
        self.album_edits["date"].setPlaceholderText("YYYY 또는 YYYY-MM-DD")
        self.compilation = QCheckBox("컴필레이션 (여러 아티스트)")
        self.compilation.toggled.connect(self._compilation_toggled)
        form.addRow(self.compilation)
        self.auto_fill = QCheckBox("앨범 정보를 트랙에 자동 반영 (아티스트·코멘트)")
        self.auto_fill.setToolTip("앨범 아티스트·코멘트를 입력하면 바로 모든 트랙에 들어갑니다.\n"
                                  "트랙에 따로 입력한 다른 값(예: feat. 아티스트)은 그대로 둡니다.")
        self.auto_fill.setChecked(self.s.auto_fill_tracks)
        self.auto_fill.toggled.connect(self._auto_fill_toggled)
        form.addRow(self.auto_fill)
        self.disc_info = QLabel()
        self.disc_info.setStyleSheet("color:#777")
        self.disc_info.setWordWrap(True)
        self.disc_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(self.disc_info)
        ll.addWidget(g)
        ll.addStretch()
        split.addWidget(left_scroll)

        # ---- track table ------------------------------------------------
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
        # every column can be resized by dragging the header edge; widths are remembered
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        hh.sectionResized.connect(self._column_resized)
        # less-used columns hidden by default; right-click the header to show/hide (choice is remembered)
        for k in self.s.hidden_columns:
            if k in COL:
                self.table.setColumnHidden(COL[k], True)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        self.table.itemChanged.connect(self._track_edited)
        self.table.cellDoubleClicked.connect(self._cell_dbl)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        rl.addWidget(self.table, 1)
        self.title_bar = TitlePresetBar(self.s.title_presets)
        self.title_bar.chosen.connect(self._quick_title)
        self.title_bar.edited.connect(self._title_presets_edited)
        rl.addWidget(self.title_bar)
        sel = QHBoxLayout()
        for text, val in (("모두 선택", True), ("모두 해제", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, v=val: self._set_all_rip(v))
            sel.addWidget(b)
        b = QPushButton("🗑 선택 트랙 삭제")
        b.setToolTip("선택한 트랙을 목록에서 뺍니다 (Delete 키). 리핑되지 않습니다.")
        b.clicked.connect(self.delete_tracks)
        sel.addWidget(b)
        self.restore_btn = QPushButton("↺ 삭제한 트랙 복원")
        self.restore_btn.clicked.connect(self.restore_tracks)
        self.restore_btn.setEnabled(False)
        sel.addWidget(self.restore_btn)
        QShortcut(QKeySequence.Delete, self.table, self.delete_tracks, context=Qt.WidgetShortcut)
        hint = QLabel("더블클릭: 수정 · '#' 더블클릭: 가사/코멘트 · 우클릭: 일괄 편집 · 제목줄 우클릭: 열 표시")
        hint.setStyleSheet("color:#777")
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sel.addWidget(hint, 1)
        rl.addLayout(sel)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        self.split, self.right = split, right

        # ---- output panel ------------------------------------------------
        og = QGroupBox("출력")
        ol = QFormLayout(og)
        r1 = QHBoxLayout()
        self.fmt = QComboBox()
        for f in encoders.FORMATS:
            self.fmt.addItem(f.name, f.key)
        self.quality = QComboBox()
        self.fmt_note = QLabel()
        self.fmt_note.setStyleSheet("color:#777")
        self.fmt_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        r1.addWidget(self.fmt); r1.addWidget(self.quality); r1.addWidget(self.fmt_note, 1)
        ol.addRow("포맷", r1)
        r2 = QHBoxLayout()
        self.outdir = QLineEdit(self.s.output_dir)
        self.outdir.editingFinished.connect(self._outdir_changed)
        b = QPushButton("📁 폴더 선택…")
        b.setToolTip("저장 폴더를 고릅니다. 한 번 정하면 다음 실행 때도 이 폴더가 기본값입니다.")
        b.clicked.connect(self._browse_outdir)
        b_open = QPushButton("열기")
        b_open.clicked.connect(lambda: (os.makedirs(self.s.output_dir, exist_ok=True),
                                        QDesktopServices.openUrl(QUrl.fromLocalFile(self.s.output_dir))))
        r2.addWidget(self.outdir, 1); r2.addWidget(b); r2.addWidget(b_open)
        ol.addRow("저장 폴더", r2)
        self.album_folder = QCheckBox("앨범 이름으로 하위 폴더 만들기  (저장 폴더\\앨범 이름\\파일)")
        self.album_folder.setChecked(self.s.album_folder)
        self.album_folder.toggled.connect(self._album_folder_toggled)
        ol.addRow("", self.album_folder)
        r3 = QHBoxLayout()
        self.tpl = QComboBox(editable=True)
        self.tpl.addItems(naming.PRESETS)
        self.tpl.setEditText(self.s.template)
        self.tpl.editTextChanged.connect(self._tpl_changed)
        b_reset = QPushButton("기본값")
        b_reset.setToolTip("파일 이름 규칙을 처음 기본값으로 되돌립니다")
        b_reset.clicked.connect(lambda: self.tpl.setEditText(Settings.template))
        tb_help = QPushButton("필드 목록")
        tb_help.clicked.connect(self._tpl_help)
        r3.addWidget(self.tpl, 1); r3.addWidget(b_reset); r3.addWidget(tb_help)
        ol.addRow("파일 이름 규칙", r3)
        self.preview = QLabel()
        self.preview.setStyleSheet("color:#1565c0")
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long paths never widen the window
        ol.addRow("미리보기", self.preview)
        root.addWidget(og)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFormat("%p%")
        self.status = QLabel("준비")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.rip_btn = QPushButton("●  리핑 시작")
        self.rip_btn.clicked.connect(self.start_or_cancel)
        bottom.addWidget(self.status, 2)
        bottom.addWidget(self.progress, 3)
        bottom.addWidget(self.rip_btn)
        root.addLayout(bottom)
        self.logbox = QPlainTextEdit(readOnly=True, maximumBlockCount=2000)
        root.addWidget(self.logbox)
        self._apply_scale()
        split.setSizes([px(300), px(1000)])

        idx = self.fmt.findData(self.s.format)
        self.fmt.setCurrentIndex(max(idx, 0))
        self._fmt_changed()

    # ================================================================ UI size
    def _apply_scale(self):
        """(Re)apply every size that depends on the font size."""
        self.drive_combo.setMinimumWidth(px(200))
        self.ui_slider.setFixedWidth(px(110))
        self.left_scroll.setMinimumWidth(px(260))
        self.left_scroll.setMaximumWidth(px(400))
        hh = self.table.horizontalHeader()
        hh.setMinimumSectionSize(px(28))
        widths = {**DEFAULT_COL_WIDTHS, **self.s.column_widths}
        self._applying_widths = True
        for k, _ in COLS:
            if k in widths:
                self.table.setColumnWidth(COL[k], max(px(20), int(widths[k] * ui_scale())))
        self._applying_widths = False
        self.table.verticalHeader().setDefaultSectionSize(QFontMetrics(QApplication.font()).height() + px(8))
        self.right.setMinimumHeight(px(110))
        f = QApplication.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.rip_btn.setFont(f)
        self.rip_btn.setMinimumHeight(px(32))
        self.rip_btn.setMinimumWidth(px(170))
        self.logbox.setFixedHeight(QFontMetrics(QApplication.font()).lineSpacing() * 3 + 14)   # always 3 lines

    def _column_resized(self, index, _old, new):
        if getattr(self, "_applying_widths", False):
            return
        key = COLS[index][0]
        if key == COLS[-1][0]:          # last column just fills the remaining space
            return
        self.s.column_widths = {**self.s.column_widths, key: round(new / ui_scale())}
        self._save_soon()

    def _ui_slider_moved(self, v, apply=True):
        self.ui_size_lbl.setText(f" {v} ")
        if apply and not self.ui_slider.isSliderDown():   # keyboard / wheel / click on the track
            self._font_timer.start()

    def set_ui_size(self, pt: int):
        """Change the whole UI size: new font, then rebuild the window at that size.

        Rebuilding (instead of only changing the font) guarantees that every widget follows:
        Qt does not re-propagate fonts to widgets with style sheets and caches combo-box sizes."""
        if self._busy():
            self.ui_slider.setValue(self.s.font_pt)
            return self.log("리핑 중에는 UI 크기를 바꿀 수 없습니다.")
        f = QApplication.font()
        if f.pointSize() == pt:
            return
        f.setPointSize(pt)
        QApplication.setFont(f)
        QApplication.sendPostedEvents()        # let the window pick up the new font before rebuilding
        self.s.font_pt = pt
        self._save_soon()
        self._rebuild_ui()

    def _rebuild_ui(self):
        keep = {"log": self.logbox.toPlainText(), "status": self.status.text(),
                "disc": self.disc_info.text(), "progress": self.progress.value(),
                "track_status": {self.album.tracks[r].number: self.table.item(r, COL["status"]).text()
                                 for r in range(self.table.rowCount())
                                 if r < len(self.album.tracks) and self.table.item(r, COL["status"])}}
        self.removeToolBar(self._toolbar)
        self._toolbar.setParent(None)          # detach now (its shortcuts die with it), free later
        self._toolbar.deleteLater()
        for m in self.menuBar().findChildren(QMenu):
            m.setParent(None)
            m.deleteLater()
        self.menuBar().clear()
        self._build_ui()                       # replaces the central widget
        self._fill_drive_combo()
        self.refresh_all()
        self.set_cover(self.cover)
        self.logbox.setPlainText(keep["log"])
        self.logbox.moveCursor(QTextCursor.MoveOperation.End)
        self.status.setText(keep["status"])
        self.disc_info.setText(keep["disc"])
        self.progress.setValue(keep["progress"])
        for r, t in enumerate(self.album.tracks):
            if t.number in keep["track_status"]:
                self.table.item(r, COL["status"]).setText(keep["track_status"][t.number])

    # ================================================================ genre / folders
    def _quick_title(self, title):
        if not self.album.tracks:
            return
        if not self.table.selectedIndexes() and self.table.currentRow() < 0:
            self.table.selectRow(0)
        rows = apply_title_to_table(self.table, COL["title"], title)
        self.table.setFocus()
        if not rows:
            self.log("제목을 넣을 트랙을 먼저 선택하세요.")

    def _title_presets_edited(self, presets):
        self.s.title_presets = presets
        self._save_soon()

    def _set_label(self, name):
        self.album_edits["label"].setText(name)
        self._album_edited("label", name)

    def _genre_chosen(self, name):
        self.album.genre = name
        self.s.genre = name          # becomes the default for the next CD
        self._save_soon()
        self._update_preview()

    def _sync_genre_buttons(self):
        g = self.album.genre
        self.genre_group.setExclusive(False)
        for name, b in self.genre_btns.items():
            b.setChecked(name == g)
        self.genre_group.setExclusive(True)

    def _album_folder_toggled(self, on):
        self.s.album_folder = on
        self._save_soon()
        self._update_preview()

    # ================================================================ drives
    def refresh_drives(self):
        self.drives = list_drives()
        self._fill_drive_combo()

    def _fill_drive_combo(self):
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        for d in self.drives:
            self.drive_combo.addItem(str(d), d.letter)
        if not self.drives:
            self.drive_combo.addItem("(CD 드라이브 없음)")
        i = self.drive_combo.findData(self.s.last_drive)
        self.drive_combo.setCurrentIndex(max(i, 0))
        self.drive_combo.blockSignals(False)

    def drive(self) -> BaseDrive | None:
        i = self.drive_combo.currentIndex()
        return self.drives[i] if 0 <= i < len(self.drives) else None

    def _busy(self) -> bool:
        return bool(self.rip_thread and self.rip_thread.isRunning())

    def _poll_media(self):
        d = self.drive()
        if not d or self._busy():
            return
        present = d.media_present()
        if self._media is not None and present != self._media:
            if present:
                self.log("디스크가 삽입되었습니다.")
                QTimer.singleShot(1500, self.load_disc)
            else:
                self.log("디스크가 제거되었습니다.")
                self._disc_removed()
        self._media = present

    def eject(self):
        d = self.drive()
        if d and not self._busy():
            try:
                d.eject()
            except DriveError as e:
                self.log(str(e))
                return
            self._disc_removed()

    def _disc_removed(self):
        """Remember what was entered for this disc, then start with an empty sheet so anything typed /
        recognised before the next CD goes in is kept for that CD."""
        if self.toc is None:
            return
        self._cache_save()
        self.toc = None
        self.toc_was_empty = True
        self.album = AlbumMeta(genre=self.s.genre)
        self.set_cover(None)
        self.refresh_all()
        self.disc_info.setText("디스크 없음 — CD를 넣기 전에 웹캠 인식·직접 입력을 해 두면 CD를 넣을 때 그대로 적용됩니다.")
        self.status.setText("디스크 없음")

    def _has_user_data(self) -> bool:
        a = self.album
        if self.cover or any(getattr(a, k) for k, _ in ALBUM_FIELDS if k not in ("disc", "totaldiscs", "genre")):
            return True
        return any(t.title or t.artist or t.composer for t in a.tracks)

    def load_disc(self):
        d = self.drive()
        if not d or self._busy():
            return
        self.s.last_drive = d.letter
        self.status.setText("디스크 읽는 중…")

        def read():
            toc = d.read_toc()
            return toc, d.read_cdtext()

        run_async(read, on_done=self._disc_loaded, on_fail=self._disc_failed)

    @property
    def toc_was_empty(self) -> bool:
        return getattr(self, "_toc_was_empty", True)

    @toc_was_empty.setter
    def toc_was_empty(self, v):
        self._toc_was_empty = v

    def _disc_failed(self, e):
        self.status.setText("디스크 없음")
        self.disc_info.setText("오디오 CD를 넣어주세요.")
        self.log(e)

    def _disc_loaded(self, res):
        toc, cdtext = res
        self.toc = toc
        discid = toc.musicbrainz_discid()
        audio = toc.audio_tracks
        self.disc_info.setText(f"{len(audio)}트랙 · {fmt_duration(toc.audio_leadout)}\n"
                               f"MusicBrainz Disc ID: {discid}")
        self.status.setText(f"오디오 CD: {len(audio)}트랙")
        # info entered before the CD was inserted (webcam / typing / paste) is kept
        pending = (self.album, self.cover) if self.toc_was_empty and self._has_user_data() else None
        self.toc_was_empty = False
        self.album = AlbumMeta(mb_discid=discid, genre=self.s.genre,
                               tracks=[TrackMeta(t.number, length=t.length) for t in audio])
        self.set_cover(None)
        cached = self._cache_load(discid)
        if cached:
            self.log("이전에 저장한 이 디스크의 정보를 불러왔습니다.")
            self.apply_album(cached[0], replace=True)
            if cached[1]:
                self.set_cover(cached[1])
        elif cdtext:
            self.log("CD-Text 정보를 찾았습니다.")
            self._apply_cdtext(cdtext)
        if pending:
            p_album, p_cover = pending
            if len(p_album.tracks) not in (0, len(audio)):
                self.log(f"⚠ 미리 입력한 트랙 수({len(p_album.tracks)})와 CD 트랙 수({len(audio)})가 다릅니다. "
                         "번호대로 적용했습니다.")
            self.apply_album(p_album, keep_ids=True)
            if p_cover:
                self.set_cover(p_cover)
            self.log("CD를 넣기 전에 입력한 정보를 이 CD에 적용했습니다. 웹 검색 결과는 빈 칸만 채웁니다.")
        self._fill_empty_only = bool(pending)
        self.refresh_all()
        if self.s.auto_lookup and not cached:
            self.lookup_disc()

    def _apply_cdtext(self, ct):
        self.album.album = ct.get("album", self.album.album)
        self.album.albumartist = ct.get("albumartist", self.album.albumartist)
        for t in self.album.tracks:
            d = ct.get("tracks", {}).get(t.number, {})
            t.title = d.get("title", t.title)
            t.artist = d.get("artist", t.artist)
        self.album.source = "CD-Text"

    def lookup_disc(self, manual=False):
        if not self.toc:
            return
        self.status.setText("MusicBrainz에서 디스크 ID로 검색 중…")
        run_async(web.mb_lookup_disc, self.toc, on_done=lambda r: self._lookup_done(r, manual),
                  on_fail=lambda e: (self.status.setText("웹 검색 실패"), self.log("MusicBrainz 조회 실패: " + e)))

    def _lookup_done(self, results: list[AlbumMeta], manual=False):
        exact = [a for a in results if a.mb_discid]
        if len(exact) == 1 or (len(results) == 1):
            a = (exact or results)[0]
            self.log(f"MusicBrainz에서 찾음: {a.albumartist} – {a.album}")
            self.apply_album(a, fill_empty=self._fill_empty_only)
            self.status.setText("웹에서 앨범 정보를 찾았습니다. 확인 후 리핑하세요.")
        elif results:
            self.status.setText(f"{len(results)}개 후보 — 선택하세요")
            dlg = SearchDialog(self, "", "", self.toc, self.s.store_country, preset=results)
            if dlg.exec() and dlg.selected:
                self.apply_album(dlg.selected, fill_empty=self._fill_empty_only)
        else:
            self.status.setText("웹에서 이 디스크를 찾지 못했습니다")
            self.log("디스크 ID로 찾지 못했습니다. '웹에서 정보 찾기'로 앨범명 검색, 웹캠 인식, 또는 직접 입력하세요.")
            if manual:
                self._open_search()

    # ================================================================ metadata
    def apply_album(self, a: AlbumMeta, replace: bool = False, fill_empty: bool = False,
                    keep_ids: bool = False):
        """replace: overwrite everything · fill_empty: only fill blank fields (keeps what the user entered)
        · default: non-empty values from `a` win."""
        cur = self.album

        def take(old, new):
            if fill_empty:
                return bool(new) and not old
            return bool(new) or replace

        for key, _ in ALBUM_FIELDS:
            val = getattr(a, key)
            if key == "genre":
                if val in GENRES:            # web genres like "K-Pop" are ignored: genre = Music / Audiobook
                    cur.genre = val if (replace or not fill_empty) else (cur.genre or val)
                continue
            if take(getattr(cur, key), val) and not (fill_empty and key in ("disc", "totaldiscs")):
                setattr(cur, key, val)
        if not keep_ids:
            for k in ("mb_release_id", "mb_releasegroup_id", "source"):
                setattr(cur, k, getattr(a, k))
        if keep_ids or fill_empty:       # merging: a "yes" from either side wins
            cur.compilation = cur.compilation or a.compilation
        else:
            cur.compilation = a.compilation
        by_no = {t.number: t for t in a.tracks}
        if len(a.tracks) != len(cur.tracks) and cur.tracks and a.tracks:
            self.log(f"⚠ 트랙 수가 다릅니다 (CD {len(cur.tracks)} / 검색 결과 {len(a.tracks)}). 번호 순서대로 적용했습니다.")
        for i, t in enumerate(cur.tracks):
            src = by_no.get(t.number) or (a.tracks[i] if i < len(a.tracks) else None)
            if not src:
                continue
            for f in ("title", "artist", "composer", "lyricist", "isrc", "comment", "lyrics",
                      "mb_recording_id"):
                v = getattr(src, f)
                if take(getattr(t, f), v):
                    setattr(t, f, v)
            if not t.length:
                t.length = src.length
            if replace:
                t.rip, t.rip_user = src.rip, src.rip_user
        if not cur.tracks:   # no disc: take tracks as they are
            cur.tracks = [TrackMeta(**{**t.__dict__}) for t in a.tracks]
        self.refresh_all()
        if a.cover_url and not self.cover:
            run_async(self._fetch_best_cover, a, on_done=lambda d: d and not self.cover and self.set_cover(d),
                      on_fail=lambda e: None)

    @staticmethod
    def _fetch_best_cover(a: AlbumMeta) -> bytes | None:
        urls = []
        if a.source == "MusicBrainz" and a.mb_release_id:
            urls.append(f"https://coverartarchive.org/release/{a.mb_release_id}/front-1200")
        if a.source == "iTunes":
            urls.append(re.sub(r"\d+x\d+bb", "1400x1400bb", a.cover_url))
        urls.append(a.cover_url)
        for u in urls:
            try:
                d = web.download(u)
                if image_size(d)[0]:
                    return d
            except Exception:  # noqa: BLE001
                continue
        return None

    def search_metadata(self):
        if not (self.album.albumartist or self.album.album) and self.toc:
            # nothing typed yet: try the disc ID first, the dialog opens if there are candidates
            self.lookup_disc(manual=True)
            return
        self._open_search()

    def _open_search(self):
        dlg = SearchDialog(self, self.album.albumartist, self.album.album, self.toc, self.s.store_country,
                           genre=self.album.genre, sources=self.s.search_sources or None)
        ok = dlg.exec()
        chosen = [n for n, cb in dlg.src.items() if cb.isChecked()]
        if chosen != self.s.search_sources:
            self.s.search_sources = chosen
            self._save_soon()
        if ok and dlg.selected:
            replace = QMessageBox.question(
                self, "적용 방식", "기존에 입력한 값을 모두 덮어쓸까요?\n\n예: 검색 결과로 전부 교체\n"
                                   "아니요: 검색 결과에 있는 값만 갱신 (빈 값은 기존 유지)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes
            self.apply_album(dlg.selected, replace=replace)

    def ocr_tracks(self):
        n = len(self.album.tracks)      # 0 = no CD yet → track count follows the recognised list
        dlg = OcrDialog(self, n, self.s.camera_index, self.s.ocr_engine, self.s.ocr_lang,
                        presets=self.s.title_presets, on_presets_edited=self._title_presets_edited)
        ok = dlg.exec()
        self.title_bar.set_presets(self.s.title_presets)      # may have been edited in the dialog
        if ok:
            self.s.camera_index = dlg.cam_combo.currentData()
            if not self.album.tracks:
                self._ensure_tracks(dlg.n_tracks)
            for t in self.album.tracks:
                d = dlg.result.get(t.number)
                if d:
                    t.title = d["title"] or t.title
                    if d["artist"]:
                        t.artist = d["artist"]
            self.refresh_tracks()
            self.log(f"웹캠 인식 결과 {len(dlg.result)}개 트랙에 적용했습니다.")

    def _ensure_tracks(self, n: int):
        """No CD inserted yet: create placeholder tracks (merged by number when the CD goes in)."""
        self.album.tracks = [TrackMeta(i + 1) for i in range(n)]
        if self.auto_fill.isChecked():
            for t in self.album.tracks:
                t.artist, t.comment = self.album.albumartist, self.album.comment
        self.refresh_tracks()

    # ================================================================ cover
    def set_cover(self, data: bytes | None):
        self.cover = data
        if data:
            self.cover_lbl.set_image(data)
            w, h = image_size(data)
            self.cover_info.setText(f"{w}×{h}  ·  {len(data) // 1024} KB")
        else:
            self.cover_lbl.set_image(None, "커버 없음\n끌어다 놓기")
            self.cover_info.setText("")

    def find_cover(self):
        dlg = CoverDialog(self, self.album.albumartist, self.album.album, self.album.mb_release_id,
                          self.s.store_country)
        if dlg.exec() and dlg.result:
            self.set_cover(dlg.result)

    def cover_from_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "커버 이미지", "", "이미지 (*.jpg *.jpeg *.png *.webp *.bmp)")
        if p:
            self._cover_bytes_any(open(p, "rb").read())

    def cover_from_clipboard(self):
        from .cover_dialog import image_bytes_from_qimage
        img = QGuiApplication.clipboard().image()
        if not img.isNull():
            self.set_cover(image_bytes_from_qimage(img))
        else:
            txt = QGuiApplication.clipboard().text().strip()
            if txt.startswith("http"):
                run_async(web.download, txt, on_done=self._cover_bytes_any, on_fail=self.log)

    def _cover_bytes_any(self, data):
        from PySide6.QtGui import QImage
        from .cover_dialog import image_bytes_from_qimage
        if data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG":
            return self.set_cover(data)
        img = QImage.fromData(data)
        if not img.isNull():
            self.set_cover(image_bytes_from_qimage(img))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        from .cover_dialog import image_bytes_from_qimage
        md = e.mimeData()
        if md.hasImage():
            return self.set_cover(image_bytes_from_qimage(md.imageData()))
        for u in md.urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if p.lower().endswith(".json"):
                    return self._load_json_path(p)
                return self._cover_bytes_any(open(p, "rb").read())
            return run_async(web.download, u.toString(), on_done=self._cover_bytes_any, on_fail=self.log)

    # ================================================================ form/table sync
    def refresh_all(self):
        for key, e in self.album_edits.items():
            e.setText(str(getattr(self.album, key) or ""))
        self.compilation.blockSignals(True)
        self.compilation.setChecked(self.album.compilation)
        self.compilation.blockSignals(False)
        self._sync_genre_buttons()
        self.refresh_tracks()

    def _auto_rip(self):
        """Tracks without a title are not ripped by default; a manual tick/untick always wins."""
        for t in self.album.tracks:
            if not t.rip_user:
                t.rip = bool(t.title.strip())

    def refresh_tracks(self):
        self._auto_rip()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.album.tracks))
        for r, t in enumerate(self.album.tracks):
            for key, _ in COLS:
                if key == "rip":
                    it = QTableWidgetItem()
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if t.rip else Qt.Unchecked)
                elif key == "number":
                    it = QTableWidgetItem(f"{t.number:02d}")
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setToolTip("더블클릭: 상세 정보 (가사, 코멘트)")
                elif key == "length":
                    it = QTableWidgetItem(fmt_duration(t.length) if t.length else "")
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                elif key == "status":
                    old = self.table.item(r, COL["status"])
                    it = QTableWidgetItem(old.text() if old else "")
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                else:
                    it = QTableWidgetItem(getattr(t, key))
                    if key == "artist" and not t.artist and self.album.albumartist:
                        it.setToolTip(f"비어 있으면 앨범 아티스트 '{self.album.albumartist}' 사용")
                self.table.setItem(r, COL[key], it)
        self.table.blockSignals(False)
        self.restore_btn.setEnabled(bool(self._deleted_numbers()))
        self._update_preview()

    # album field → track field copied when "자동 반영" is on
    AUTO_FILL = {"albumartist": "artist", "comment": "comment"}

    def _album_edited(self, key, value):
        old = getattr(self.album, key)
        setattr(self.album, key, value.strip())
        tf = self.AUTO_FILL.get(key)
        if tf and self.auto_fill.isChecked():
            # overwrite only tracks that were empty or still had the previous album value
            for t in self.album.tracks:
                if getattr(t, tf) in ("", old):
                    setattr(t, tf, value.strip())
            self.refresh_tracks()
        self._update_preview()

    def _auto_fill_toggled(self, on):
        self.s.auto_fill_tracks = on
        self._save_soon()
        if on:   # fill empty track fields right away
            for af, tf in self.AUTO_FILL.items():
                val = getattr(self.album, af)
                for t in self.album.tracks:
                    if val and not getattr(t, tf):
                        setattr(t, tf, val)
            self.refresh_tracks()

    def _compilation_toggled(self, v):
        self.album.compilation = v
        self._update_preview()

    def _track_edited(self, item: QTableWidgetItem):
        r, c = item.row(), item.column()
        if r >= len(self.album.tracks):
            return
        t = self.album.tracks[r]
        key = COLS[c][0]
        if key == "rip":
            t.rip = item.checkState() == Qt.Checked
            t.rip_user = True
        elif key in EDITABLE:
            setattr(t, key, item.text().strip())
            if key == "title" and not t.rip_user:   # typing a title ticks the track
                self._auto_rip()
                self.table.blockSignals(True)
                self.table.item(r, COL["rip"]).setCheckState(Qt.Checked if t.rip else Qt.Unchecked)
                self.table.blockSignals(False)
        self._update_preview()

    def _cell_dbl(self, r, c):
        if COLS[c][0] == "number" and r < len(self.album.tracks):
            if TrackDetailDialog(self, self.album.tracks[r]).exec():
                self.refresh_tracks()

    def _set_all_rip(self, v):
        for t in self.album.tracks:
            t.rip, t.rip_user = v, True
        self.refresh_tracks()

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _table_menu(self, pos):
        m = QMenu(self)
        m.addAction("선택한 트랙에 값 일괄 입력…", self.batch_edit)
        m.addAction("여기부터 클립보드 트랙 목록 붙여넣기", self.paste_titles)
        m.addAction("상세 정보 (가사/코멘트)…", lambda: self._cell_dbl(self.table.currentRow(), COL["number"]))
        m.addSeparator()
        m.addAction("선택 트랙만 리핑", lambda: self._rip_only_selected())
        m.addAction("선택 트랙 삭제 (Delete)", self.delete_tracks)
        if self._deleted_numbers():
            m.addAction("삭제한 트랙 복원", self.restore_tracks)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def delete_tracks(self):
        if self._busy():
            return
        rows = set(self.selected_rows())
        if not rows:
            return
        nums = [self.album.tracks[r].number for r in sorted(rows)]
        self.album.tracks = [t for i, t in enumerate(self.album.tracks) if i not in rows]
        self.refresh_tracks()
        self.log(f"트랙 {', '.join(f'{n:02d}' for n in nums)} 삭제 (‘삭제한 트랙 복원’으로 되돌릴 수 있습니다)")

    def _deleted_numbers(self) -> list[int]:
        if not self.toc:
            return []
        have = {t.number for t in self.album.tracks}
        return [t.number for t in self.toc.audio_tracks if t.number not in have]

    def restore_tracks(self):
        if self._busy():
            return
        missing = self._deleted_numbers()
        for n in missing:
            t = TrackMeta(n, length=self.toc.track(n).length)
            if self.auto_fill.isChecked():
                t.artist, t.comment = self.album.albumartist, self.album.comment
            self.album.tracks.append(t)
        self.album.tracks.sort(key=lambda t: t.number)
        self.refresh_tracks()
        if missing:
            self.log(f"트랙 {', '.join(f'{n:02d}' for n in missing)} 복원")

    def _header_menu(self, pos):
        m = QMenu(self)
        m.addSection("표시할 열")
        for key, label in COLS:
            if key in ("rip", "number", "title"):
                continue
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(COL[key]))
            a.toggled.connect(lambda on, k=key: self._toggle_column(k, on))
        m.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, key, show):
        self.table.setColumnHidden(COL[key], not show)
        self.s.hidden_columns = [k for k, _ in COLS if self.table.isColumnHidden(COL[k])]
        self._save_soon()

    def _rip_only_selected(self):
        rows = set(self.selected_rows())
        for i, t in enumerate(self.album.tracks):
            t.rip, t.rip_user = i in rows, True
        self.refresh_tracks()

    # ================================================================ tools
    def paste_titles(self):
        text = QGuiApplication.clipboard().text()
        if not text.strip():
            return QMessageBox.information(self, "붙여넣기", "클립보드에 텍스트가 없습니다.\n"
                                                             "웹페이지(멜론, 벅스 등)의 트랙 목록을 복사한 뒤 사용하세요.")
        parsed = ocr.parse_tracklist(text)
        if not self.album.tracks:
            self._ensure_tracks(ocr.guess_track_count(parsed))
        n = len(self.album.tracks)
        numbered = {k: v for k, v in parsed if k is not None and 1 <= k <= n}
        count = 0
        if len(numbered) >= max(1, n // 2):
            for t in self.album.tracks:
                if t.number in numbered:
                    t.title = numbered[t.number]
                    count += 1
        else:
            start = (self.selected_rows() or [0])[0]
            for i, (_, title) in enumerate(parsed):
                if start + i >= n:
                    break
                self.album.tracks[start + i].title = title
                count += 1
        self.refresh_tracks()
        self.log(f"클립보드에서 {count}개 제목을 붙여넣었습니다.")

    def batch_edit(self):
        rows = self.selected_rows() or list(range(len(self.album.tracks)))
        fields = [("artist", "아티스트"), ("composer", "작곡"), ("lyricist", "작사"),
                  ("comment", "코멘트")]
        name, ok = QInputDialog.getItem(self, "일괄 입력", f"{len(rows)}개 트랙에 입력할 항목:",
                                        [f[1] for f in fields], 0, False)
        if not ok:
            return
        key = next(k for k, l in fields if l == name)
        val, ok = QInputDialog.getText(self, "일괄 입력", f"{name} 값:")
        if ok:
            for r in rows:
                setattr(self.album.tracks[r], key, val.strip())
            self.refresh_tracks()

    def copy_albumartist(self):
        for t in self.album.tracks:
            t.artist = self.album.albumartist
        self.refresh_tracks()

    def strip_numbers(self):
        for t in self.album.tracks:
            t.title = re.sub(r"^\s*\d{1,2}\s*[\.\)\-:]\s*", "", t.title)
        self.refresh_tracks()

    def move_feat(self):
        for t in self.album.tracks:
            m = re.search(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]", t.title, re.I)
            if m:
                base = t.artist or self.album.albumartist
                t.artist = f"{base} (feat. {m.group(1).strip()})"
                t.title = (t.title[:m.start()] + t.title[m.end():]).strip()
        self.refresh_tracks()

    def title_case(self):
        small = {"a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "from", "by", "of", "in"}

        def tc(text):
            out = []
            for i, w in enumerate(text.split(" ")):
                if not w.isascii() or (w.isupper() and len(w) > 1):   # keep Korean & acronyms
                    out.append(w)
                elif i and w.lower() in small:
                    out.append(w.lower())
                else:
                    out.append(w[:1].upper() + w[1:].lower())
            return " ".join(out)

        for t in self.album.tracks:
            t.title = tc(t.title)
        self.album.album = tc(self.album.album)
        self.refresh_all()

    def clear_tracks(self):
        for t in self.album.tracks:
            for f in ("title", "artist", "composer", "lyricist", "genre", "isrc", "comment", "lyrics"):
                setattr(t, f, "")
        self.refresh_tracks()

    def submit_mb(self):
        if self.toc:
            url = f"https://musicbrainz.org/cdtoc/attach?id={self.toc.musicbrainz_discid()}" \
                  f"&tracks={len(self.toc.audio_tracks)}&toc={self.toc.musicbrainz_toc().replace('+', '%20')}"
            QDesktopServices.openUrl(QUrl(url))

    # ================================================================ output settings
    def _fmt_changed(self):
        key = self.fmt.currentData()
        fi = encoders.FORMAT_BY_KEY[key]
        self.quality.blockSignals(True)
        self.quality.clear()
        for q, label in fi.qualities:
            self.quality.addItem(label, q)
        qi = self.quality.findData(self.s.quality if key == self.s.format else fi.default)
        self.quality.setCurrentIndex(max(qi, 0))
        self.quality.blockSignals(False)
        note = fi.note
        if fi.needs_ffmpeg and not encoders.find_ffmpeg(self.s.ffmpeg_path):
            note = "⚠ ffmpeg.exe가 필요합니다 (exe 옆에 두거나 설정에서 지정). " + note
        self.fmt_note.setText(note)
        self.s.format = key
        self._save_soon()
        self.s.quality = self.quality.currentData()
        self._update_preview()

    def _quality_changed(self):
        self.s.quality = self.quality.currentData()
        self._save_soon()

    def _save_soon(self):
        """Persist settings shortly after a change (debounced) so they become the new defaults."""
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.s.save)
        self._save_timer.start()

    def _outdir_changed(self):
        path = self.outdir.text().strip()
        if not path:
            self.outdir.setText(self.s.output_dir)
            return
        self.s.output_dir = os.path.normpath(path)
        self.outdir.setText(self.s.output_dir)
        self._save_soon()
        self._update_preview()

    def _browse_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더", self.s.output_dir)
        if d:
            self.outdir.setText(d)
            self._outdir_changed()

    def _tpl_changed(self, text):
        if not text.strip():
            return
        self.s.template = text.strip()
        self._save_soon()
        self._update_preview()

    def _tpl_help(self):
        rows = "".join(f"<tr><td><code>{k}</code></td><td>{v}</td></tr>" for k, v in naming.FIELDS_HELP)
        QMessageBox.information(self, "파일 이름 규칙", f"""<p><b>사용 가능한 필드</b></p><table>{rows}</table>
            <p><code>/</code> 는 폴더 구분입니다.<br>
            <code>[ ]</code> 로 감싼 부분은 안의 값이 비어 있으면 통째로 생략됩니다.<br>
            예: <code>{{albumartist}}/[{{year}} - ]{{album}}/{{track:02}}. {{title}}</code></p>
            <p>컴필레이션 앨범은 설정 &gt; 태그/파일의 별도 규칙을 사용합니다.</p>""")

    def _update_preview(self):
        if not self.album.tracks:
            self.preview.setText("")
            return
        t = next((t for t in self.album.tracks if t.rip), self.album.tracks[0])
        self.preview.setText(output_path(self.s, self.album, t))

    # ================================================================ rip
    def start_or_cancel(self):
        if self._busy():
            self.rip_thread.job.cancel()
            self.rip_btn.setEnabled(False)
            return
        d = self.drive()
        if not d or not self.toc:
            return QMessageBox.warning(self, "리핑", "오디오 CD가 없습니다.")
        if not any(t.rip for t in self.album.tracks):
            return QMessageBox.warning(self, "리핑", "선택된 트랙이 없습니다.\n\n"
                                       "제목이 있는 트랙만 자동으로 체크됩니다. 제목을 입력하거나 "
                                       "(웹 검색·웹캠 인식·붙여넣기) 리핑할 트랙을 직접 체크하세요.")
        missing = [t.number for t in self.album.tracks if t.rip and not t.title]
        if not self.album.album or missing:
            msg = []
            if not self.album.album:
                msg.append("앨범 이름이 비어 있습니다.")
            if missing:
                msg.append(f"제목 없는 트랙: {', '.join(map(str, missing))} (\"Track NN\"으로 저장)")
            if QMessageBox.question(self, "확인", "\n".join(msg) + "\n\n그래도 계속할까요?") != QMessageBox.Yes:
                return
        fi = encoders.FORMAT_BY_KEY[self.s.format]
        if fi.needs_ffmpeg and not encoders.find_ffmpeg(self.s.ffmpeg_path):
            return QMessageBox.warning(self, "ffmpeg 필요", f"{fi.name} 포맷은 ffmpeg.exe가 필요합니다.\n"
                                       "https://www.gyan.dev/ffmpeg/builds/ 에서 받아 프로그램 폴더에 넣거나 설정에서 지정하세요.")
        # warn about overwriting
        job_probe = RipJob(d, self.toc, self.album, None, self.s)
        existing = [t.number for t in self.album.tracks if t.rip and os.path.exists(job_probe.output_path(t))]
        if existing and QMessageBox.question(self, "덮어쓰기", f"이미 있는 파일 {len(existing)}개를 덮어쓸까요?") \
                != QMessageBox.Yes:
            return
        self.s.save()
        self._cache_save()
        for r in range(self.table.rowCount()):
            self.table.item(r, COL["status"]).setText("대기" if self.album.tracks[r].rip else "")
        self.rip_thread = RipThread((d, self.toc, copy.deepcopy(self.album), self.cover, copy.copy(self.s)))
        self.rip_thread.progress.connect(self._on_progress)
        self.rip_thread.track_done.connect(self._on_track_done)
        self.rip_thread.log.connect(self.log)
        self.rip_thread.report.connect(self._on_report)
        self.rip_btn.setText("■  취소")
        self._set_editing(False)
        self.rip_thread.start()

    def _set_editing(self, on: bool):
        for w in (self.drive_combo, self.fmt, self.quality, self.outdir, self.tpl):
            w.setEnabled(on)

    def _on_progress(self, track, frac, text):
        self.progress.setValue(int(frac * 100))
        if track:
            self.status.setText(f"트랙 {track:02d} {text}…")
            row = next((i for i, t in enumerate(self.album.tracks) if t.number == track), None)
            if row is not None:
                self.table.item(row, COL["status"]).setText("읽는 중…")

    def _on_track_done(self, rep: TrackReport):
        row = next((i for i, t in enumerate(self.album.tracks) if t.number == rep.number), None)
        if row is None:
            return
        txt = ("❌ " + rep.error) if rep.error else (
            ("✅ " if rep.ar_status in ("v1", "v2") else ("⚠ " if rep.unrecovered or rep.ar_status == "mismatch"
                                                          else "✔ ")) + ar_text(rep))
        it = self.table.item(row, COL["status"])
        it.setText(txt)
        it.setToolTip(rep.path or rep.error)

    def _on_report(self, rep: JobReport):
        self.rip_btn.setText("●  리핑 시작")
        self.rip_btn.setEnabled(True)
        self._set_editing(True)
        ok = [r for r in rep.tracks if r.path]
        bad = [r for r in rep.tracks if r.error or r.unrecovered]
        self.status.setText(f"완료: {len(ok)}개 트랙" + (f" · 문제 {len(bad)}개" if bad else "") +
                            (" (취소됨)" if rep.cancelled else ""))
        self.progress.setValue(100)
        if ok and self.s.eject_when_done and not rep.cancelled:
            self.eject()
        if rep.folder:
            box = QMessageBox(self)
            box.setWindowTitle("리핑 완료")
            box.setText(f"{len(ok)}개 트랙을 저장했습니다.\n{rep.folder}" +
                        (f"\n\n⚠ 문제 트랙: {', '.join(str(r.number) for r in bad)}" if bad else ""))
            open_btn = box.addButton("폴더 열기", QMessageBox.AcceptRole)
            box.addButton("닫기", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(rep.folder))

    def log(self, msg: str):
        self.logbox.appendPlainText(msg)

    # ================================================================ settings / persistence
    def open_settings(self):
        if SettingsDialog(self, self.s).exec():
            self._fmt_changed()

    def _cache_path(self, discid):
        d = os.path.join(config_dir(), "discs")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, discid.replace(".", "_"))

    def _cache_save(self):
        if not self.album.mb_discid:
            return
        p = self._cache_path(self.album.mb_discid)
        with open(p + ".json", "w", encoding="utf-8") as f:
            json.dump(self.album.to_dict(), f, ensure_ascii=False, indent=1)
        if self.cover:
            with open(p + ".cover", "wb") as f:
                f.write(self.cover)

    def _cache_load(self, discid):
        p = self._cache_path(discid)
        try:
            with open(p + ".json", encoding="utf-8") as f:
                a = AlbumMeta.from_dict(json.load(f))
        except (OSError, ValueError):
            return None
        cover = open(p + ".cover", "rb").read() if os.path.exists(p + ".cover") else None
        return a, cover

    def save_json(self):
        p, _ = QFileDialog.getSaveFileName(self, "메타데이터 저장", f"{self.album.album or 'album'}.json", "JSON (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.album.to_dict(), f, ensure_ascii=False, indent=1)

    def load_json(self):
        p, _ = QFileDialog.getOpenFileName(self, "메타데이터 불러오기", "", "JSON (*.json)")
        if p:
            self._load_json_path(p)

    def _load_json_path(self, p):
        with open(p, encoding="utf-8") as f:
            self.apply_album(AlbumMeta.from_dict(json.load(f)), replace=True)

    def closeEvent(self, e):
        if self._busy():
            if QMessageBox.question(self, "종료", "리핑 중입니다. 취소하고 종료할까요?") != QMessageBox.Yes:
                return e.ignore()
            self.rip_thread.job.cancel()
            self.rip_thread.wait(10000)
        self.s.save()
        super().closeEvent(e)
