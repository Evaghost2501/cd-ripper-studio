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


def write_tags(path: str, fmt: str, album: AlbumMeta, track: TrackMeta,
               cover: tuple[bytes, str] | None, id3v23: bool = True):
    v = track_values(album, track)
    if fmt == "mp3":
        _id3(path, album, track, v, cover, id3v23)
    elif fmt in ("flac", "opus"):
        _vorbis(path, fmt, album, track, v, cover)
    elif fmt in ("aac", "alac"):
        _mp4(path, album, track, v, cover)
    # wav: no tags (keeps maximum compatibility)


def _id3(path, album, track, v, cover, v23):
    from mutagen.id3 import (APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TPE1, TPE2, TPOS,
                             TPUB, TRCK, TSRC, TXXX, USLT, TCMP, UFID)
    tags = ID3()
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
    for desc, val in (("CATALOGNUMBER", album.catalog), ("BARCODE", album.barcode),
                      ("MusicBrainz Album Id", album.mb_release_id),
                      ("MusicBrainz Release Group Id", album.mb_releasegroup_id),
                      ("MusicBrainz Disc Id", album.mb_discid)):
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


def _vorbis(path, fmt, album, track, v, cover):
    from mutagen.flac import FLAC, Picture
    from mutagen.oggopus import OggOpus
    f = FLAC(path) if fmt == "flac" else OggOpus(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    pairs = {
        "TITLE": v["title"], "ARTIST": v["artist"], "ALBUMARTIST": v["albumartist"], "ALBUM": v["album"],
        "TRACKNUMBER": str(v["track"]), "TRACKTOTAL": str(v["totaltracks"]),
        "DISCNUMBER": str(v["disc"]), "DISCTOTAL": str(v["totaldiscs"]), "DATE": album.date,
        "GENRE": v["genre"], "COMPOSER": track.composer, "LYRICIST": track.lyricist, "LABEL": album.label,
        "CATALOGNUMBER": album.catalog, "BARCODE": album.barcode, "ISRC": track.isrc,
        "COMMENT": track.comment or album.comment, "LYRICS": track.lyrics,
        "COMPILATION": "1" if album.compilation else "",
        "MUSICBRAINZ_ALBUMID": album.mb_release_id, "MUSICBRAINZ_RELEASEGROUPID": album.mb_releasegroup_id,
        "MUSICBRAINZ_DISCID": album.mb_discid, "MUSICBRAINZ_TRACKID": track.mb_recording_id,
    }
    for k, val in pairs.items():
        if val:
            t[k] = val
    if cover:
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, cover[1], "Cover", cover[0]
        if fmt == "flac":
            f.clear_pictures()
            f.add_picture(pic)
        else:
            t["METADATA_BLOCK_PICTURE"] = base64.b64encode(pic.write()).decode("ascii")
    f.save()


def _mp4(path, album, track, v, cover):
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    f = MP4(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
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
    for name, val in (("LABEL", album.label), ("CATALOGNUMBER", album.catalog), ("ISRC", track.isrc),
                      ("MusicBrainz Album Id", album.mb_release_id)):
        if val:
            t[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(val.encode())]
    if cover:
        kind = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
        t["covr"] = [MP4Cover(cover[0], imageformat=kind)]
    f.save()
