"""Capture the CD back cover with a webcam (or open a photo), OCR it, and
turn the text into an editable track list."""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap, QTransform, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QRubberBand, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QMessageBox)

from ..core import ocr
from .util import fit_to_screen, run_async


class ImageView(QLabel):
    """Shows an image scaled to fit; drag to select a crop region."""

    def __init__(self):
        super().__init__(alignment=Qt.AlignCenter)
        self.setMinimumSize(560, 420)
        self.setStyleSheet("background:#111; color:#aaa;")
        self.image: QImage | None = None
        self.sel: QRect | None = None         # selection in image coordinates
        self._origin = None
        self._band = QRubberBand(QRubberBand.Rectangle, self)
        self.selectable = False

    def set_image(self, img: QImage, keep_sel=False):
        self.image = img
        if not keep_sel:
            self.sel = None
            self._band.hide()
        self._redraw()

    def _geom(self):
        if not self.image:
            return 1.0, 0, 0
        s = min(self.width() / self.image.width(), self.height() / self.image.height())
        ox = (self.width() - self.image.width() * s) / 2
        oy = (self.height() - self.image.height() * s) / 2
        return s, ox, oy

    def _redraw(self):
        if not self.image:
            return
        pm = QPixmap.fromImage(self.image).scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self.sel:
            s, _, _ = self._geom()
            p = QPainter(pm)
            p.fillRect(pm.rect(), QColor(0, 0, 0, 110))
            r = QRect(int(self.sel.x() * s), int(self.sel.y() * s), int(self.sel.width() * s), int(self.sel.height() * s))
            p.drawPixmap(r, QPixmap.fromImage(self.image.copy(self.sel)).scaled(r.size()))
            p.setPen(QPen(QColor("#4fc3f7"), 2))
            p.drawRect(r)
            p.end()
        self.setPixmap(pm)

    def resizeEvent(self, e):
        self._redraw()
        super().resizeEvent(e)

    def mousePressEvent(self, e):
        if self.selectable and self.image:
            self._origin = e.position().toPoint()
            self._band.setGeometry(QRect(self._origin, QSize()))
            self._band.show()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._band.setGeometry(QRect(self._origin, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        if self._origin is None:
            return
        r = self._band.geometry()
        self._band.hide()
        self._origin = None
        s, ox, oy = self._geom()
        img_r = QRect(int((r.x() - ox) / s), int((r.y() - oy) / s), int(r.width() / s), int(r.height() / s))
        img_r = img_r.intersected(self.image.rect())
        self.sel = img_r if img_r.width() > 20 and img_r.height() > 20 else None
        self._redraw()

    def cropped(self) -> QImage | None:
        if not self.image:
            return None
        return self.image.copy(self.sel) if self.sel else self.image


class OcrDialog(QDialog):
    def __init__(self, parent, n_tracks: int, camera_index: int = 0, engine: str = "auto", lang: str = "ko"):
        super().__init__(parent)
        self.setWindowTitle("웹캠으로 트랙 목록 인식")
        fit_to_screen(self, 1250, 760)
        self.fixed_count = n_tracks > 0            # 0 = no CD yet: count comes from the text
        self.n_tracks, self.engine, self.lang = n_tracks, engine, lang
        self.cap = None
        self.result: dict[int, dict] = {}

        lay = QVBoxLayout(self)
        split = QSplitter()
        lay.addWidget(split, 1)

        # ---- left: camera / image -------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        cam_row = QHBoxLayout()
        self.cam_combo = QComboBox()
        for i in range(5):
            self.cam_combo.addItem(f"카메라 {i}", i)
        self.cam_combo.setCurrentIndex(min(camera_index, 4))
        self.b_live = QPushButton("▶ 카메라 켜기")
        self.b_shot = QPushButton("📷 촬영")
        self.b_shot.setEnabled(False)
        self.b_file = QPushButton("사진 파일 열기…")
        self.b_rot = QPushButton("⟳ 90°")
        cam_row.addWidget(self.cam_combo); cam_row.addWidget(self.b_live); cam_row.addWidget(self.b_shot)
        cam_row.addStretch(); cam_row.addWidget(self.b_file); cam_row.addWidget(self.b_rot)
        ll.addLayout(cam_row)
        self.view = ImageView()
        self.view.setText("카메라를 켜거나 사진 파일을 여세요.\n\n"
                          "팁: CD 뒷면을 밝은 곳에서 정면으로, 글자가 화면을 가득 채우게 찍으세요.\n"
                          "웹캠 초점이 안 맞으면 휴대폰으로 찍어 '사진 파일 열기'를 쓰는 것이 더 정확합니다.")
        ll.addWidget(self.view, 1)
        ocr_row = QHBoxLayout()
        self.sel_hint = QLabel("촬영 후 트랙 목록 부분을 마우스로 드래그하면 그 영역만 인식합니다.")
        self.sel_hint.setStyleSheet("color:#777")
        self.b_ocr = QPushButton("🔍 문자 인식")
        self.b_ocr.setEnabled(False)
        ocr_row.addWidget(self.sel_hint, 1); ocr_row.addWidget(self.b_ocr)
        ll.addLayout(ocr_row)
        split.addWidget(left)

        # ---- right: text + parsed table ---------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("인식된 텍스트 (직접 수정 가능 — 아래 표가 자동 갱신됩니다)"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("01. 첫 번째 곡\n02. 두 번째 곡\n…\n\n여기에 트랙 목록을 직접 붙여넣어도 됩니다.")
        rl.addWidget(self.text, 2)
        opt = QHBoxLayout()
        self.split_artist = QCheckBox("'아티스트 - 제목' 형식이면 분리 (컴필레이션)")
        opt.addWidget(self.split_artist)
        opt.addStretch()
        rl.addLayout(opt)
        self.table = QTableWidget(n_tracks, 3)
        self.table.setHorizontalHeaderLabels(["#", "제목", "아티스트"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        rl.addWidget(self.table, 3)
        split.addWidget(right)
        split.setSizes([700, 550])

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("트랙에 적용")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.status = QLabel()
        bl = QHBoxLayout()
        bl.addWidget(self.status, 1)
        bl.addWidget(bb)
        lay.addLayout(bl)

        self.timer = QTimer(self, interval=40, timeout=self._grab)
        self.b_live.clicked.connect(self.toggle_camera)
        self.b_shot.clicked.connect(self.snapshot)
        self.b_file.clicked.connect(self.open_file)
        self.b_rot.clicked.connect(self.rotate)
        self.b_ocr.clicked.connect(self.run_ocr)
        self.text.textChanged.connect(self.reparse)
        self.split_artist.toggled.connect(self.reparse)
        self.reparse()

        engines = ocr.available_engines()
        self.status.setText("OCR 엔진: " + (", ".join(engines) if engines else
                                           "없음 — 텍스트를 직접 붙여넣어 사용할 수 있습니다"))

    # ---- camera ---------------------------------------------------------
    def toggle_camera(self):
        if self.cap:
            return self.stop_camera()
        try:
            import cv2
        except ImportError:
            return QMessageBox.warning(self, "카메라", "OpenCV(opencv-python)가 설치되어 있지 않습니다.")
        idx = self.cam_combo.currentData()
        api = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(idx, api)
        if not cap.isOpened():
            return QMessageBox.warning(self, "카메라", f"카메라 {idx}를 열 수 없습니다.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        self.cap = cap
        self.view.selectable = False
        self.b_live.setText("■ 카메라 끄기")
        self.b_shot.setEnabled(True)
        self.timer.start()

    def stop_camera(self):
        self.timer.stop()
        if self.cap:
            self.cap.release()
        self.cap = None
        self.b_live.setText("▶ 카메라 켜기")
        self.b_shot.setEnabled(False)

    def _grab(self):
        import cv2
        ok, frame = self.cap.read()
        if not ok:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        self._frame = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()
        self.view.set_image(self._frame)

    def snapshot(self):
        if getattr(self, "_frame", None) is None:
            return
        img = self._frame
        self.stop_camera()
        self._loaded(img)

    def open_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "CD 뒷면 사진", "", "이미지 (*.jpg *.jpeg *.png *.bmp *.webp *.heic)")
        if p:
            img = QImage(p)
            if img.isNull():
                return QMessageBox.warning(self, "이미지", "이미지를 열 수 없습니다.")
            self.stop_camera()
            self._loaded(img)

    def _loaded(self, img: QImage):
        self.view.selectable = True
        self.view.set_image(img)
        self.b_ocr.setEnabled(True)
        self.sel_hint.setText("트랙 목록 부분을 드래그해 선택하면 정확도가 올라갑니다 (선택 안 하면 전체 인식).")

    def rotate(self):
        if self.view.image is not None and not self.cap:
            self.view.set_image(self.view.image.transformed(QTransform().rotate(90)))

    # ---- OCR --------------------------------------------------------------
    def run_ocr(self):
        img = self.view.cropped()
        if img is None:
            return
        # light preprocessing: upscale small crops, grayscale
        if img.width() < 1600:
            f = 1600 / img.width()
            img = img.scaled(int(img.width() * f), int(img.height() * f), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_Grayscale8)
        self.status.setText("인식 중…")
        self.b_ocr.setEnabled(False)

        def done(text):
            self.b_ocr.setEnabled(True)
            self.text.setPlainText(text)
            self.status.setText("인식 완료. 텍스트나 표를 확인·수정한 뒤 '트랙에 적용'을 누르세요.")

        def fail(e):
            self.b_ocr.setEnabled(True)
            self.status.setText("인식 실패")
            QMessageBox.warning(self, "OCR", e)

        run_async(ocr.recognize, img, self.engine, self.lang, on_done=done, on_fail=fail)

    def reparse(self):
        parsed = ocr.parse_tracklist(self.text.toPlainText())
        if not self.fixed_count:
            self.n_tracks = ocr.guess_track_count(parsed)
            self.table.setRowCount(self.n_tracks)
        mapping = ocr.assign_titles(parsed, self.n_tracks)
        split = self.split_artist.isChecked()
        self.table.blockSignals(True)
        for r in range(self.n_tracks):
            n = r + 1
            title, artist = mapping.get(n, ""), ""
            if split and title:
                m = re.match(r"^(.+?)\s+[-–/]\s+(.+)$", title)
                if m:
                    artist, title = m.group(1), m.group(2)
            for c, v in enumerate((str(n), title, artist)):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)

    def _accept(self):
        self.result = {}
        for r in range(self.n_tracks):
            title = self.table.item(r, 1).text().strip()
            artist = self.table.item(r, 2).text().strip()
            if title or artist:
                self.result[r + 1] = {"title": title, "artist": artist}
        self.stop_camera()
        self.accept()

    def reject(self):
        self.stop_camera()
        super().reject()
