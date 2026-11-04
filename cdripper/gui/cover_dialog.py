"""Cover/image finder: Google Images (in-app results, exact query), optional music stores,
plus drag & drop / paste URL / clipboard."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QListView)

from ..sources import web
from .util import fit_to_screen, image_size, pixmap_from_bytes, px, run_async


def image_bytes_from_qimage(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "JPG", 95)
    return bytes(ba)


class CoverDialog(QDialog):
    """Searches Google Images with exactly the typed query and shows the results in-app.
    Music stores (iTunes/Deezer/Cover Art Archive) are optional."""

    def __init__(self, parent, artist: str, album: str, mbid: str = "", country: str = "KR",
                 music_sources: bool = False):
        super().__init__(parent)
        self.setWindowTitle("커버 이미지 찾기 (Google)")
        fit_to_screen(self, 1000, 700)
        self.setAcceptDrops(True)
        self.country, self.mbid = country, mbid
        self.result: bytes | None = None
        self.cands: list[web.CoverCandidate] = []
        self._gen = 0

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit(" ".join(x for x in (artist.strip(), album.strip()) if x),
                               placeholderText="검색어 (입력한 그대로 Google 이미지에서 검색)")
        self.query.setClearButtonEnabled(True)
        b_search = QPushButton("🔍 검색")
        b_search.setDefault(True)
        b_browser = QPushButton("브라우저로 열기 ↗")
        b_browser.setToolTip("같은 검색어로 브라우저에서 Google 이미지를 엽니다.\n"
                             "이미지를 이 창으로 드래그하거나, 이미지 주소를 복사해 'URL 붙여넣기'를 누르세요.")
        row.addWidget(self.query, 1)
        row.addWidget(b_search)
        row.addWidget(b_browser)
        lay.addLayout(row)
        self.music = QCheckBox("음악 서비스(iTunes·Deezer·Cover Art Archive)도 함께 검색")
        self.music.setChecked(music_sources)
        lay.addWidget(self.music)

        icon = px(160)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(icon, icon))
        self.list.setGridSize(QSize(icon + px(30), icon + px(70)))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)

        hint = QLabel("💡 이미지 파일·브라우저 이미지를 이 창에 끌어다 놓아도 됩니다. Ctrl+V로 클립보드 이미지 붙여넣기.")
        hint.setStyleSheet("color:#777")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        bottom = QHBoxLayout()
        for text, fn in (("파일에서…", self.from_file), ("URL 붙여넣기…", self.from_url),
                         ("클립보드 이미지", self.from_clipboard)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        bottom.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("선택한 이미지 사용")
        bb.accepted.connect(self.use_selected)
        bb.rejected.connect(self.reject)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

        b_search.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        b_browser.clicked.connect(self.open_browser)
        self.list.itemDoubleClicked.connect(lambda *_: self.use_selected())
        if self.query.text():
            self.search()
        else:
            self.status.setText("검색어를 입력하세요.")

    def open_browser(self):
        q = self.query.text().strip()
        if q:
            QDesktopServices.openUrl(QUrl(web.google_images_url(q)))

    # ------------------------------------------------------------------
    def search(self):
        q = self.query.text().strip()
        if not q:
            return
        self._gen += 1
        gen = self._gen
        self.list.clear()
        self.cands = []
        self.status.setText(f"Google 이미지에서 “{q}” 검색 중…")
        errs: list = []
        music = self.music.isChecked()

        def work():
            found = []
            try:
                found += web.google_image_search(q)
            except Exception as e:  # noqa: BLE001
                errs.append(f"Google: {web._short(e)}")
            if music:
                found += web.cover_search("", q, self.mbid, self.country, errs)
            return found

        run_async(work, on_done=lambda c: gen == self._gen and self._show(c, errs),
                  on_fail=lambda e: self.status.setText("실패: " + e))

    def _show(self, cands, errs):
        self.cands = cands
        google_failed = any(e.startswith("Google") for e in errs)
        if cands:
            self.status.setText(f"{len(cands)}개 이미지 — 더블클릭하면 원본 크기로 가져옵니다."
                                + (f"  ({'; '.join(errs)[:150]})" if errs else ""))
        else:
            self.status.setText("프로그램 안에서 결과를 가져오지 못해 브라우저로 Google 이미지를 열었습니다. "
                                "원하는 이미지를 이 창으로 끌어다 놓으세요." if google_failed
                                else "결과가 없습니다. 검색어를 바꿔 보세요.")
            if google_failed:
                self.open_browser()
        icon = self.list.iconSize().width()
        for i, c in enumerate(cands):
            size = f"{c.width}×{c.height}" if c.width else "원본"
            it = QListWidgetItem(f"{size}\n{c.info[:40]}" if c.source == "Google"
                                 else f"[{c.source}] {size}\n{c.info[:40]}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(c.full_url)
            self.list.addItem(it)
            run_async(web.download, c.thumb_url,
                      on_done=lambda d, it=it: it.setIcon(QIcon(pixmap_from_bytes(d, icon))),
                      on_fail=lambda e: None)

    def use_selected(self):
        it = self.list.currentItem()
        if not it:
            return
        c = self.cands[it.data(Qt.UserRole)]
        self.status.setText("원본 이미지 다운로드 중…")

        def done(data):
            if image_size(data)[0] == 0:
                # some sources refuse very large sizes – fall back to thumbnail
                run_async(web.download, c.thumb_url, on_done=self._finish, on_fail=self._fail)
            else:
                self._finish(data)

        run_async(web.download, c.full_url, on_done=done,
                  on_fail=lambda e: run_async(web.download, c.thumb_url, on_done=self._finish, on_fail=self._fail))

    def _fail(self, e):
        self.status.setText("다운로드 실패: " + e)

    def _finish(self, data: bytes):
        w, h = image_size(data)
        if not w:
            return self._fail("이미지가 아닙니다")
        self.result = data
        self.accept()

    def from_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "커버 이미지", "", "이미지 (*.jpg *.jpeg *.png *.webp *.bmp)")
        if p:
            with open(p, "rb") as f:
                self._finish_any(f.read())

    def from_url(self, url: str | None = None):
        if not url:
            url = QGuiApplication.clipboard().text().strip()
            url, ok = QInputDialog.getText(self, "이미지 URL", "이미지 주소:", text=url if url.startswith("http") else "")
            if not ok or not url:
                return
        self.status.setText("다운로드 중…")
        run_async(web.download, url, on_done=self._finish_any, on_fail=self._fail)

    def from_clipboard(self):
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            txt = QGuiApplication.clipboard().text().strip()
            if txt.startswith("http"):
                return self.from_url(txt)
            return self._fail("클립보드에 이미지가 없습니다")
        self._finish(image_bytes_from_qimage(img))

    def _finish_any(self, data: bytes):
        """Convert webp/bmp etc. to JPEG for tag compatibility."""
        if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
            return self._finish(data)
        img = QImage.fromData(data)
        if img.isNull():
            return self._fail("이미지를 읽을 수 없습니다")
        self._finish(image_bytes_from_qimage(img))

    # drag & drop -------------------------------------------------------
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasImage():
            return self._finish(image_bytes_from_qimage(QImage(md.imageData())))
        for u in md.urls():
            if u.isLocalFile():
                with open(u.toLocalFile(), "rb") as f:
                    return self._finish_any(f.read())
            return self.from_url(u.toString())

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Paste):
            return self.from_clipboard()
        super().keyPressEvent(e)
