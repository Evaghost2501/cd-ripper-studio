from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from ..core import ocr
from ..core.settings import Settings
from .util import fit_to_screen


class SettingsDialog(QDialog):
    def __init__(self, parent, s: Settings):
        super().__init__(parent)
        self.setWindowTitle("설정")
        fit_to_screen(self, 560, 480)
        self.s = s
        tabs = QTabWidget()

        # ripping
        w = QWidget(); f = QFormLayout(w)
        self.secure = QCheckBox("보안 모드: AccurateRip 불일치 시 전 구간 재독취·비교 (EAC/dBpoweramp 방식)")
        self.secure.setChecked(s.secure_mode)
        self.ar = QCheckBox("AccurateRip 데이터베이스로 검증"); self.ar.setChecked(s.accuraterip)
        self.offset = QSpinBox(); self.offset.setRange(-3000, 3000); self.offset.setValue(s.read_offset)
        self.offset.setSuffix(" 샘플")
        off_help = QLabel('<a href="http://www.accuraterip.com/driveoffsets.htm">드라이브별 오프셋 목록</a> '
                          '(예: 대부분의 LG/ASUS 드라이브 +6, Pioneer +48/+667)')
        off_help.setOpenExternalLinks(True); off_help.setWordWrap(True)
        self.eject = QCheckBox("완료 후 CD 꺼내기"); self.eject.setChecked(s.eject_when_done)
        f.addRow(self.secure); f.addRow(self.ar)
        f.addRow("읽기 오프셋 보정", self.offset); f.addRow("", off_help); f.addRow(self.eject)
        tabs.addTab(w, "리핑")

        # tags & files
        w = QWidget(); f = QFormLayout(w)
        self.id3 = QComboBox(); self.id3.addItems(["ID3v2.3 (Windows 탐색기·차량 호환 최고)", "ID3v2.4"])
        self.id3.setCurrentIndex(0 if s.id3v23 else 1)
        self.embed = QCheckBox("커버를 파일에 내장"); self.embed.setChecked(s.embed_cover)
        self.cmax = QComboBox()
        for v, t in ((0, "원본 크기"), (500, "500px"), (800, "800px"), (1000, "1000px (권장)"), (1400, "1400px")):
            self.cmax.addItem(t, v)
        self.cmax.setCurrentIndex(max(0, self.cmax.findData(s.cover_max_px)))
        self.folderjpg = QCheckBox("폴더에 folder.jpg 저장 (원본 해상도)"); self.folderjpg.setChecked(s.save_cover_file)
        self.m3u = QCheckBox("재생목록(.m3u8) 생성"); self.m3u.setChecked(s.write_m3u)
        self.cue = QCheckBox("큐시트(.cue) 생성"); self.cue.setChecked(s.write_cue)
        self.log = QCheckBox("추출 로그(.log) 생성"); self.log.setChecked(s.write_log)
        self.flac = QCheckBox("보관용 FLAC 사본도 함께 만들기 (_FLAC 폴더)"); self.flac.setChecked(s.extra_flac)
        self.vtpl = QLineEdit(s.various_template)
        f.addRow("MP3 태그 버전", self.id3); f.addRow(self.embed); f.addRow("내장 커버 최대 크기", self.cmax)
        f.addRow(self.folderjpg); f.addRow(self.m3u); f.addRow(self.cue); f.addRow(self.log); f.addRow(self.flac)
        f.addRow("컴필레이션 앨범 파일명", self.vtpl)
        tabs.addTab(w, "태그 / 파일")

        # metadata / OCR
        w = QWidget(); f = QFormLayout(w)
        self.auto = QCheckBox("CD를 넣으면 자동으로 웹에서 정보 검색"); self.auto.setChecked(s.auto_lookup)
        self.country = QComboBox()
        for c in ("KR", "US", "JP", "GB", "DE", "FR"):
            self.country.addItem(c)
        self.country.setCurrentText(s.store_country)
        self.engine = QComboBox()
        self.engine.addItem("자동", "auto")
        for e in ocr.available_engines():
            self.engine.addItem({"windows": "Windows 내장 OCR", "tesseract": "Tesseract"}[e], e)
        self.engine.setCurrentIndex(max(0, self.engine.findData(s.ocr_engine)))
        self.lang = QComboBox()
        for code, name in (("ko", "한국어"), ("en", "영어"), ("ja", "일본어"), ("zh-Hans", "중국어 간체")):
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.ocr_lang)))
        self.ffmpeg = QLineEdit(s.ffmpeg_path, placeholderText="자동 탐지 (exe 옆 ffmpeg.exe 또는 PATH)")
        b = QPushButton("…")
        b.clicked.connect(lambda: self.ffmpeg.setText(
            QFileDialog.getOpenFileName(self, "ffmpeg.exe", "", "ffmpeg (ffmpeg*)")[0] or self.ffmpeg.text()))
        row = QHBoxLayout(); row.addWidget(self.ffmpeg); row.addWidget(b)
        f.addRow(self.auto); f.addRow("iTunes 스토어 국가", self.country)
        f.addRow("OCR 엔진", self.engine); f.addRow("OCR 언어", self.lang)
        f.addRow("ffmpeg (AAC/ALAC용)", row)
        tabs.addTab(w, "메타데이터 / OCR")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        s = self.s
        s.secure_mode, s.accuraterip = self.secure.isChecked(), self.ar.isChecked()
        s.read_offset, s.eject_when_done = self.offset.value(), self.eject.isChecked()
        s.id3v23 = self.id3.currentIndex() == 0
        s.embed_cover, s.cover_max_px = self.embed.isChecked(), self.cmax.currentData()
        s.save_cover_file, s.write_m3u = self.folderjpg.isChecked(), self.m3u.isChecked()
        s.write_cue, s.write_log, s.extra_flac = self.cue.isChecked(), self.log.isChecked(), self.flac.isChecked()
        s.various_template = self.vtpl.text().strip() or s.various_template
        s.auto_lookup, s.store_country = self.auto.isChecked(), self.country.currentText()
        s.ocr_engine, s.ocr_lang = self.engine.currentData(), self.lang.currentData()
        s.ffmpeg_path = self.ffmpeg.text().strip()
        s.save()
        self.accept()
