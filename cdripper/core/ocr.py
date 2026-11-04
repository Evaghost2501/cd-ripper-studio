"""OCR of a CD back-cover photo and track-list parsing.

Engines (tried in order):
  1. Windows built-in OCR (Windows.Media.Ocr) – offline, supports Korean if the
     Korean language pack is installed.
  2. Tesseract (if tesseract.exe is installed) – fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

IS_WINDOWS = sys.platform == "win32"


def available_engines() -> list[str]:
    engines = []
    if IS_WINDOWS:
        try:
            import winrt.windows.media.ocr  # noqa: F401
            engines.append("windows")
        except ImportError:
            pass
    if find_tesseract():
        engines.append("tesseract")
    return engines


def find_tesseract() -> str | None:
    for c in (shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if c and os.path.isfile(c):
            return c
    return None


def windows_languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


def recognize(qimage, engine: str = "auto", lang: str = "ko") -> str:
    """Run OCR on a QImage and return text (one line per recognised line)."""
    engines = available_engines() if engine == "auto" else [engine]
    errors = []
    for e in engines:
        try:
            if e == "windows":
                return _windows_ocr(qimage, lang)
            if e == "tesseract":
                return _tesseract(qimage, lang)
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{e}: {ex}")
    raise RuntimeError("사용 가능한 OCR 엔진이 없습니다.\n" + "\n".join(errors) +
                       "\n\nWindows 설정 > 시간 및 언어 > 언어에서 한국어 언어팩(광학 문자 인식 포함)을 설치하거나 "
                       "Tesseract-OCR을 설치하세요.")


def _windows_ocr(qimage, lang: str) -> str:
    import asyncio
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    import ctypes
    ctypes.windll.ole32.CoInitializeEx(None, 0)   # join the MTA in this worker thread (WinRT needs COM)
    img = qimage.convertToFormat(QImage.Format_ARGB32)   # = BGRA byte order on little endian
    max_dim = OcrEngine.max_image_dimension
    if max(img.width(), img.height()) > max_dim:
        img = img.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    data = bytes(ptr)[: img.bytesPerLine() * h]
    if img.bytesPerLine() != w * 4:  # strip row padding
        data = b"".join(data[y * img.bytesPerLine(): y * img.bytesPerLine() + w * 4] for y in range(h))

    engine = None
    if lang:
        try:
            lg = Language(lang)
            if OcrEngine.is_language_supported(lg):
                engine = OcrEngine.try_create_from_language(lg)
        except Exception:  # noqa: BLE001
            engine = None
    engine = engine or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR 엔진을 만들 수 없습니다 (OCR 언어팩 없음)")

    async def run():
        writer = DataWriter()
        writer.write_bytes(data)
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h,
                                                     BitmapAlphaMode.PREMULTIPLIED)
        res = await engine.recognize_async(bmp)
        return [ln.text for ln in res.lines]

    lines = asyncio.run(run())
    # Windows OCR puts spaces between every Hangul syllable block for some fonts; tidy it
    return "\n".join(_fix_cjk_spacing(l) for l in lines)


def _tesseract(qimage, lang: str) -> str:
    exe = find_tesseract()
    if not exe:
        raise RuntimeError("tesseract가 설치되어 있지 않습니다")
    tl = {"ko": "kor+eng", "ja": "jpn+eng", "en": "eng", "zh-Hans": "chi_sim+eng"}.get(lang, "eng")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "img.png")
        qimage.save(p)
        flags = 0x08000000 if IS_WINDOWS else 0
        r = subprocess.run([exe, p, "stdout", "-l", tl, "--psm", "6"], capture_output=True, creationflags=flags)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace"))
        return r.stdout.decode("utf-8", errors="replace")


def _fix_cjk_spacing(s: str) -> str:
    # "사 랑 해" -> "사랑해" only when the line looks like single syllables separated by spaces
    tokens = s.split(" ")
    if len(tokens) >= 3 and sum(len(t) == 1 for t in tokens) / len(tokens) > 0.7:
        return "".join(tokens)
    return s


# --------------------------------------------------------------------------
# Track list parsing
# --------------------------------------------------------------------------
_DURATION = re.compile(r"[\(\[]?\s*\d{1,2}\s*[:：'′.]\s*\d{2}\s*[\)\]]?\s*$")
_NUM = re.compile(r"^\s*(?:(?:track|tr\.?|disc\s*\d+\s*[-–]?)\s*)?(\d{1,2})\s*(?:[\.\)\]:\-–—_/|]+|\s)\s*(.+)$", re.I)
_TITLE_TAIL_JUNK = re.compile(r"[\s\.·…_\-–—]{2,}$")


def parse_tracklist(text: str) -> list[tuple[int | None, str]]:
    """Parse OCR text into [(track_no or None, title)].

    Handles "01. Title 3:45", "1 Title", "1) Title (feat. X)", dotted leaders, etc.
    """
    out: list[tuple[int | None, str]] = []
    for raw in text.splitlines():
        line = raw.strip().replace("\u3000", " ")
        if not line:
            continue
        line = _DURATION.sub("", line).strip()
        line = _TITLE_TAIL_JUNK.sub("", line).strip()
        m = _NUM.match(line)
        if m and 0 < int(m.group(1)) <= 99:
            title = m.group(2).strip(" .-–—:|")
            if title:
                out.append((int(m.group(1)), title))
                continue
        if out and out[-1][0] is not None and len(line) > 1 and not re.match(r"^\d", line):
            # continuation lines (subtitles, "feat." on next line) are kept as unnumbered
            out.append((None, line))
        elif len(line) > 1:
            out.append((None, line))
    return out


def assign_titles(parsed: list[tuple[int | None, str]], n_tracks: int) -> dict[int, str]:
    """Map parsed lines to track numbers. Numbered lines win; otherwise sequential."""
    numbered = {n: t for n, t in parsed if n is not None and 1 <= n <= n_tracks}
    if len(numbered) >= max(1, n_tracks // 2):
        return numbered
    seq = [t for _, t in parsed]
    return {i + 1: t for i, t in enumerate(seq[:n_tracks])}
