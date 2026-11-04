"""File-name templates.

Syntax
  {field}            value, e.g. {title}
  {field:02}         zero-padded number, e.g. {track:02} -> 03
  [ ... ]            optional block: removed when any field inside is empty
  /  or  \\           folder separator

Example:  {albumartist}/[{year} - ]{album}/[{disc}-]{track:02} - {title}
"""
from __future__ import annotations

import re

PRESETS = [
    # file name only (the album sub-folder option adds the folder)
    "{track:02}. {title}",
    "{track:02} - {title}",
    "{track:02}. {artist} - {title}",
    "{disc}-{track:02} {title}",
    # with folders of their own
    "{albumartist}/{album}/{track:02} - {title}",
    "{albumartist}/[{year} - ]{album}/{track:02}. {title}",
]

FIELDS_HELP = [
    ("{title}", "트랙 제목"), ("{artist}", "트랙 아티스트"), ("{albumartist}", "앨범 아티스트"),
    ("{album}", "앨범"), ("{track:02}", "트랙 번호(2자리)"), ("{totaltracks}", "총 트랙 수"),
    ("{disc}", "디스크 번호"), ("{totaldiscs}", "총 디스크 수"), ("{year}", "연도"),
    ("{date}", "발매일"), ("{genre}", "장르"), ("{composer}", "작곡가"), ("{label}", "레이블"),
]

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_FIELD = re.compile(r"\{(\w+)(?::([^}]*))?\}")
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")


def sanitize(part: str, max_len: int = 120) -> str:
    part = _ILLEGAL.sub(lambda m: "：" if m.group() == ":" else ("？" if m.group() == "?" else "_"), part)
    part = part.strip().rstrip(". ")
    if part.upper().split(".")[0] in _RESERVED:
        part = "_" + part
    return part[:max_len].rstrip(". ") or "_"


def _render_fields(text: str, values: dict) -> tuple[str, bool]:
    """Substitute fields. Returns (text, any_empty)."""
    empty = False

    def sub(m):
        nonlocal empty
        key, spec = m.group(1), m.group(2)
        v = values.get(key, "")
        if v in ("", None, 0):
            empty = True
            return ""
        if spec:
            try:
                return format(int(v) if str(v).isdigit() else v, spec)
            except (ValueError, TypeError):
                return str(v)
        # protect path separators inside values
        return str(v).replace("/", "\x01").replace("\\", "\x01")

    return _FIELD.sub(sub, text), empty


def render(template: str, values: dict) -> str:
    """Render template into a relative path (without extension)."""
    def opt(m):
        out, empty = _render_fields(m.group(1), values)
        return "" if empty else out

    text = _OPTIONAL.sub(opt, template)
    text, _ = _render_fields(text, values)
    parts = re.split(r"[/\\]", text)
    parts = [sanitize(p.replace("\x01", "_")) for p in parts if p.strip()]
    return "/".join(parts) or "track"
