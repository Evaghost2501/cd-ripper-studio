"""Persistent user settings (JSON in %APPDATA%\\CDRipperStudio)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "CDRipperStudio")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Settings:
    output_dir: str = os.path.join(os.path.expanduser("~"), "Music", "CD Rips")
    template: str = "{albumartist}/[{year} - ]{album}/{track:02}. {title}"
    various_template: str = "Various Artists/[{year} - ]{album}/{track:02}. {artist} - {title}"
    format: str = "mp3"
    quality: str = "cbr320"
    extra_flac: bool = False             # also produce a FLAC archive copy
    id3v23: bool = True                  # ID3v2.3 = best Windows Explorer / car-stereo compatibility
    embed_cover: bool = True
    cover_max_px: int = 1000             # 0 = original size
    save_cover_file: bool = True         # folder.jpg next to tracks
    write_m3u: bool = True
    write_cue: bool = True
    write_log: bool = True
    secure_mode: bool = True
    read_offset: int = 0                 # samples; see accuraterip.com/driveoffsets.htm
    accuraterip: bool = True
    eject_when_done: bool = True
    auto_lookup: bool = True
    auto_fill_tracks: bool = True        # album artist / genre / comment typed → copied to tracks
    store_country: str = "KR"
    ffmpeg_path: str = ""
    ocr_engine: str = "auto"
    ocr_lang: str = "ko"
    camera_index: int = 0
    last_drive: str = ""
    font_pt: int = 18                    # UI font size (2x the original 9 pt)
    cover_music_sources: bool = False
    hidden_columns: list = field(default_factory=lambda: ["lyricist", "isrc"])    # cover search: also query iTunes/Deezer/CAA besides Google

    @classmethod
    def load(cls) -> "Settings":
        p = os.path.join(config_dir(), "settings.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            names = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in d.items() if k in names})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        p = os.path.join(config_dir(), "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
