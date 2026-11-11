"""Cover / image picker.

Main tab: a real web browser *inside* the dialog showing Google Images (or Yes24 / Amazon) for the
typed query. Right-click any image → "이 이미지를 커버로 사용".  A real browser is used instead of
reading Google's result page in the background, because Google changes / blocks that page often.

Second tab: iTunes · Deezer · Cover Art Archive results as a thumbnail grid.
Also: file, URL, clipboard, drag & drop.
"""
from __future__ import annotations

import base64
import os
from urllib.parse import quote_plus

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from ..sources import web
from .util import fit_to_screen, image_size, pixmap_from_bytes, px, run_async

try:  # QtWebEngine (embedded Chromium) – optional, the dialog degrades gracefully without it
    from PySide6.QtWebEngineCore import QWebEngineContextMenuRequest, QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:  # pragma: no cover
    HAS_WEBENGINE = False

SITES = {
    "Google 이미지": "https://www.google.com/search?tbm=isch&q={q}",
    "Yes24": "https://www.yes24.com/Product/Search?domain=BOOK&query={q}",
    "Amazon": "https://www.amazon.com/s?k={q}&i=stripbooks",
}


def image_bytes_from_qimage(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "JPG", 95)
    return bytes(ba)


def decode_data_url(url: str) -> bytes | None:
    """data:image/jpeg;base64,.... → bytes"""
    if not url.startswith("data:") or "," not in url:
        return None
    head, data = url.split(",", 1)
    try:
        return base64.b64decode(data) if ";base64" in head else bytes(QUrl.fromPercentEncoding(data.encode()), "utf-8")
    except (ValueError, TypeError):
        return None


_profile = None


def _web_profile():
    """Persistent profile so Google consent / cookies survive between uses."""
    global _profile
    if _profile is None:
        from ..core.settings import config_dir
        from PySide6.QtWidgets import QApplication
        _profile = QWebEngineProfile("CDRipperStudio", QApplication.instance())   # lives as long as the app
        _profile.setPersistentStoragePath(os.path.join(config_dir(), "web"))
        _profile.setCachePath(os.path.join(config_dir(), "web-cache"))
    return _profile


if HAS_WEBENGINE:
    class CoverWebView(QWebEngineView):
        """Browser whose right-click menu on an image offers "이 이미지를 커버로 사용"."""
        imageChosen = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setPage(QWebEnginePage(_web_profile(), self))

        def contextMenuEvent(self, e):
            req = self.lastContextMenuRequest()
            menu = self.createStandardContextMenu()
            if req is not None and req.mediaType() == QWebEngineContextMenuRequest.MediaType.MediaTypeImage \
                    and req.mediaUrl().isValid():
                url = req.mediaUrl().toString()
                act = QAction("이 이미지를 커버로 사용", menu)
                f = act.font(); f.setBold(True); act.setFont(f)
                act.triggered.connect(lambda: self.choose(url))
                first = menu.actions()[0] if menu.actions() else None
                menu.insertAction(first, act)
                menu.insertSeparator(first)
            menu.popup(e.globalPos())

        def choose(self, url: str):
            # also put the rendered image on the clipboard: fallback when the site blocks downloads
            self.page().triggerAction(QWebEnginePage.WebAction.CopyImageToClipboard)
            self.imageChosen.emit(url)


