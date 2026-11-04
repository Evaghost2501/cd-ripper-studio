"""Cover-art finder: iTunes (up to 3000px), Deezer (1000px), Cover Art Archive,
plus Google Images in the browser → drag & drop / paste URL / clipboard."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QListView)

from ..sources import web
from .util import image_size, pixmap_from_bytes, run_async


def image_bytes_from_qimage(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "JPG", 95)
    return bytes(ba)


class CoverDialog(QDialog):
    def __init__(self, parent, artist: str, album: str, mbid: str = "", country: str = "KR"):
        super().__init__(parent)
        self.setWindowTitle("앨범 커버 찾기")
        self.resize(980, 680)
        self.setAcceptDrops(True)
        self.country, self.mbid = country, mbid
        self.result: bytes | None = None
        self.cands: list[web.CoverCandidate] = []

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query_artist = QLineEdit(artist, placeholderText="아티스트")
        self.query_album = QLineEdit(album, placeholderText="앨범")
        b_search = QPushButton("검색")
        b_search.setDefault(True)
        b_google = QPushButton("Google 이미지에서 찾기 ↗")
        b_google.setToolTip("브라우저에서 Google 이미지 검색을 엽니다.\n"
                            "마음에 드는 이미지를 이 창으로 드래그하거나, 이미지 주소를 복사해 'URL 붙여넣기'를 누르세요.")
        row.addWidget(self.query_artist, 2); row.addWidget(self.query_album, 3)
        row.addWidget(b_search); row.addWidget(b_google)
        lay.addLayout(row)

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(200, 200))
        self.list.setGridSize(QSize(225, 260))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)

        hint = QLabel("💡 이미지 파일·브라우저 이미지를 이 창에 끌어다 놓아도 됩니다. Ctrl+V로 클립보드 이미지 붙여넣기.")
        hint.setStyleSheet("color:#777")
        lay.addWidget(hint)
        self.status = QLabel()
        lay.addWidget(self.status)

        bottom = QHBoxLayout()
        for text, fn in (("파일에서…", self.from_file), ("URL 붙여넣기…", self.from_url),
                         ("클립보드 이미지", self.from_clipboard)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        bottom.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("선택한 커버 사용")
        bb.accepted.connect(self.use_selected)
        bb.rejected.connect(self.reject)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

        b_search.clicked.connect(self.search)
        self.query_album.returnPressed.connect(self.search)
        b_google.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(web.google_images_url(self.query_artist.text(), self.query_album.text()))))
        self.list.itemDoubleClicked.connect(lambda *_: self.use_selected())
        if artist or album:
            self.search()

    # ------------------------------------------------------------------
    def search(self):
        self.list.clear()
        self.cands = []
        self.status.setText("검색 중…")
        errs: list = []
        run_async(web.cover_search, self.query_artist.text().strip(), self.query_album.text().strip(),
                  self.mbid, self.country, errs, on_done=lambda c: self._show(c, errs),
                  on_fail=lambda e: self.status.setText("실패: " + e))

    def _show(self, cands, errs):
        self.cands = cands
        self.status.setText(f"{len(cands)}개 후보" + (f"  (일부 실패: {'; '.join(errs)[:150]})" if errs else "")
                            if cands else "후보가 없습니다. Google 이미지 검색을 이용해 보세요.")
        for i, c in enumerate(cands):
            size = f"{c.width}×{c.height}" if c.width else "원본"
            it = QListWidgetItem(f"[{c.source}] {size}\n{c.info[:50]}")
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
            run_async(web.download, c.thumb_url,
                      on_done=lambda d, it=it: it.setIcon(QIcon(pixmap_from_bytes(d, 200))),
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
