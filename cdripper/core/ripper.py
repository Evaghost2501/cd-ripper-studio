"""Track extraction with read-offset correction and secure (re-read & compare) mode."""
from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .drive import BaseDrive, DriveError
from .toc import SAMPLES_PER_SECTOR, SECTOR_BYTES, Toc

CHUNK = 104  # sectors per verification chunk (~1.4 s of audio)


class Cancelled(Exception):
    pass


@dataclass
class RipResult:
    pcm: bytes
    crc32: int
    rereads: int = 0                    # chunks that needed extra reads
    unrecovered: list[int] = field(default_factory=list)   # LBAs of chunks that never agreed
    read_errors: int = 0


class TrackReader:
    def __init__(self, drive: BaseDrive, toc: Toc, offset: int = 0, retries: int = 5):
        self.drive, self.toc, self.offset, self.retries = drive, toc, offset, retries
        self.limit = toc.audio_leadout   # never read past the audio area

    def _read_raw(self, lba: int, count: int) -> tuple[bytes, bool]:
        """Read sectors, zero-filling anything outside the disc. Returns (data, error)."""
        lo, hi = max(lba, 0), min(lba + count, self.limit)
        pre = (lo - lba) * SECTOR_BYTES
        post = (lba + count - hi) * SECTOR_BYTES if hi > lo else 0
        if hi <= lo:
            return b"\0" * count * SECTOR_BYTES, False
        for attempt in range(self.retries + 1):
            try:
                data = self.drive.read_sectors(lo, hi - lo)
                if len(data) == (hi - lo) * SECTOR_BYTES:
                    return b"\0" * pre + data + b"\0" * post, False
            except DriveError:
                pass
        # give up on this block: read sector by sector to salvage what we can
        out = bytearray()
        for s in range(lo, hi):
            try:
                out += self.drive.read_sectors(s, 1)
            except DriveError:
                out += b"\0" * SECTOR_BYTES
        return b"\0" * pre + bytes(out) + b"\0" * post, True

    def _read_samples(self, first_sample: int, n_samples: int) -> tuple[bytes, bool]:
        """Read an arbitrary sample range (sample-accurate, used for offset correction)."""
        first_lba = first_sample // SAMPLES_PER_SECTOR
        skip = (first_sample - first_lba * SAMPLES_PER_SECTOR) * 4
        n_sectors = -(-(skip + n_samples * 4) // SECTOR_BYTES)
        raw, err = self._read_raw(first_lba, n_sectors)
        return raw[skip:skip + n_samples * 4], err

    def rip(self, track_no: int, secure: bool, progress: Callable[[float], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False, verified: Callable[[bytes], bool] | None = None
            ) -> RipResult:
        t = self.toc.track(track_no)
        base = t.start * SAMPLES_PER_SECTOR + self.offset
        total = t.length * SAMPLES_PER_SECTOR
        chunk_samples = CHUNK * SAMPLES_PER_SECTOR
        chunks: list[bytes] = []
        errors = 0
        passes = 2 if secure else 1

        # pass 1
        for pos in range(0, total, chunk_samples):
            if cancelled():
                raise Cancelled
            data, err = self._read_samples(base + pos, min(chunk_samples, total - pos))
            chunks.append(data)
            errors += err
            if progress:
                progress(pos / total / passes)

        pcm = b"".join(chunks)
        result = RipResult(pcm, zlib.crc32(pcm), read_errors=errors)
        # AccurateRip match → no need to re-read
        if not secure or (verified and verified(pcm)):
            if progress:
                progress(1.0)
            return result

        # pass 2: re-read and compare every chunk, majority vote on mismatch
        for i, pos in enumerate(range(0, total, chunk_samples)):
            if cancelled():
                raise Cancelled
            n = min(chunk_samples, total - pos)
            again, _ = self._read_samples(base + pos, n)
            if again != chunks[i]:
                result.rereads += 1
                votes = Counter([zlib.crc32(chunks[i]), zlib.crc32(again)])
                samples = {zlib.crc32(chunks[i]): chunks[i], zlib.crc32(again): again}
                for _ in range(self.retries * 2):
                    d, _ = self._read_samples(base + pos, n)
                    c = zlib.crc32(d)
                    votes[c] += 1
                    samples[c] = d
                    if votes[c] >= 3:
                        break
                crc, cnt = votes.most_common(1)[0]
                chunks[i] = samples[crc]
                if cnt < 3:
                    result.unrecovered.append(t.start + pos // SAMPLES_PER_SECTOR)
            if progress:
                progress(0.5 + pos / total / 2)

        pcm = b"".join(chunks)
        result.pcm, result.crc32 = pcm, zlib.crc32(pcm)
        if progress:
            progress(1.0)
        return result