class CoverDialog(QDialog):
    def __init__(self, parent, artist: str, album: str, mbid: str = "", country: str = "KR",
                 music_sources: bool = False):
        super().__init__(parent)
        self.setWindowTitle("커버 이미지 찾기")
        fit_to_screen(self, 1200, 820)
        self.setAcceptDrops(True)
        self.country, self.mbid = country, mbid
        self.result: bytes | None = None
        self.cands: list[web.CoverCandidate] = []
        self._gen = 0
        self._page_url = ""

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit(" ".join(x for x in (artist.strip(), album.strip()) if x),
                               placeholderText="검색어 (입력한 그대로 검색)")
        self.query.setClearButtonEnabled(True)
        row.addWidget(self.query, 1)
        self.site_btns = {}
        for name in SITES:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self.open_site(n))
            row.addWidget(b)
            self.site_btns[name] = b
        b_ext = QPushButton("↗")
        b_ext.setToolTip("현재 검색을 외부 브라우저로 열기")
        b_ext.clicked.connect(self.open_external)
        row.addWidget(b_ext)
        lay.addLayout(row)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- tab 1: embedded browser (or fallback grid) ----------------------
        if HAS_WEBENGINE:
            web_tab = QWidget()
            wl = QVBoxLayout(web_tab)
            wl.setContentsMargins(0, 0, 0, 0)
            hint = QLabel("① 이미지를 클릭해 크게 연 다음  ② 큰 이미지에서 <b>마우스 오른쪽 버튼 → "
                          "'이 이미지를 커버로 사용'</b>  (작은 썸네일에서 바로 골라도 됩니다)")
            hint.setWordWrap(True)
            hint.setProperty("role", "muted")
            wl.addWidget(hint)
            self.view = CoverWebView()
            from . import theme
            self.view.page().setBackgroundColor(theme.qcolor("surface"))   # no white flash before a page loads
            self.view.imageChosen.connect(self.use_image_url)
            self.view.urlChanged.connect(lambda u: setattr(self, "_page_url", u.toString()))
            wl.addWidget(self.view, 1)
            self.tabs.addTab(web_tab, "Google · Yes24 · Amazon")
        else:
            self.view = None

        # ---- tab 2: music services grid ----------------------------------------
        grid_tab = QWidget()
        gl = QVBoxLayout(grid_tab)
        gl.setContentsMargins(0, 0, 0, 0)
        g_row = QHBoxLayout()
        g_btn = QPushButton("iTunes · Deezer · Cover Art Archive 검색")
        g_btn.clicked.connect(self.search_music)
        g_row.addWidget(g_btn)
        g_row.addStretch()
        gl.addLayout(g_row)
        icon = px(150)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(icon, icon))
        self.list.setGridSize(QSize(icon + px(30), icon + px(70)))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda *_: self.use_selected())
        gl.addWidget(self.list, 1)
        self.tabs.addTab(grid_tab, "음악 서비스")
        self.tabs.currentChanged.connect(self._tab_changed)

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
        self.use_btn = QPushButton("선택한 이미지 사용")
        self.use_btn.setProperty("variant", "primary")
        self.use_btn.clicked.connect(self.use_selected)
        bottom.addWidget(self.use_btn)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("닫기")
        bb.rejected.connect(self.reject)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

        self.query.returnPressed.connect(self.search)
        self._music_searched = False
        if music_sources or not HAS_WEBENGINE:
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self._tab_changed(self.tabs.currentIndex())
        if self.query.text():
            QTimer.singleShot(0, self.search)
        else:
            self.status.setText("검색어를 입력하세요.")

    # ------------------------------------------------------------------ searching
    def _on_web_tab(self) -> bool:
        return self.view is not None and self.tabs.currentIndex() == 0

    def _tab_changed(self, _i):
        self.use_btn.setVisible(not self._on_web_tab())

    def search(self):
        if self._on_web_tab():
            self.open_site("Google 이미지")
        else:
            self.search_music()

    def site_url(self, name: str) -> str:
        return SITES[name].format(q=quote_plus(self.query.text().strip()))

    def open_site(self, name: str):
        q = self.query.text().strip()
        if not q:
            return
        if self.view is None:            # no embedded browser available
            QDesktopServices.openUrl(QUrl(self.site_url(name)))
            return self.status.setText("외부 브라우저에서 이미지를 이 창으로 끌어다 놓으세요.")
        self.tabs.setCurrentIndex(0)
        self.view.setUrl(QUrl(self.site_url(name)))
        self.status.setText(f"{name}: “{q}” — 마음에 드는 이미지에서 오른쪽 버튼 → '이 이미지를 커버로 사용'")

    def open_external(self):
        url = self._page_url if self._on_web_tab() and self._page_url else self.site_url("Google 이미지")
        QDesktopServices.openUrl(QUrl(url))

    def search_music(self):
        q = self.query.text().strip()
        if not q:
            return
        self._gen += 1
        gen = self._gen
        self.list.clear()
        self.cands = []
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self.status.setText("iTunes · Deezer · Cover Art Archive 검색 중…")
        errs: list = []
        run_async(web.cover_search, "", q, self.mbid, self.country, errs,
                  on_done=lambda c: gen == self._gen and self._show(c, errs),
                  on_fail=lambda e: self.status.setText("실패: " + e))

    def _show(self, cands, errs):
        self.cands = cands
        self.status.setText(f"{len(cands)}개 이미지 — 더블클릭하면 원본 크기로 가져옵니다."
                            + (f"  ({'; '.join(errs)[:150]})" if errs else "") if cands else
                            "결과가 없습니다." + (f" ({'; '.join(errs)[:150]})" if errs else ""))
        icon = self.list.iconSize().width()
        for i, c in enumerate(cands):
            size = f"{c.width}×{c.height}" if c.width else "원본"
            it = QListWidgetItem(f"[{c.source}] {size}\n{c.info[:40]}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(c.full_url)
            self.list.addItem(it)
            run_async(web.download, c.thumb_url,
                      on_done=lambda d, it=it: it.setIcon(QIcon(pixmap_from_bytes(d, icon))),
                      on_fail=lambda e: None)

    # ------------------------------------------------------------------ choosing
    def use_image_url(self, url: str):
        """An image picked in the embedded browser."""
        data = decode_data_url(url)
        if data:                         # Google thumbnails are often inline data: images
            return self._finish_any(data)
        if url.startswith("file:"):
            with open(QUrl(url).toLocalFile(), "rb") as f:
                return self._finish_any(f.read())
        self.status.setText("원본 이미지 가져오는 중…")
        run_async(web.download, url, 20, {"Referer": self._page_url or "https://www.google.com/"},
                  on_done=self._finish_any, on_fail=self._fallback_clipboard)

    def _fallback_clipboard(self, err):
        # the site refused the download → use the image the browser already shows (copied to clipboard)
        def take():
            img = QGuiApplication.clipboard().image()
            if img.isNull():
                return self._fail(err + " — 다른 이미지를 고르거나, 이미지를 크게 연 뒤 다시 시도하세요.")
            self._finish(image_bytes_from_qimage(img))
        QTimer.singleShot(300, take)

    def use_selected(self):
        it = self.list.currentItem()
        if not it:
            return
        c = self.cands[it.data(Qt.UserRole)]
        self.status.setText("원본 이미지 다운로드 중…")

        def done(data):
            if image_size(data)[0] == 0:
                run_async(web.download, c.thumb_url, on_done=self._finish, on_fail=self._fail)
            else:
                self._finish(data)

        run_async(web.download, c.full_url, on_done=done,
                  on_fail=lambda e: run_async(web.download, c.thumb_url, on_done=self._finish, on_fail=self._fail))

    def _fail(self, e):
        self.status.setText("가져오기 실패: " + e)

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
        """Convert webp/avif/bmp etc. to JPEG for tag compatibility."""
        if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
            return self._finish(data)
        img = QImage.fromData(data)
        if img.isNull():
            return self._fail("이미지를 읽을 수 없습니다 (웹페이지 주소일 수 있습니다)")
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
        if e.matches(QKeySequence.Paste) and not (self.view and self.view.hasFocus()):
            return self.from_clipboard()
        super().keyPressEvent(e)
