"""Whole-disc rip job. Pure Python with callbacks so it can run in a QThread."""
from __future__ import annotations

import datetime
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .. import __version__
from . import accuraterip, encoders, naming, tagger
from .drive import BaseDrive
from .metadata import AlbumMeta, track_values
from .ripper import Cancelled, TrackReader
from .settings import Settings
from .toc import Toc, fmt_duration


@dataclass
class TrackReport:
    number: int
    path: str = ""
    crc32: int = 0
    ar_status: str = ""
    ar_conf: int = 0
    rereads: int = 0
    unrecovered: int = 0
    error: str = ""


@dataclass
class JobReport:
    tracks: list[TrackReport] = field(default_factory=list)
    folder: str = ""
    log_path: str = ""
    cancelled: bool = False


class RipJob:
    def __init__(self, drive: BaseDrive, toc: Toc, album: AlbumMeta, cover: bytes | None, s: Settings,
                 on_progress: Callable[[int, float, str], None] = lambda *a: None,
                 on_track_done: Callable[[TrackReport], None] = lambda r: None,
                 on_log: Callable[[str], None] = print):
        self.drive, self.toc, self.album, self.cover, self.s = drive, toc, album, cover, s
        self.on_progress, self.on_track_done, self.log = on_progress, on_track_done, on_log
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def output_path(self, track, fmt: str | None = None) -> str:
        return output_path(self.s, self.album, track, fmt)

    def run(self) -> JobReport:
        s, album = self.s, self.album
        report = JobReport()
        selected = [t for t in album.tracks if t.rip]
        if not selected:
            return report
        ffmpeg = encoders.find_ffmpeg(s.ffmpeg_path)
        cover = tagger.prepare_cover(self.cover, s.cover_max_px) if (self.cover and s.embed_cover) else None
        formats = [s.format] + (["flac"] if s.extra_flac and s.format != "flac" else [])

        ar_db = None
        if s.accuraterip:
            self.log("AccurateRip 데이터베이스 조회 중…")
            ar_db = accuraterip.fetch(self.toc, encoders_session())
            self.log("AccurateRip: " + (f"{max((len(x) for x in ar_db), default=0)}개 프레싱 정보 발견"
                                        if ar_db else "이 디스크 정보 없음 (검증 생략)"))

        reader = TrackReader(self.drive, self.toc, offset=s.read_offset)
        audio = self.toc.audio_tracks
        all_tracks = self.toc.tracks
        pool = ThreadPoolExecutor(max_workers=2)
        futures = []

        try:
            for i, tm in enumerate(selected):
                if self._cancel:
                    raise Cancelled
                tt = self.toc.track(tm.number)
                idx = all_tracks.index(tt)
                is_first, is_last = tt is audio[0], tt is audio[-1]
                rep = TrackReport(tm.number)
                self.log(f"트랙 {tm.number:02d} 읽는 중… ({fmt_duration(tt.length)})")
                ar_result = {}

                def verified(pcm, idx=idx, is_first=is_first, is_last=is_last, ar_result=ar_result):
                    v1, v2 = accuraterip.checksums(pcm, is_first, is_last)
                    st, conf = accuraterip.verify(ar_db, idx, v1, v2)
                    ar_result.update(st=st, conf=conf, v1=v1, v2=v2)
                    return st in ("v1", "v2")

                res = reader.rip(tm.number, s.secure_mode,
                                 progress=lambda f, i=i, n=tm.number: self.on_progress(
                                     n, (i + f * 0.9) / len(selected), "읽는 중"),
                                 cancelled=lambda: self._cancel,
                                 verified=verified if ar_db else None)
                if ar_db and (s.secure_mode is False or not ar_result or res.rereads):
                    verified(res.pcm)
                rep.crc32, rep.rereads, rep.unrecovered = res.crc32, res.rereads, len(res.unrecovered)
                rep.ar_status, rep.ar_conf = ar_result.get("st", "off" if not s.accuraterip else "notfound"), \
                    ar_result.get("conf", 0)

                def finish(pcm=res.pcm, tm=tm, rep=rep):
                    try:
                        for fmt in formats:
                            path = self.output_path(tm, fmt)
                            if fmt != s.format:   # archive copy goes into a sibling "FLAC" tree
                                path = os.path.join(s.output_dir, "_FLAC",
                                                    os.path.relpath(path, s.output_dir))
                            q = s.quality if fmt == s.format else "8"
                            encoders.encode(pcm, fmt, q, path, ffmpeg)
                            tagger.write_tags(path, fmt, album, tm, cover, s.id3v23)
                            if fmt == s.format:
                                rep.path = path
                    except Exception as e:  # noqa: BLE001
                        rep.error = str(e)
                    self.on_track_done(rep)
                    return rep

                futures.append(pool.submit(finish))
                report.tracks.append(rep)
                self.log(f"트랙 {tm.number:02d} 읽기 완료  CRC {rep.crc32:08X}  {ar_text(rep)}"
                         + (f"  재독취 {rep.rereads}구간" if rep.rereads else "")
                         + (f"  ⚠ 복구 불가 {rep.unrecovered}구간" if rep.unrecovered else ""))
        except Cancelled:
            report.cancelled = True
            self.log("사용자가 취소했습니다.")
        finally:
            for f in futures:
                f.result()
            pool.shutdown()

        done = [r for r in report.tracks if r.path]
        if done:
            report.folder = os.path.dirname(done[0].path)
            self._write_extras(report, done)
        self.on_progress(0, 1.0, "완료")
        return report

    # ------------------------------------------------------------------
    def _write_extras(self, report: JobReport, done: list[TrackReport]):
        s, album, folder = self.s, self.album, report.folder
        if self.cover and s.save_cover_file:
            ext = ".png" if self.cover[:4] == b"\x89PNG" else ".jpg"
            with open(os.path.join(folder, "folder" + ext), "wb") as f:
                f.write(self.cover)
        base = naming.sanitize(f"{album.albumartist} - {album.album}" if album.album else "album")
        if s.write_m3u:
            with open(os.path.join(folder, base + ".m3u8"), "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for r in done:
                    tm = next(t for t in album.tracks if t.number == r.number)
                    v = track_values(album, tm)
                    f.write(f"#EXTINF:{tm.length // 75},{v['artist']} - {v['title']}\n")
                    f.write(os.path.relpath(r.path, folder) + "\n")
        if s.write_cue:
            q = lambda x: str(x).replace('"', "'")  # noqa: E731
            lines = [f'REM GENRE "{q(album.genre)}"', f"REM DATE {album.year}",
                     f"REM DISCID {self.toc.freedb_id():08X}", f"REM COMMENT \"CD Ripper Studio {__version__}\"",
                     f'PERFORMER "{q(album.albumartist)}"', f'TITLE "{q(album.album)}"']
            ftype = {"mp3": "MP3", "wav": "WAVE"}.get(s.format, "WAVE")
            for r in done:
                tm = next(t for t in album.tracks if t.number == r.number)
                v = track_values(album, tm)
                lines += [f'FILE "{os.path.basename(r.path)}" {ftype}', f"  TRACK {r.number:02d} AUDIO",
                          f'    TITLE "{q(v["title"])}"', f'    PERFORMER "{q(v["artist"])}"']
                if tm.isrc:
                    lines.append(f"    ISRC {tm.isrc}")
                lines.append("    INDEX 01 00:00:00")
            with open(os.path.join(folder, base + ".cue"), "w", encoding="utf-8-sig") as f:
                f.write("\n".join(lines) + "\n")
        if s.write_log:
            report.log_path = os.path.join(folder, base + ".log")
            with open(report.log_path, "w", encoding="utf-8") as f:
                f.write(self._log_text(report))

    def _log_text(self, report: JobReport) -> str:
        s, a = self.s, self.album
        fi = encoders.FORMAT_BY_KEY[s.format]
        q = dict(fi.qualities).get(s.quality, s.quality)
        out = [f"CD Ripper Studio {__version__} 추출 로그  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", "",
               f"드라이브        : {self.drive}", f"읽기 오프셋    : {s.read_offset:+d} 샘플",
               f"모드           : {'보안 (재독취 비교)' if s.secure_mode else '빠름 (버스트)'}",
               f"출력 포맷      : {fi.name} / {q}", "",
               f"앨범 아티스트  : {a.albumartist}", f"앨범           : {a.album}", f"연도           : {a.date}",
               f"MusicBrainz ID : {self.toc.musicbrainz_discid()}", f"AccurateRip URL: {self.toc.accuraterip_url()}",
               "", " 트랙  길이   CRC32     AccurateRip            재독취  파일", "-" * 90]
        for r in report.tracks:
            tm = next(t for t in a.tracks if t.number == r.number)
            out.append(f"  {r.number:02d}  {fmt_duration(tm.length):>5}  {r.crc32:08X}  {ar_text(r):<22} "
                       f"{r.rereads:>4}    {os.path.basename(r.path) if r.path else '실패: ' + r.error}")
        ok = all(r.ar_status in ("v1", "v2") for r in report.tracks)
        bad = [r.number for r in report.tracks if r.unrecovered or r.error]
        out += ["", "결과: " + ("모든 트랙이 AccurateRip으로 정확히 검증되었습니다." if ok else
                              (f"문제 트랙: {bad}" if bad else "오류 없이 추출되었습니다 (AccurateRip 미검증 트랙 있음)."))]
        if report.cancelled:
            out.append("※ 취소됨")
        return "\n".join(out) + "\n"


def output_path(s: Settings, album: AlbumMeta, track, fmt: str | None = None) -> str:
    """<저장 폴더>/[<앨범 이름>/]<파일 이름 규칙>.<확장자>"""
    fmt = fmt or s.format
    tpl = s.various_template if album.compilation else s.template
    rel = naming.render(tpl, track_values(album, track))
    if s.album_folder:
        rel = naming.sanitize(album.album.strip() or "앨범 이름 없음") + "/" + rel
    return os.path.join(s.output_dir, *rel.split("/")) + encoders.FORMAT_BY_KEY[fmt].ext


def ar_text(r: TrackReport) -> str:
    return {"v2": f"정확함 v2 (신뢰도 {r.ar_conf})", "v1": f"정확함 v1 (신뢰도 {r.ar_conf})",
            "mismatch": "불일치", "notfound": "DB에 없음", "off": "사용 안 함"}.get(r.ar_status, r.ar_status)


def encoders_session():
    from ..sources.web import session
    return session
