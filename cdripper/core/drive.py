"""Optical drive access.

Windows: raw CD-DA reading through DeviceIoControl (IOCTL_CDROM_RAW_READ),
no external tools or admin rights needed.
Other OS / testing: set env CDRIPPER_FAKE=1 to get a simulated drive.
"""
from __future__ import annotations

import math
import os
import struct
import sys

from .toc import SECTOR_BYTES, Toc, TocTrack

IS_WINDOWS = sys.platform == "win32"
MAX_SECTORS_PER_READ = 26        # 26*2352 = 61 152 bytes (< 64 KiB driver limit)


class DriveError(Exception):
    pass


class BaseDrive:
    letter: str = ""
    label: str = ""

    def read_toc(self) -> Toc: ...
    def read_sectors(self, lba: int, count: int) -> bytes: ...
    def read_cdtext(self) -> dict: return {}
    def media_present(self) -> bool: return True
    def eject(self): ...
    def close_tray(self): ...
    def close(self): ...

    def __str__(self):
        return self.label or self.letter


# ---------------------------------------------------------------------------
# Windows implementation
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetLogicalDrives.restype = wintypes.DWORD
    k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
                                          wintypes.LPWSTR, wintypes.DWORD]

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
    OPEN_EXISTING = 3
    INVALID_HANDLE = wintypes.HANDLE(-1).value
    DRIVE_CDROM = 5

    IOCTL_CDROM_READ_TOC = 0x00024000
    IOCTL_CDROM_READ_TOC_EX = 0x00024054
    IOCTL_CDROM_RAW_READ = 0x0002403E
    IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
    IOCTL_STORAGE_LOAD_MEDIA = 0x002D480C
    IOCTL_STORAGE_CHECK_VERIFY2 = 0x002D0800
    CDDA = 2
    TOC_EX_FORMAT_CDTEXT = 5

    class WinDrive(BaseDrive):
        def __init__(self, letter: str):
            self.letter = letter.upper().rstrip(":\\")
            self.handle = None
            name = ctypes.create_unicode_buffer(261)
            k32.GetVolumeInformationW(f"{self.letter}:\\", name, 261, None, None, None, None, 0)
            self.label = f"{self.letter}:" + (f"  [{name.value}]" if name.value else "")

        def _open(self):
            if self.handle:
                return self.handle
            h = k32.CreateFileW(f"\\\\.\\{self.letter}:", GENERIC_READ,
                                FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if h == INVALID_HANDLE or h is None:
                raise DriveError(f"드라이브 {self.letter}: 를 열 수 없습니다 (오류 {ctypes.get_last_error()})")
            self.handle = h
            return h

        def _ioctl(self, code, inbuf=None, outsize=0):
            h = self._open()
            out = ctypes.create_string_buffer(outsize) if outsize else None
            returned = wintypes.DWORD(0)
            in_ptr = ctypes.cast(inbuf, wintypes.LPVOID) if inbuf is not None else None
            in_len = ctypes.sizeof(inbuf) if inbuf is not None else 0
            out_ptr = ctypes.cast(out, wintypes.LPVOID) if out is not None else None
            ok = k32.DeviceIoControl(h, code, in_ptr, in_len, out_ptr, outsize, ctypes.byref(returned), None)
            if not ok:
                raise DriveError(f"DeviceIoControl 0x{code:08X} 실패 (오류 {ctypes.get_last_error()})")
            return out.raw[:returned.value] if out is not None else b""

        def read_toc(self) -> Toc:
            raw = self._ioctl(IOCTL_CDROM_READ_TOC, outsize=804)
            if len(raw) < 4:
                raise DriveError("TOC를 읽을 수 없습니다. 오디오 CD가 들어있는지 확인하세요.")
            first, last = raw[2], raw[3]
            tracks, leadout = [], None
            for i in range(last - first + 2):
                off = 4 + i * 8
                ctrl_adr, num = raw[off + 1], raw[off + 2]
                m, s, f = raw[off + 5], raw[off + 6], raw[off + 7]
                lba = (m * 60 + s) * 75 + f - 150
                if num == 0xAA:
                    leadout = lba
                else:
                    tracks.append(TocTrack(num, lba, is_audio=not (ctrl_adr & 0x04)))
            if leadout is None or not any(t.is_audio for t in tracks):
                raise DriveError("오디오 트랙이 없습니다.")
            return Toc(first, last, leadout, tracks)

        def read_sectors(self, lba: int, count: int) -> bytes:
            out = bytearray()
            while count > 0:
                n = min(count, MAX_SECTORS_PER_READ)
                info = ctypes.create_string_buffer(struct.pack("<qII", lba * 2048, n, CDDA), 16)
                out += self._ioctl(IOCTL_CDROM_RAW_READ, info, n * SECTOR_BYTES)
                lba += n
                count -= n
            return bytes(out)

        def read_cdtext(self) -> dict:
            try:
                req = ctypes.create_string_buffer(bytes([TOC_EX_FORMAT_CDTEXT, 0, 0, 0]), 4)
                head = self._ioctl(IOCTL_CDROM_READ_TOC_EX, req, 4)
                length = struct.unpack(">H", head[:2])[0] + 2
                raw = self._ioctl(IOCTL_CDROM_READ_TOC_EX, req, length)
                return parse_cdtext(raw[4:])
            except DriveError:
                return {}

        def media_present(self) -> bool:
            try:
                self._ioctl(IOCTL_STORAGE_CHECK_VERIFY2)
                return True
            except DriveError:
                return False

        def eject(self):
            self._ioctl(IOCTL_STORAGE_EJECT_MEDIA)
            self.close()

        def close_tray(self):
            self._ioctl(IOCTL_STORAGE_LOAD_MEDIA)

        def close(self):
            if self.handle:
                k32.CloseHandle(self.handle)
                self.handle = None


def parse_cdtext(packs: bytes) -> dict:
    """Parse CD-Text packs → {'album': .., 'albumartist': .., 'tracks': {n: {'title', 'artist'}}}."""
    buffers: dict[int, dict[int, bytearray]] = {0x80: {}, 0x81: {}}
    for i in range(0, len(packs) - 17, 18):
        p = packs[i:i + 18]
        ptype, track, block = p[0], p[1] & 0x7F, (p[3] >> 4) & 0x07
        if ptype not in buffers or block != 0:
            continue
        data = p[4:16]
        # text chunks may start mid-string for a track number; walk null separators
        cur = track
        for b in data:
            if b == 0:
                cur += 1
                continue
            buffers[ptype].setdefault(cur, bytearray()).append(b)

    def dec(b: bytearray) -> str:
        for enc in ("utf-8", "cp949", "latin-1"):
            try:
                return b.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return ""

    res: dict = {"tracks": {}}
    for ptype, key in ((0x80, "title"), (0x81, "artist")):
        for tno, b in buffers[ptype].items():
            text = dec(b)
            if text in ("", "\t"):
                continue
            if tno == 0:
                res["album" if key == "title" else "albumartist"] = text
            else:
                res["tracks"].setdefault(tno, {})[key] = text
    return res if (res.get("album") or res["tracks"]) else {}


# ---------------------------------------------------------------------------
# Fake drive for development / tests (no hardware)
# ---------------------------------------------------------------------------
class FakeDrive(BaseDrive):
    """Simulated 4-track disc with sine tones. Enabled with CDRIPPER_FAKE=1."""

    def __init__(self):
        self.letter = "Z"
        self.label = "Z:  [가상 테스트 CD]"
        starts = [0, 75 * 20, 75 * 35, 75 * 52]
        self._toc = Toc(1, 4, 75 * 70, [TocTrack(i + 1, s) for i, s in enumerate(starts)])

    def read_toc(self):
        return self._toc

    def read_sectors(self, lba, count):
        import numpy as np
        start = lba * 588
        n = np.arange(start, start + count * 588)
        trk = next(t for t in reversed(self._toc.tracks) if lba >= t.start)
        freq = 220.0 * (2 ** ((trk.number - 1) / 3))
        left = (np.sin(2 * math.pi * freq * n / 44100) * 8000).astype("<i2")
        right = (np.sin(2 * math.pi * freq * 1.5 * n / 44100) * 6000).astype("<i2")
        return np.column_stack([left, right]).tobytes()

    def read_cdtext(self):
        return {"album": "Test Album", "albumartist": "Test Artist",
                "tracks": {1: {"title": "First Song"}, 2: {"title": "Second Song"}}}


def list_drives() -> list[BaseDrive]:
    drives: list[BaseDrive] = []
    if os.environ.get("CDRIPPER_FAKE"):
        drives.append(FakeDrive())
    if IS_WINDOWS:
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(ord("A") + i)
                if k32.GetDriveTypeW(f"{letter}:\\") == DRIVE_CDROM:
                    drives.append(WinDrive(letter))
    return drives
