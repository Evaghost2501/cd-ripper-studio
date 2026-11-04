"""AccurateRip checksum calculation and database lookup."""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .toc import Toc

SKIP = 5 * 588  # first/last 5 sectors are excluded on first/last track


def checksums(pcm: bytes, is_first: bool, is_last: bool) -> tuple[int, int]:
    """AccurateRip v1 and v2 CRC of one track's PCM (16-bit stereo LE)."""
    samples = np.frombuffer(pcm, dtype="<u4").astype(np.uint64)
    n = len(samples)
    mult = np.arange(1, n + 1, dtype=np.uint64)
    lo_lim = SKIP if is_first else 1
    hi_lim = n - SKIP if is_last else n
    mask = (mult >= lo_lim) & (mult <= hi_lim)
    prod = samples[mask] * mult[mask]
    lo = prod & np.uint64(0xFFFFFFFF)
    hi = prod >> np.uint64(32)
    v1 = int(lo.sum(dtype=np.uint64)) & 0xFFFFFFFF
    v2 = (v1 + int(hi.sum(dtype=np.uint64))) & 0xFFFFFFFF
    return v1, v2


@dataclass
class ArEntry:
    confidence: int
    crc: int


def parse_db(data: bytes) -> list[list[ArEntry]]:
    """Returns per-track list of (confidence, crc) from all pressings in the DB file."""
    tracks: list[list[ArEntry]] = []
    pos = 0
    while pos + 13 <= len(data):
        count = data[pos]
        pos += 13
        for i in range(count):
            if pos + 9 > len(data):
                break
            conf, crc, _crc450 = struct.unpack_from("<BII", data, pos)
            pos += 9
            while len(tracks) <= i:
                tracks.append([])
            tracks[i].append(ArEntry(conf, crc))
    return tracks


def fetch(toc: Toc, session) -> list[list[ArEntry]] | None:
    try:
        r = session.get(toc.accuraterip_url(), timeout=10)
        if r.status_code != 200 or not r.content:
            return None
        return parse_db(r.content)
    except Exception:
        return None


def verify(db: list[list[ArEntry]] | None, index: int, v1: int, v2: int) -> tuple[str, int]:
    """Returns (status, confidence) – status in {'v2', 'v1', 'mismatch', 'notfound'}."""
    if not db or index >= len(db):
        return "notfound", 0
    best = ("mismatch", 0)
    for e in db[index]:
        if e.crc == v2 and e.confidence > best[1]:
            best = ("v2", e.confidence)
        elif e.crc == v1 and e.confidence > best[1]:
            best = ("v1", e.confidence)
    return best
