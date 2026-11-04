"""Prepend a Xing VBR header (frame count, byte count, seek TOC) to a raw
MPEG-1 Layer III stream so players show the right duration and seek correctly."""
from __future__ import annotations

import struct

_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_RATES = [44100, 48000, 32000, 0]


def _frames(data: bytes):
    """Yield (offset, length) of each MPEG-1 Layer III frame."""
    pos, n = 0, len(data)
    while pos + 4 <= n:
        h = struct.unpack_from(">I", data, pos)[0]
        if (h >> 21) & 0x7FF != 0x7FF or (h >> 19) & 3 != 3 or (h >> 17) & 3 != 1:
            pos += 1          # resync
            continue
        br = _BITRATES[(h >> 12) & 0xF] * 1000
        sr = _RATES[(h >> 10) & 3]
        if not br or not sr:
            pos += 1
            continue
        length = 144 * br // sr + ((h >> 9) & 1)
        yield pos, length
        pos += length


def add_xing_header(data: bytes) -> bytes:
    frames = list(_frames(data))
    if not frames:
        return data
    first = struct.unpack_from(">I", data, frames[0][0])[0]
    sr_idx = (first >> 10) & 3
    mono = ((first >> 6) & 3) == 3
    # 128 kbps frame (417 bytes @ 44.1 kHz) is big enough to hold the Xing payload
    hdr = (0xFFFB << 16) | (9 << 12) | (sr_idx << 10) | (first & 0xC0)   # MPEG1 L3, no CRC, no padding
    frame_len = 144 * 128000 // _RATES[sr_idx]
    side = 17 if mono else 32
    total_bytes = len(data) + frame_len
    n = len(frames) + 1
    toc = bytearray(100)
    for i in range(100):
        idx = min(int(i / 100 * len(frames)), len(frames) - 1)
        toc[i] = min(255, int((frames[idx][0] + frame_len) * 256 / total_bytes))
    payload = b"Xing" + struct.pack(">III", 0x7, n, total_bytes) + bytes(toc)
    frame = struct.pack(">I", hdr) + b"\0" * side + payload
    frame += b"\0" * (frame_len - len(frame))
    return frame + data
