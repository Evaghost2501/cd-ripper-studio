"""Album metadata search / selection dialog (MusicBrainz, iTunes, Deezer)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from ..core.metadata import AlbumMeta
from ..core.toc import Toc
from ..sources import web
from .util import fit_to_screen, pixmap_from_bytes, px, run_async


class SearchDialog(QDialog):
    def __init__(self, parent, artist: str, album: str, toc: Toc | None, country: str = "KR",
                 preset: list[AlbumMeta] | None = None):
        super().__init__(parent)
        self.setWindowTitle("앨범 정보 검색")
        fit_to_screen(self, 1100, 640)
        self.toc, self.country = toc, country
        self.results: list[AlbumMeta] = []
        self.selected: AlbumMeta | None = None
        self._gen = 0

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.artist = QLineEdit(artist, placeholderText="아티스트")
        self.album = QLineEdit(album, placeholderText="앨범 이름")
        self.btn = QPushButton("검색")
        self.btn.setDefault(True)
        row.addWidget(QLabel("아티스트")); row.addWidget(self.artist, 2)
        row.addWidget(QLabel("앨범")); row.addWidget(self.album, 3)
        self.src = {}
        for s in ("MusicBrainz", "iTunes", "Deezer"):
            cb = QCheckBox(s, checked=True)
            self.src[s] = cb
            row.addWidget(cb)
        row.addWidget(self.btn)
        lay.addLayout(row)

        split = QSplitter()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["출처", "아티스트", "앨범", "발매", "트랙", "비고"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        split.addWidget(self.table)

        side = QWidget()
        sl = QVBoxLayout(side)
        self.cover = QLabel(alignment=Qt.AlignCenter)
        self.cover.setFixedSize(px(200), px(200))
        self.cover.setStyleSheet("border:1px solid #888;")
        self.tracks = QListWidget()
        sl.addWidget(self.cover, alignment=Qt.AlignHCenter)
        sl.addWidget(self.tracks)
        split.addWidget(side)
        split.setSizes([720, 360])
        lay.addWidget(split, 1)

        self.status = QLabel()
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("이 정보 적용")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.ok_btn = bb.button(QDialogButtonBox.Ok)
        self.ok_btn.setEnabled(False)
        lay.addWidget(bb)

        self.btn.clicked.connect(self.search)
        self.album.returnPressed.connect(self.search)
        self.artist.returnPressed.connect(self.search)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.doubleClicked.connect(lambda *_: self._accept())

        if preset:
            self._show(preset)
            self.status.setText("디스크 ID로 찾은 결과입니다. 맞는 앨범을 선택하세요.")
        elif artist or album:
            self.search()

    def search(self):
        sources = [s for s, cb in self.src.items() if cb.isChecked()]
        if not (self.artist.text().strip() or self.album.text().strip()):
            return
        self.status.setText("검색 중…")
        self.btn.setEnabled(False)
        errs: list = []
        run_async(web.search_all, self.artist.text().strip(), self.album.text().strip(), sources,
                  self.country, errs, on_done=lambda r: self._show(r, errs), on_fail=self._fail)

    def _fail(self, msg):
        self.btn.setEnabled(True)
        self.status.setText("검색 실패: " + msg)

    def _show(self, results, errs=None):
        self.btn.setEnabled(True)
        n = len(self.toc.audio_tracks) if self.toc else 0
        # results with matching track count first
        results = sorted(results, key=lambda a: (len(a.tracks) != n) if n else 0)
        self.results = results
        self.table.setRowCount(len(results))
        for r, a in enumerate(results):
            match = n and len(a.tracks) == n
            vals = [a.source, a.albumartist, a.album, a.date, str(len(a.tracks)),
                    ("✔ 트랙 수 일치" if match else "") + (" 디스크 ID 일치" if a.mb_discid else "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if match:
                    it.setForeground(QBrush(QColor("#2e7d32")))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        msg = f"{len(results)}개 결과"
        if errs:
            msg += "   (일부 실패: " + "; ".join(errs)[:200] + ")"
        if not results:
            msg = "결과가 없습니다. 검색어를 바꾸거나 직접 입력하세요." + (f" ({'; '.join(errs)[:200]})" if errs else "")
        self.status.setText(msg)
        if results:
            self.table.selectRow(0)

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        a = self.results[rows[0].row()]
        self._gen += 1
        gen = self._gen
        self.ok_btn.setEnabled(False)
        self.tracks.clear()
        self.tracks.addItem("트랙 목록 불러오는 중…")
        self.cover.clear()
        if a.cover_url:
            run_async(web.download, a.cover_url,
                      on_done=lambda d: gen == self._gen and self.cover.setPixmap(pixmap_from_bytes(d, px(200) - 2)),
                      on_fail=lambda e: None)

        def done(full: AlbumMeta):
            if gen != self._gen:
                return
            self.results[rows[0].row()] = full
            self.tracks.clear()
            for t in full.tracks:
                extra = f"  — {t.artist}" if t.artist and t.artist != full.albumartist else ""
                self.tracks.addItem(f"{t.number:02d}. {t.title}{extra}")
            info = [x for x in (full.label, full.catalog, full.genre) if x]
            if info:
                self.tracks.addItem("")
                self.tracks.addItem(" · ".join(info))
            self.ok_btn.setEnabled(True)

        run_async(web.complete, a, self.toc, on_done=done,
                  on_fail=lambda e: gen == self._gen and (self.tracks.clear(), self.tracks.addItem("실패: " + e)))

    def _accept(self):
        rows = self.table.selectionModel().selectedRows()
        if rows and self.ok_btn.isEnabled():
            self.selected = self.results[rows[0].row()]
            self.accept()
