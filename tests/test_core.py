import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cdripper.core import accuraterip, naming, ocr
from cdripper.core.drive import FakeDrive, parse_cdtext
from cdripper.core.metadata import AlbumMeta, TrackMeta, track_values
from cdripper.core.ripper import TrackReader
from cdripper.core.toc import Toc, TocTrack


def test_musicbrainz_discid_reference():
    # example from https://musicbrainz.org/doc/Disc_ID_Calculation
    offs = [150, 15363, 32314, 46592, 63414, 80489]
    toc = Toc(1, 6, 95462 - 150, [TocTrack(i + 1, o - 150) for i, o in enumerate(offs)])
    assert toc.musicbrainz_discid() == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-"


def test_enhanced_cd_excludes_data_track():
    tracks = [TocTrack(1, 0), TocTrack(2, 20000), TocTrack(3, 50000, is_audio=False)]
    toc = Toc(1, 3, 80000, tracks)
    assert toc.audio_leadout == 50000 - 11400
    assert toc.track(2).length == 50000 - 11400 - 20000
    assert toc.musicbrainz_toc().startswith("1+2+38750+")


def test_accuraterip_checksum_matches_reference_loop():
    rng = np.random.default_rng(1)
    pcm = rng.integers(-30000, 30000, size=(588 * 20, 2), dtype=np.int16).tobytes()
    words = np.frombuffer(pcm, "<u4")
    for first, last in ((True, False), (False, True), (False, False), (True, True)):
        v1 = v2 = 0
        lo = 5 * 588 if first else 1
        hi = len(words) - 5 * 588 if last else len(words)
        for i, w in enumerate(words, 1):
            if lo <= i <= hi:
                p = int(w) * i
                v1 = (v1 + p) & 0xFFFFFFFF
                v2 = (v2 + (p & 0xFFFFFFFF) + (p >> 32)) & 0xFFFFFFFF
        assert accuraterip.checksums(pcm, first, last) == (v1, v2)


def test_accuraterip_db_parse_and_verify():
    import struct
    blob = struct.pack("<BIII", 2, 1, 2, 3) + struct.pack("<BII", 7, 0xAAAA, 0) + struct.pack("<BII", 3, 0xBBBB, 0)
    db = accuraterip.parse_db(blob)
    assert accuraterip.verify(db, 0, 0, 0xAAAA) == ("v2", 7)
    assert accuraterip.verify(db, 1, 0xBBBB, 0) == ("v1", 3)
    assert accuraterip.verify(db, 1, 1, 2)[0] == "mismatch"


def test_naming_template():
    a = AlbumMeta(albumartist="아이유", album="Palette", date="2017-04-21",
                  tracks=[TrackMeta(3, title="밤편지: Night Letter?"), TrackMeta(4)])
    v = track_values(a, a.tracks[0])
    assert naming.render("{albumartist}/[{year} - ]{album}/{track:02}. {title}", v) == \
        "아이유/2017 - Palette/03. 밤편지： Night Letter？"
    a.date = ""
    assert naming.render("{albumartist}/[{year} - ]{album}/{track:02}", track_values(a, a.tracks[0])) == \
        "아이유/Palette/03"
    assert naming.render("{album}/{title}", {"album": "AC/DC", "title": "CON"}) == "AC_DC/_CON"


def test_ocr_parse():
    text = """TRACK LIST
01. 이 지금 dlwlrma 3:45
2) 팔레트 (Feat. G-DRAGON)   (3:37)
03 이런 엔딩 ........ 3:03
4 - Black Out
"""
    p = ocr.parse_tracklist(text)
    m = ocr.assign_titles(p, 4)
    assert m == {1: "이 지금 dlwlrma", 2: "팔레트 (Feat. G-DRAGON)", 3: "이런 엔딩", 4: "Black Out"}
    assert ocr.assign_titles(ocr.parse_tracklist("Song A\nSong B\n"), 2) == {1: "Song A", 2: "Song B"}


def test_cdtext_parse():
    def pack(t, trk, text12):
        return bytes([t, trk, 0, 0]) + text12.ljust(12, b"\0")[:12] + b"\0\0"
    data = pack(0x80, 0, b"Album\0Song1\0") + pack(0x80, 2, b"Song2\0") + pack(0x81, 0, b"Artist\0")
    r = parse_cdtext(data)
    assert r["album"] == "Album" and r["albumartist"] == "Artist"
    assert r["tracks"][1]["title"] == "Song1" and r["tracks"][2]["title"] == "Song2"


def test_offset_reading_is_sample_accurate():
    d = FakeDrive()
    toc = d.read_toc()
    plain = TrackReader(d, toc, 0)._read_samples(1000, 3000)[0]
    shifted = TrackReader(d, toc, 0)._read_samples(1006, 3000)[0]
    assert plain[6 * 4:] == shifted[:-6 * 4]
    # reading before the disc start is zero-filled
    pre, _ = TrackReader(d, toc, 0)._read_samples(-10, 20)
    assert pre[:40] == b"\0" * 40


