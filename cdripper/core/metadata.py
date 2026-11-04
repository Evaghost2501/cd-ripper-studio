"""Album / track metadata model."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict

ALBUM_FIELDS = [
    # key, Korean label — in the order shown in the album panel
    ("album", "앨범"),
    ("albumartist", "앨범 아티스트"),
    ("label", "레이블"),
    ("genre", "장르"),            # chosen with buttons (GENRES), not typed
    ("date", "발매일/연도"),
    ("disc", "디스크 번호"),
    ("totaldiscs", "총 디스크 수"),
    ("comment", "코멘트"),
]

GENRES = ["Music", "Audiobook"]

TRACK_FIELDS = [
    ("title", "제목"),
    ("artist", "아티스트"),
    ("composer", "작곡"),
    ("lyricist", "작사"),
    ("isrc", "ISRC"),
    ("comment", "코멘트"),
]


@dataclass
class TrackMeta:
    number: int
    title: str = ""
    artist: str = ""
    composer: str = ""
    lyricist: str = ""
    genre: str = ""
    isrc: str = ""
    comment: str = ""
    lyrics: str = ""
    mb_recording_id: str = ""
    length: int = 0          # sectors (from TOC)
    rip: bool = True


@dataclass
class AlbumMeta:
    albumartist: str = ""
    album: str = ""
    date: str = ""
    genre: str = ""
    disc: str = "1"
    totaldiscs: str = "1"
    label: str = ""
    catalog: str = ""
    barcode: str = ""
    comment: str = ""
    compilation: bool = False
    mb_release_id: str = ""
    mb_releasegroup_id: str = ""
    mb_discid: str = ""
    source: str = ""                         # where the data came from
    cover_url: str = ""                      # suggestion from source
    tracks: list[TrackMeta] = field(default_factory=list)

    @property
    def year(self) -> str:
        return self.date[:4] if self.date else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlbumMeta":
        names = {f.name for f in fields(cls)}
        tnames = {f.name for f in fields(TrackMeta)}
        a = cls(**{k: v for k, v in d.items() if k in names and k != "tracks"})
        a.tracks = [TrackMeta(**{k: v for k, v in t.items() if k in tnames}) for t in d.get("tracks", [])]
        return a


def track_values(album: AlbumMeta, track: TrackMeta) -> dict:
    """Flat dict of every value usable in file-name templates and tags."""
    total = len(album.tracks)
    return {
        "albumartist": album.albumartist or track.artist,
        "artist": track.artist or album.albumartist,
        "album": album.album,
        "title": track.title or f"Track {track.number:02d}",
        "track": track.number,
        "tracknumber": track.number,
        "totaltracks": total,
        "disc": int(album.disc) if str(album.disc).isdigit() else 1,
        "totaldiscs": int(album.totaldiscs) if str(album.totaldiscs).isdigit() else 1,
        "date": album.date,
        "year": album.year,
        "genre": album.genre,                 # genre is album-level only (Music / Audiobook)
        "composer": track.composer,
        "lyricist": track.lyricist,
        "label": album.label,
        "isrc": track.isrc,
    }
