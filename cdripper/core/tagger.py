"""Write tags + embedded cover art with mutagen."""
from __future__ import annotations

import base64

from .metadata import AlbumMeta, TrackMeta, track_values


def prepare_cover(data: bytes | None, max_px: int) -> tuple[bytes, str] | None:
    """Optionally shrink cover for embedding. Returns (bytes, mime)."""
    if not data:
        return None
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QImage
    img = QImage.fromData(data)
    if img.isNull():
        return None
    too_big = max_px and max(img.width(), img.height()) > max_px
    if not too_big:
        if data[:3] == b"\xff\xd8\xff":
            return data, "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, "image/png"
    else:
        img = img.scaled(max_px, max_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "JPG", 92)
    return bytes(ba), "image/jpeg"


# file extension -> writer family
EXT_FORMAT = {".mp3": "mp3", ".flac": "flac", ".opus": "opus", ".ogg": "ogg",
              ".m4a": "aac", ".m4b": "aac", ".mp4": "aac", ".aac": "aac"}
EDITABLE_EXTS = tuple(EXT_FORMAT)


def format_of(path: str) -> str | None:
    import os
    return EXT_FORMAT.get(os.path.splitext(path)[1].lower())


def write_tags(path: str, fmt: str, album: AlbumMeta, track: TrackMeta,
               cover: tuple[bytes, str] | None, id3v23: bool = True, remove_cover: bool = False):
    """Write the fields this program manages into `path`.

    Existing tags written by other programs (ReplayGain, custom fields …) are kept: only the managed
    fields are replaced. `cover=None` keeps the embedded picture, `remove_cover=True` deletes it."""
    v = track_values(album, track)
    if fmt == "mp3":
        _id3(path, album, track, v, cover, id3v23, remove_cover)
    elif fmt in ("flac", "opus", "ogg"):
        _vorbis(path, fmt, album, track, v, cover, remove_cover)
    elif fmt in ("aac", "alac"):
        _mp4(path, album, track, v, cover, remove_cover)
    # wav: no tags (keeps maximum compatibility)


def author(album: AlbumMeta) -> str:
    """Audiobooks: the album artist is the book's author. Written as Album Artist, as Artist (the
    field audiobook apps show as "Author") and as an explicit AUTHOR tag."""
    return album.albumartist.strip() if album.genre == "Audiobook" else ""


_ID3_TXXX = ("AUTHOR", "MusicBrainz Album Id", "MusicBrainz Release Group Id", "MusicBrainz Disc Id")


def _id3(path, album, track, v, cover, v23, remove_cover):
    from mutagen.id3 import (APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TPE1, TPE2, TPOS,
                             TPUB, TRCK, TSRC, TXXX, USLT, TCMP, UFID, ID3NoHeaderError)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    managed = ["TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TPOS", "TDRC", "TYER", "TDAT", "TCON", "TCOM",
               "TEXT", "TPUB", "TSRC", "TCMP", "COMM", "USLT", "UFID:http://musicbrainz.org"]
    managed += [f"TXXX:{d}" for d in _ID3_TXXX]
    if cover or remove_cover:
        managed.append("APIC")
    for key in managed:
        tags.delall(key)
    tags.add(TIT2(encoding=3, text=v["title"]))
    tags.add(TPE1(encoding=3, text=v["artist"]))
    tags.add(TPE2(encoding=3, text=v["albumartist"]))
    tags.add(TALB(encoding=3, text=v["album"]))
    tags.add(TRCK(encoding=3, text=f"{v['track']}/{v['totaltracks']}"))
    tags.add(TPOS(encoding=3, text=f"{v['disc']}/{v['totaldiscs']}"))
    if album.date:
        tags.add(TDRC(encoding=3, text=album.date))
    if v["genre"]:
        tags.add(TCON(encoding=3, text=v["genre"]))
    if track.composer:
        tags.add(TCOM(encoding=3, text=track.composer))
    if track.lyricist:
        tags.add(TEXT(encoding=3, text=track.lyricist))
    if album.label:
        tags.add(TPUB(encoding=3, text=album.label))
    if track.isrc:
        tags.add(TSRC(encoding=3, text=track.isrc))
    if album.compilation:
        tags.add(TCMP(encoding=3, text="1"))
    comment = track.comment or album.comment
    if comment:
        tags.add(COMM(encoding=3, lang="kor", desc="", text=comment))
    if track.lyrics:
        tags.add(USLT(encoding=3, lang="kor", desc="", text=track.lyrics))
    for desc, val in zip(_ID3_TXXX, (author(album), album.mb_release_id, album.mb_releasegroup_id,
                                     album.mb_discid)):
        if val:
            tags.add(TXXX(encoding=3, desc=desc, text=val))
    if track.mb_recording_id:
        tags.add(UFID(owner="http://musicbrainz.org", data=track.mb_recording_id.encode()))
    if cover:
        tags.add(APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0]))
    if v23:
        tags.update_to_v23()
        tags.save(path, v2_version=3)
    else:
        tags.save(path, v2_version=4)


