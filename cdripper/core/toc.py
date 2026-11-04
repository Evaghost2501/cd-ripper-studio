"""CD Table of Contents and disc identifiers (MusicBrainz, AccurateRip, FreeDB)."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field

SECTOR_BYTES = 2352          # one CD-DA sector (1/75 s)
SAMPLES_PER_SECTOR = 588     # stereo 16-bit samples per sector
FRAMES_PER_SEC = 75
PREGAP = 150                 # 2-second lead-in offset used by MSF addressing
DATA_TRACK_GAP = 11400       # gap between sessions on Enhanced CDs


@dataclass
class TocTrack:
    number: int
    start: int          # LBA (sector) of track start
    is_audio: bool = True
    length: int = 0     # sectors, filled by Toc


@dataclass
class Toc:
    first: int
    last: int
    leadout: int                      # LBA of lead-out
    tracks: list[TocTrack] = field(default_factory=list)

    def __post_init__(self):
        for i, t in enumerate(self.tracks):
            nxt = self.tracks[i + 1].start if i + 1 < len(self.tracks) else self.leadout
            t.length = nxt - t.start
        # On Enhanced CDs the last audio track ends before the session gap.
        audio = self.audio_tracks
        if audio and audio[-1] is not self.tracks[-1]:
            data = self.tracks[self.tracks.index(audio[-1]) + 1]
            audio[-1].length = data.start - DATA_TRACK_GAP - audio[-1].start

    @property
    def audio_tracks(self) -> list[TocTrack]:
        return [t for t in self.tracks if t.is_audio]

    @property
    def audio_leadout(self) -> int:
        """End of the last audio track (differs from leadout on Enhanced CDs)."""
        a = self.audio_tracks
        return a[-1].start + a[-1].length if a else self.leadout

    def track(self, number: int) -> TocTrack:
        for t in self.tracks:
            if t.number == number:
                return t
        raise KeyError(number)

    @property
    def total_seconds(self) -> float:
        return self.audio_leadout / FRAMES_PER_SEC

    # ---------------- identifiers ----------------
    def musicbrainz_discid(self) -> str:
        audio = self.audio_tracks
        first, last = audio[0].number, audio[-1].number
        offsets = [0] * 100
        offsets[0] = self.audio_leadout + PREGAP
        for t in audio:
            offsets[t.number] = t.start + PREGAP
        s = f"{first:02X}{last:02X}" + "".join(f"{o:08X}" for o in offsets)
        digest = hashlib.sha1(s.encode("ascii")).digest()
        return base64.b64encode(digest).decode().replace("+", ".").replace("/", "_").replace("=", "-")

    def musicbrainz_toc(self) -> str:
        audio = self.audio_tracks
        parts = [audio[0].number, audio[-1].number, self.audio_leadout + PREGAP]
        parts += [t.start + PREGAP for t in audio]
        return "+".join(str(p) for p in parts)

    def freedb_id(self) -> int:
        def digit_sum(n: int) -> int:
            return sum(int(c) for c in str(n))
        n = sum(digit_sum((t.start + PREGAP) // FRAMES_PER_SEC) for t in self.tracks)
        total = (self.leadout + PREGAP) // FRAMES_PER_SEC - (self.tracks[0].start + PREGAP) // FRAMES_PER_SEC
        return ((n % 255) << 24) | (total << 8) | len(self.tracks)

    def accuraterip_ids(self) -> tuple[int, int, int, int]:
        """Returns (track_count, id1, id2, freedb_id) as used by the AccurateRip DB."""
        id1 = id2 = 0
        for i, t in enumerate(self.tracks, 1):
            id1 += t.start
            id2 += max(t.start, 1) * i
        id1 += self.leadout
        id2 += self.leadout * (len(self.tracks) + 1)
        return len(self.tracks), id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, self.freedb_id()

    def accuraterip_url(self) -> str:
        n, id1, id2, cddb = self.accuraterip_ids()
        return ("http://www.accuraterip.com/accuraterip/"
                f"{id1 & 0xF:x}/{(id1 >> 4) & 0xF:x}/{(id1 >> 8) & 0xF:x}/"
                f"dBAR-{n:03d}-{id1:08x}-{id2:08x}-{cddb:08x}.bin")


def fmt_duration(sectors: int) -> str:
    sec = sectors // FRAMES_PER_SEC
    return f"{sec // 60}:{sec % 60:02d}"
