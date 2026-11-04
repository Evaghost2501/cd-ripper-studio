"""Run the real Windows drive code against a simulated kernel32 (disc swap → stale handle)."""
import ctypes
import importlib
import struct
import sys
import types

import pytest


class FakeKernel32:
    """Minimal kernel32: one CD drive D:, handles go stale when the disc is swapped."""

    def __init__(self):
        self.disc = 1             # which disc is inserted (None = empty)
        self.generation = 0       # bumps on every disc change; older handles become stale
        self.handles = {}
        self.next_h = 100
        self.opened = 0
        for name in ("CreateFileW", "DeviceIoControl", "GetDriveTypeW", "CloseHandle", "GetLogicalDrives",
                     "GetVolumeInformationW"):
            f = getattr(self, "_" + name)
            g = (lambda f: lambda *a: f(*a))(f)
            setattr(self, name, g)

    def swap_disc(self, disc):
        self.disc = disc
        self.generation += 1

    def _CreateFileW(self, path, *a):
        self.opened += 1
        h = self.next_h
        self.next_h += 1
        self.handles[h] = self.generation
        return h

    def _CloseHandle(self, h):
        self.handles.pop(h, None)
        return 1

    def _GetDriveTypeW(self, root):
        return 5 if root.startswith("D") else 3

    def _GetLogicalDrives(self):
        return 1 << 3

    def _GetVolumeInformationW(self, *a):
        return 0

    def _DeviceIoControl(self, h, code, in_ptr, in_len, out_ptr, outsize, returned, overlapped):
        if self.handles.get(h) != self.generation:
            ctypes.set_last_error(55)                 # ERROR_DEV_NOT_EXIST
            return 0
        if self.disc is None:
            ctypes.set_last_error(21)                 # ERROR_NOT_READY
            return 0
        if code == 0x00024000:                        # READ_TOC: disc N has N+1 tracks
            n = self.disc + 1
            toc = bytearray(804)
            toc[2], toc[3] = 1, n
            for i in range(n + 1):
                off = 4 + i * 8
                num = i + 1 if i < n else 0xAA
                lba = 150 + i * 75 * 60
                m, s, fr = lba // 4500, (lba // 75) % 60, lba % 75
                toc[off + 1], toc[off + 2] = 0x10, num
                toc[off + 5:off + 8] = bytes([m, s, fr])
            data = bytes(toc)
        else:
            data = b""
        if out_ptr and data:
            ctypes.memmove(out_ptr, data, min(len(data), outsize))
        returned._obj.value = min(len(data), outsize)
        return 1


@pytest.fixture
def windrive(monkeypatch):
    fake = FakeKernel32()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    last = [0]                                      # Windows-only ctypes error API, emulated
    monkeypatch.setattr(ctypes, "set_last_error", lambda v: last.__setitem__(0, v), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last[0], raising=False)
    import cdripper.core.drive as drive
    mod = importlib.reload(drive)
    yield mod, fake
    monkeypatch.undo()
    importlib.reload(drive)


def test_disc_swap_does_not_break_detection(windrive):
    drive, fake = windrive
    [d] = drive.list_drives()
    assert d.letter == "D"
    assert len(d.read_toc().tracks) == 2           # disc 1
    fake.swap_disc(None)                            # tray opened → old handle is stale
    assert d.media_present() is False               # reopened, then "not ready" = no disc
    fake.swap_disc(2)                               # new CD inserted → stale again
    assert d.media_present() is True                # this used to stay False forever (error 55)
    assert len(d.read_toc().tracks) == 3            # and the new TOC is read
    assert len(fake.handles) == 1                   # old handles were closed


def test_not_ready_then_ready_while_spinning_up(windrive, monkeypatch):
    drive, fake = windrive
    monkeypatch.setattr(drive.time, "sleep", lambda s: fake.swap_disc(4) if fake.disc is None else None)
    [d] = drive.list_drives()
    fake.swap_disc(None)
    assert len(d.read_toc().tracks) == 5            # waited for spin-up instead of failing