_VORBIS_KEYS = ("TITLE", "ARTIST", "ALBUMARTIST", "ALBUM", "TRACKNUMBER", "TRACKTOTAL", "TOTALTRACKS",
                "DISCNUMBER", "DISCTOTAL", "TOTALDISCS", "DATE", "GENRE", "COMPOSER", "LYRICIST", "LABEL",
                "ISRC", "AUTHOR", "COMMENT", "DESCRIPTION", "LYRICS", "COMPILATION", "MUSICBRAINZ_ALBUMID",
                "MUSICBRAINZ_RELEASEGROUPID", "MUSICBRAINZ_DISCID", "MUSICBRAINZ_TRACKID")


def _vorbis(path, fmt, album, track, v, cover, remove_cover):
    import mutagen
    from mutagen.flac import FLAC, Picture
    f = FLAC(path) if fmt == "flac" else mutagen.File(path)      # OggOpus / OggVorbis
    if f.tags is None:
        f.add_tags()
    t = f.tags
    pairs = {
        "TITLE": v["title"], "ARTIST": v["artist"], "ALBUMARTIST": v["albumartist"], "ALBUM": v["album"],
        "TRACKNUMBER": str(v["track"]), "TRACKTOTAL": str(v["totaltracks"]),
        "DISCNUMBER": str(v["disc"]), "DISCTOTAL": str(v["totaldiscs"]), "DATE": album.date,
        "GENRE": v["genre"], "COMPOSER": track.composer, "LYRICIST": track.lyricist, "LABEL": album.label,
        "ISRC": track.isrc, "AUTHOR": author(album),
        "COMMENT": track.comment or album.comment, "LYRICS": track.lyrics,
        "COMPILATION": "1" if album.compilation else "",
        "MUSICBRAINZ_ALBUMID": album.mb_release_id, "MUSICBRAINZ_RELEASEGROUPID": album.mb_releasegroup_id,
        "MUSICBRAINZ_DISCID": album.mb_discid, "MUSICBRAINZ_TRACKID": track.mb_recording_id,
    }
    for k in _VORBIS_KEYS:                    # replace only what we manage
        if k in t:
            del t[k]
    for k, val in pairs.items():
        if val:
            t[k] = val
    if cover or remove_cover:
        if fmt == "flac":
            f.clear_pictures()
        elif "METADATA_BLOCK_PICTURE" in t:
            del t["METADATA_BLOCK_PICTURE"]
    if cover:
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, cover[1], "Cover", cover[0]
        if fmt == "flac":
            f.add_picture(pic)
        else:
            t["METADATA_BLOCK_PICTURE"] = base64.b64encode(pic.write()).decode("ascii")
    f.save()


_MP4_FREEFORM = ("LABEL", "ISRC", "AUTHOR", "MusicBrainz Album Id")


