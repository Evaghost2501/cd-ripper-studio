"""Online metadata & cover-art sources: MusicBrainz (+Cover Art Archive), iTunes, Deezer."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

from .. import __version__
from ..core.metadata import AlbumMeta, TrackMeta
from ..core.toc import Toc

UA = f"CDRipperStudio/{__version__} ( https://github.com/cdripper-studio )"
session = requests.Session()
session.headers["User-Agent"] = UA
_mb_lock = threading.Lock()
_mb_last = [0.0]


def _get(url, params=None, timeout=15, mb=False):
    if mb:  # MusicBrainz allows 1 request / second
        with _mb_lock:
            wait = 1.05 - (time.time() - _mb_last[0])
            if wait > 0:
                time.sleep(wait)
            _mb_last[0] = time.time()
    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _artist_credit(ac) -> str:
    return "".join(c.get("name", c.get("artist", {}).get("name", "")) + c.get("joinphrase", "") for c in ac or [])


# ----------------------------- MusicBrainz -----------------------------------
MB = "https://musicbrainz.org/ws/2"
INC = "artist-credits+recordings+labels+release-groups+isrcs+genres+recording-level-rels+work-rels+work-level-rels+artist-rels"


def _mb_release_to_album(rel: dict, discid: str = "", toc: Toc | None = None) -> AlbumMeta:
    media = rel.get("media", [])
    medium = None
    if discid:
        medium = next((m for m in media if any(d.get("id") == discid for d in m.get("discs", []))), None)
    if medium is None and toc:
        n = len(toc.audio_tracks)
        medium = next((m for m in media if m.get("track-count") == n), None)
    medium = medium or (media[0] if media else {})
    labels = rel.get("label-info") or [{}]
    genres = sorted(rel.get("genres", []) + rel.get("release-group", {}).get("genres", []),
                    key=lambda g: -g.get("count", 0))
    a = AlbumMeta(
        albumartist=_artist_credit(rel.get("artist-credit")),
        album=rel.get("title", ""),
        date=rel.get("date", ""),
        genre=genres[0]["name"].title() if genres else "",
        disc=str(medium.get("position", 1)),
        totaldiscs=str(len(media) or 1),
        label=(labels[0].get("label") or {}).get("name", ""),
        catalog=labels[0].get("catalog-number") or "",
        barcode=rel.get("barcode") or "",
        mb_release_id=rel.get("id", ""),
        mb_releasegroup_id=rel.get("release-group", {}).get("id", ""),
        mb_discid=discid,
        source="MusicBrainz",
        cover_url=f"https://coverartarchive.org/release/{rel['id']}/front-500"
        if rel.get("cover-art-archive", {}).get("front") else "",
    )
    a.compilation = a.albumartist.lower() in ("various artists", "여러 아티스트")
    for t in medium.get("tracks", []):
        rec = t.get("recording", {})
        composers, lyricists = [], []
        for r in rec.get("relations", []):
            if r.get("type") == "performance" and r.get("work"):
                for wr in r["work"].get("relations", []):
                    if wr.get("type") == "composer":
                        composers.append(wr["artist"]["name"])
                    elif wr.get("type") == "lyricist":
                        lyricists.append(wr["artist"]["name"])
        a.tracks.append(TrackMeta(
            number=int(t.get("position") or len(a.tracks) + 1),
            title=t.get("title") or rec.get("title", ""),
            artist=_artist_credit(t.get("artist-credit") or rec.get("artist-credit")),
            composer=", ".join(dict.fromkeys(composers)),
            lyricist=", ".join(dict.fromkeys(lyricists)),
            isrc=(rec.get("isrcs") or [""])[0],
            mb_recording_id=rec.get("id", ""),
        ))
    return a


def mb_lookup_disc(toc: Toc) -> list[AlbumMeta]:
    discid = toc.musicbrainz_discid()
    try:
        data = _get(f"{MB}/discid/{discid}", {"toc": toc.musicbrainz_toc(), "inc": INC, "fmt": "json"}, mb=True)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    rels = data.get("releases", [])
    out = []
    for r in rels:
        exact = any(d.get("id") == discid for m in r.get("media", []) for d in m.get("discs", []))
        out.append(_mb_release_to_album(r, discid if exact else "", toc))
    return out


def mb_search(artist: str, album: str, limit: int = 15) -> list[AlbumMeta]:
    q = []
    if album:
        q.append(f'release:"{album}"')
    if artist:
        q.append(f'artist:"{artist}"')
    data = _get(f"{MB}/release", {"query": " AND ".join(q), "limit": limit, "fmt": "json"}, mb=True)
    out = []
    for r in data.get("releases", []):
        a = AlbumMeta(albumartist=_artist_credit(r.get("artist-credit")), album=r.get("title", ""),
                      date=r.get("date", ""), mb_release_id=r["id"], source="MusicBrainz",
                      cover_url=f"https://coverartarchive.org/release/{r['id']}/front-250")
        a.tracks = [TrackMeta(i + 1) for i in range(r.get("track-count", 0))]
        a._partial = True
        out.append(a)
    return out


def mb_release(mbid: str, toc: Toc | None = None) -> AlbumMeta:
    rel = _get(f"{MB}/release/{mbid}", {"inc": INC, "fmt": "json"}, mb=True)
    return _mb_release_to_album(rel, "", toc)


# ----------------------------- iTunes ----------------------------------------
def itunes_search(term: str, country: str = "KR", limit: int = 20) -> list[AlbumMeta]:
    data = _get("https://itunes.apple.com/search",
                {"term": term, "entity": "album", "limit": limit, "country": country})
    out = []
    for r in data.get("results", []):
        art = r.get("artworkUrl100", "")
        a = AlbumMeta(albumartist=r.get("artistName", ""), album=r.get("collectionName", ""),
                      date=(r.get("releaseDate") or "")[:10], genre=r.get("primaryGenreName", ""),
                      source="iTunes", cover_url=art.replace("100x100bb", "600x600bb"))
        a.tracks = [TrackMeta(i + 1) for i in range(r.get("trackCount", 0))]
        a._partial = True
        a._itunes_id = r.get("collectionId")
        a._country = country
        a.label = (r.get("copyright") or "").lstrip("℗© 0123456789").strip()
        out.append(a)
    return out


def itunes_album(a: AlbumMeta) -> AlbumMeta:
    data = _get("https://itunes.apple.com/lookup",
                {"id": a._itunes_id, "entity": "song", "country": getattr(a, "_country", "KR")})
    songs = [r for r in data.get("results", []) if r.get("wrapperType") == "track"]
    discs = max((s.get("discCount", 1) for s in songs), default=1)
    a.totaldiscs = str(discs)
    a.tracks = [TrackMeta(number=s.get("trackNumber", i + 1), title=s.get("trackName", ""),
                          artist=s.get("artistName", ""), genre="")
                for i, s in enumerate(sorted(songs, key=lambda s: (s.get("discNumber", 1), s.get("trackNumber", 0))))
                if s.get("discNumber", 1) == 1] if songs else a.tracks
    a._partial = False
    return a


# ----------------------------- Deezer ----------------------------------------
def deezer_search(term: str, limit: int = 20) -> list[AlbumMeta]:
    data = _get("https://api.deezer.com/search/album", {"q": term, "limit": limit})
    out = []
    for r in data.get("data", []):
        a = AlbumMeta(albumartist=r.get("artist", {}).get("name", ""), album=r.get("title", ""),
                      source="Deezer", cover_url=r.get("cover_big", ""))
        a.tracks = [TrackMeta(i + 1) for i in range(r.get("nb_tracks", 0))]
        a._partial = True
        a._deezer_id = r.get("id")
        out.append(a)
    return out


def deezer_album(a: AlbumMeta) -> AlbumMeta:
    r = _get(f"https://api.deezer.com/album/{a._deezer_id}")
    a.date = r.get("release_date", "")
    a.label = r.get("label", "")
    a.barcode = r.get("upc", "")
    g = r.get("genres", {}).get("data", [])
    a.genre = g[0]["name"] if g else ""
    tracks = r.get("tracks", {}).get("data", [])
    a.tracks = [TrackMeta(number=i + 1, title=t.get("title", ""), artist=t.get("artist", {}).get("name", ""),
                          isrc=t.get("isrc", "")) for i, t in enumerate(tracks)]
    a._partial = False
    return a


def complete(a: AlbumMeta, toc: Toc | None = None) -> AlbumMeta:
    """Fetch the full track listing for a partial search result."""
    if not getattr(a, "_partial", False):
        return a
    if a.source == "MusicBrainz":
        full = mb_release(a.mb_release_id, toc)
        full.cover_url = full.cover_url or a.cover_url
        return full
    if a.source == "iTunes":
        return itunes_album(a)
    if a.source == "Deezer":
        return deezer_album(a)
    return a


def search_all(artist: str, album: str, sources=("MusicBrainz", "iTunes", "Deezer"), country="KR",
               errors: list | None = None) -> list[AlbumMeta]:
    term = f"{artist} {album}".strip()
    results: list[AlbumMeta] = []
    jobs = {"MusicBrainz": lambda: mb_search(artist, album),
            "iTunes": lambda: itunes_search(term, country),
            "Deezer": lambda: deezer_search(term)}
    threads, buckets = [], {}

    def run(name):
        try:
            buckets[name] = jobs[name]()
        except Exception as e:  # noqa: BLE001
            buckets[name] = []
            if errors is not None:
                errors.append(f"{name}: {e}")

    for s in sources:
        th = threading.Thread(target=run, args=(s,))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    for s in sources:
        results += buckets.get(s, [])
    return results


# ----------------------------- Cover art -------------------------------------
@dataclass
class CoverCandidate:
    source: str
    thumb_url: str
    full_url: str
    info: str = ""
    width: int = 0
    height: int = 0


def cover_search(artist: str, album: str, mbid: str = "", country: str = "KR",
                 errors: list | None = None) -> list[CoverCandidate]:
    term = f"{artist} {album}".strip()
    out: list[CoverCandidate] = []

    def safe(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            if errors is not None:
                errors.append(str(e))

    def itunes():
        for r in _get("https://itunes.apple.com/search",
                      {"term": term, "entity": "album", "limit": 12, "country": country}).get("results", []):
            u = r.get("artworkUrl100", "")
            if u:
                out.append(CoverCandidate("iTunes", u.replace("100x100bb", "300x300bb"),
                                          u.replace("100x100bb", "3000x3000bb"),
                                          f"{r.get('artistName')} – {r.get('collectionName')}", 3000, 3000))

    def deezer():
        for r in _get("https://api.deezer.com/search/album", {"q": term, "limit": 12}).get("data", []):
            if r.get("cover_medium"):
                out.append(CoverCandidate("Deezer", r["cover_medium"], r.get("cover_xl") or r["cover_big"],
                                          f"{r.get('artist', {}).get('name')} – {r.get('title')}", 1000, 1000))

    def caa_ids():
        ids = [mbid] if mbid else []
        if not ids and album:
            ids = [a.mb_release_id for a in mb_search(artist, album, limit=6)]
        for rid in ids:
            try:
                d = _get(f"https://coverartarchive.org/release/{rid}", timeout=10)
            except Exception:  # noqa: BLE001
                continue
            for img in d.get("images", []):
                if img.get("front"):
                    th = img.get("thumbnails", {})
                    out.append(CoverCandidate("CoverArtArchive", th.get("250") or th.get("small") or img["image"],
                                              img["image"], "MusicBrainz 원본 스캔"))

    threads = [threading.Thread(target=safe, args=(f,)) for f in (itunes, deezer, caa_ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def google_images_url(artist: str, album: str) -> str:
    return "https://www.google.com/search?tbm=isch&tbs=isz:l&q=" + quote_plus(f"{artist} {album} album cover")


def download(url: str, timeout: int = 20) -> bytes:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content