@pytest.mark.parametrize("fmt,quality", [("mp3", "cbr320"), ("mp3", "v0"), ("flac", "8"), ("wav", "default"),
                                         ("opus", "0.9"), ("aac", "256"), ("alac", "default")])
def test_full_rip_job(tmp_path, fmt, quality):
    from PySide6.QtGui import QGuiApplication, QImage, QColor
    _app = QGuiApplication.instance() or QGuiApplication([])
    from cdripper.core import encoders
    from cdripper.core.job import RipJob
    from cdripper.core.settings import Settings
    import mutagen

    if encoders.FORMAT_BY_KEY[fmt].needs_ffmpeg and not encoders.find_ffmpeg():
        pytest.skip("ffmpeg missing")
    d = FakeDrive()
    toc = d.read_toc()
    album = AlbumMeta(albumartist="Test Artist", album="Test: Album", date="2024", genre="Pop",
                      tracks=[TrackMeta(t.number, title=f"Song {t.number}", length=t.length) for t in toc.tracks])
    album.tracks[2].rip = False
    img = QImage(1600, 1600, QImage.Format_RGB32)
    img.fill(QColor("red"))
    from cdripper.gui.cover_dialog import image_bytes_from_qimage
    cover = image_bytes_from_qimage(img)
    s = Settings(output_dir=str(tmp_path), format=fmt, quality=quality, accuraterip=False, secure_mode=True,
                 extra_flac=(fmt == "mp3"))
    logs = []
    rep = RipJob(d, toc, album, cover, s, on_log=logs.append).run()
    assert [r.number for r in rep.tracks] == [1, 2, 4]
    assert all(r.path and not r.error for r in rep.tracks), [r.error for r in rep.tracks]
    f = mutagen.File(rep.tracks[0].path)
    assert f is not None and f.info.length == pytest.approx(20, abs=0.2)
    if fmt == "mp3":
        assert str(f.tags["TIT2"]) == "Song 1" and str(f.tags["TRCK"]) == "1/4"
        apic = f.tags.getall("APIC")[0]
        assert QImage.fromData(apic.data).width() == 1000       # resized for embedding
        assert os.path.exists(os.path.join(tmp_path, "_FLAC", "Test： Album", "01. Song 1.flac"))
        assert rep.tracks[0].path == os.path.join(tmp_path, "Test： Album", "01. Song 1.mp3")
    if fmt == "flac":
        assert f["TITLE"] == ["Song 1"] and f.pictures
        import soundfile as sf
        data, _ = sf.read(rep.tracks[0].path, dtype="int16")
        assert data.tobytes() == TrackReader(d, toc, 0).rip(1, False).pcm   # bit-exact
    folder = rep.folder
    names = os.listdir(folder)
    assert "folder.jpg" in names and any(n.endswith(".cue") for n in names) and any(n.endswith(".log") for n in names)


def test_secure_mode_recovers_flaky_reads():
    import random

    class Flaky(FakeDrive):
        def __init__(self):
            super().__init__()
            self.rng = random.Random(3)

        def read_sectors(self, lba, count):
            data = bytearray(super().read_sectors(lba, count))
            if self.rng.random() < 0.3:           # 30% of reads get a corrupted byte
                data[self.rng.randrange(len(data))] ^= 0xFF
            return bytes(data)

    good = TrackReader(FakeDrive(), FakeDrive().read_toc()).rip(2, False).pcm
    d = Flaky()
    res = TrackReader(d, d.read_toc()).rip(2, True)
    assert res.rereads > 0 and not res.unrecovered
    assert res.pcm == good


def test_stale_handle_is_reopened_once():
    from cdripper.core.drive import DriveError, call_with_recovery
    calls, reopens = [], []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise DriveError("stale", 55)       # ERROR_DEV_NOT_EXIST after a disc change
        return "toc"
    assert call_with_recovery(call, lambda: reopens.append(1)) == "toc"
    assert len(calls) == 2 and len(reopens) == 1


def test_stale_handle_gives_up_after_one_reopen():
    from cdripper.core.drive import DriveError, call_with_recovery

    def call():
        raise DriveError("stale", 55)
    with pytest.raises(DriveError):
        call_with_recovery(call, lambda: None)


def test_not_ready_waits_for_spin_up():
    from cdripper.core.drive import DriveError, call_with_recovery
    n = [0]

    def call():
        n[0] += 1
        if n[0] < 4:
            raise DriveError("not ready", 21)
        return "ok"
    assert call_with_recovery(call, lambda: None, retries_not_ready=5, wait=0) == "ok"
    with pytest.raises(DriveError):            # without retries "no disc" is reported immediately
        n[0] = 0
        call_with_recovery(call, lambda: None, retries_not_ready=0, wait=0)


def test_error_text_korean():
    from cdripper.core.drive import describe_error
    assert "끊겼" in describe_error(55) and "[코드 55]" in describe_error(55)
