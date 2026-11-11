"""Persistent user settings (JSON in %APPDATA%\\CDRipperStudio)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields


def logs_dir() -> str:
    d = os.path.join(config_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "CDRipperStudio")
    os.makedirs(d, exist_ok=True)
    return d


DEFAULT_TITLE_PRESETS = ["JYbooks' Company Song", "Story Song", "Story Reading", "Echo Chant", "Kids Song",
                         "Melody"]


@dataclass
class Settings:
    output_dir: str = os.path.join(os.path.expanduser("~"), "Music", "CD Rips")
    album_folder: bool = True            # put each album in <저장 폴더>/<앨범 이름>/
    template: str = "{track:02}. {title}"
    various_template: str = "{track:02}. {artist} - {title}"
    genre: str = "Music"                 # last chosen genre button = default for the next CD
    format: str = "mp3"
    quality: str = "cbr320"
    extra_flac: bool = False             # also produce a FLAC archive copy
    id3v23: bool = True                  # ID3v2.3 = best Windows Explorer / car-stereo compatibility
    embed_cover: bool = True
    cover_max_px: int = 1000             # 0 = original size
    save_cover_file: bool = True         # folder.jpg next to tracks
    write_m3u: bool = False              # playlist (.m3u8) in the album folder
    write_cue: bool = False              # cue sheet (.cue) in the album folder
    settings_version: int = 2
    log_in_album_folder: bool = False    # rip log always goes to %APPDATA%\\CDRipperStudio\\logs; copy optional
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
    theme: str = "dark"                  # Purple Green Flux: "dark" = Night (default), "light" = Day
    font_pt: int = 18                    # UI font size (2x the original 9 pt)
    cover_music_sources: bool = False
    hidden_columns: list = field(default_factory=lambda: ["lyricist", "isrc"])
    column_widths: dict = field(default_factory=dict)   # track table, at 9 pt scale
    label_presets: list = field(default_factory=lambda: ["JYbooks"])   # one-click label buttons
    title_presets: list = field(default_factory=lambda: list(DEFAULT_TITLE_PRESETS))  # quick track titles
    search_sources: list = field(default_factory=list)  # remembered checkboxes in the search dialog    # cover search: also query iTunes/Deezer/CAA besides Google

    @classmethod
    def load(cls) -> "Settings":
        p = os.path.join(config_dir(), "settings.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            names = {f.name for f in fields(cls)}
            if d.get("settings_version", 1) < 2:     # v2: playlist / cue sheet are off by default
                d["write_m3u"] = d["write_cue"] = False
                d["settings_version"] = 2
            # old defaults already contained the album folder; the album-folder option does that now
            if d.get("template") == "{albumartist}/[{year} - ]{album}/{track:02}. {title}":
                d["template"] = cls.template
            if d.get("various_template") == "Various Artists/[{year} - ]{album}/{track:02}. {artist} - {title}":
                d["various_template"] = cls.various_template
            return cls(**{k: v for k, v in d.items() if k in names})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        p = os.path.join(config_dir(), "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