def _mp4(path, album, track, v, cover, remove_cover):
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    f = MP4(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    managed = ["\xa9nam", "\xa9ART", "aART", "\xa9alb", "trkn", "disk", "\xa9day", "\xa9gen", "\xa9wrt",
               "\xa9cmt", "\xa9lyr", "cpil"] + [f"----:com.apple.iTunes:{n}" for n in _MP4_FREEFORM]
    if cover or remove_cover:
        managed.append("covr")
    for key in managed:
        if key in t:
            del t[key]
    t["\xa9nam"] = v["title"]
    t["\xa9ART"] = v["artist"]
    t["aART"] = v["albumartist"]
    t["\xa9alb"] = v["album"]
    t["trkn"] = [(v["track"], v["totaltracks"])]
    t["disk"] = [(v["disc"], v["totaldiscs"])]
    for key, val in (("\xa9day", album.date), ("\xa9gen", v["genre"]), ("\xa9wrt", track.composer),
                     ("\xa9cmt", track.comment or album.comment), ("\xa9lyr", track.lyrics)):
        if val:
            t[key] = val
    if album.compilation:
        t["cpil"] = True
    for name, val in zip(_MP4_FREEFORM, (album.label, track.isrc, author(album), album.mb_release_id)):
        if val:
            t[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(val.encode())]
    if cover:
        kind = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
        t["covr"] = [MP4Cover(cover[0], imageformat=kind)]
    f.save()


# ----------------------------------------------------------------------------- reading
def _num_pair(text) -> tuple[int, int]:
    """'3/12' -> (3, 12); '3' -> (3, 0)."""
    s = str(text or "").strip()
    a, _, b = s.partition("/")
    try:
        n = int(a)
    except ValueError:
        n = 0
    try:
        tot = int(b)
    except ValueError:
        tot = 0
    return n, tot


def read_tags(path: str) -> dict:
    """Read an existing audio file's tags into a flat dict:
    album-level keys (album, albumartist, date, genre, label, comment, disc, totaldiscs, compilation),
    track-level keys (number, totaltracks, title, artist, composer, lyricist, isrc, lyrics) and
    'cover' (bytes or None). Missing values are ''."""
    import mutagen
    fmt = format_of(path)
    out = {k: "" for k in ("album", "albumartist", "date", "genre", "label", "comment", "title", "artist",
                           "composer", "lyricist", "isrc", "lyrics")}
    out.update(number=0, totaltracks=0, disc=0, totaldiscs=0, compilation=False, cover=None, fmt=fmt)
    f = mutagen.File(path)
    if f is None or f.tags is None:
        return out
    t = f.tags

    if fmt == "mp3":
        def g(key):
            fr = t.get(key)
            return str(fr.text[0]) if fr is not None and getattr(fr, "text", None) else ""
        out.update(title=g("TIT2"), artist=g("TPE1"), albumartist=g("TPE2"), album=g("TALB"),
                   genre=g("TCON"), composer=g("TCOM"), lyricist=g("TEXT"), label=g("TPUB"), isrc=g("TSRC"),
                   date=g("TDRC") or g("TYER"), compilation=g("TCMP") == "1")
        out["number"], out["totaltracks"] = _num_pair(g("TRCK"))
        out["disc"], out["totaldiscs"] = _num_pair(g("TPOS"))
        comms = t.getall("COMM")
        out["comment"] = str(comms[0].text[0]) if comms and comms[0].text else ""
        lyr = t.getall("USLT")
        out["lyrics"] = lyr[0].text if lyr else ""
        apic = t.getall("APIC")
        out["cover"] = apic[0].data if apic else None
    elif fmt in ("flac", "opus", "ogg"):
        def g(key):
            v = t.get(key) or t.get(key.lower())
            return str(v[0]) if v else ""
        out.update(title=g("TITLE"), artist=g("ARTIST"), albumartist=g("ALBUMARTIST"), album=g("ALBUM"),
                   date=g("DATE"), genre=g("GENRE"), composer=g("COMPOSER"), lyricist=g("LYRICIST"),
                   label=g("LABEL") or g("ORGANIZATION"), isrc=g("ISRC"), comment=g("COMMENT") or g("DESCRIPTION"),
                   lyrics=g("LYRICS"), compilation=g("COMPILATION") == "1")
        out["number"], tt = _num_pair(g("TRACKNUMBER"))
        out["totaltracks"] = tt or _num_pair(g("TRACKTOTAL") or g("TOTALTRACKS"))[0]
        out["disc"], dt = _num_pair(g("DISCNUMBER"))
        out["totaldiscs"] = dt or _num_pair(g("DISCTOTAL") or g("TOTALDISCS"))[0]
        pics = getattr(f, "pictures", None)
        if pics:
            out["cover"] = pics[0].data
        elif g("METADATA_BLOCK_PICTURE"):
            from mutagen.flac import Picture
            try:
                out["cover"] = Picture(base64.b64decode(g("METADATA_BLOCK_PICTURE"))).data
            except Exception:  # noqa: BLE001
                pass
    elif fmt == "aac":
        def g(key):
            v = t.get(key)
            return str(v[0]) if v else ""
        out.update(title=g("\xa9nam"), artist=g("\xa9ART"), albumartist=g("aART"), album=g("\xa9alb"),
                   date=g("\xa9day"), genre=g("\xa9gen"), composer=g("\xa9wrt"), comment=g("\xa9cmt"),
                   lyrics=g("\xa9lyr"), compilation=bool(t.get("cpil")))
        trkn, disk = t.get("trkn"), t.get("disk")
        if trkn:
            out["number"], out["totaltracks"] = trkn[0][0], trkn[0][1]
        if disk:
            out["disc"], out["totaldiscs"] = disk[0][0], disk[0][1]
        for name, key in (("LABEL", "label"), ("ISRC", "isrc")):
            ff = t.get(f"----:com.apple.iTunes:{name}")
            if ff:
                out[key] = bytes(ff[0]).decode("utf-8", "replace")
        covr = t.get("covr")
        out["cover"] = bytes(covr[0]) if covr else None
    return out
