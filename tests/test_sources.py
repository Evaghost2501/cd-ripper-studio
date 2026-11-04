from cdripper.core.toc import Toc, TocTrack
from cdripper.sources import web

MB_DISCID = {"releases": [{
    "id": "rel-1", "title": "Palette", "date": "2017-04-21", "barcode": "8804775079517",
    "artist-credit": [{"name": "IU", "joinphrase": ""}],
    "release-group": {"id": "rg-1", "genres": [{"name": "k-pop", "count": 3}]},
    "label-info": [{"catalog-number": "L100005234", "label": {"name": "LOEN Entertainment"}}],
    "cover-art-archive": {"front": True},
    "media": [{"position": 1, "track-count": 2, "discs": [{"id": "DISCID"}], "tracks": [
        {"position": 1, "title": "이 지금", "artist-credit": [{"name": "IU"}],
         "recording": {"id": "rec-1", "isrcs": ["KRA381700001"], "relations": [
             {"type": "performance", "work": {"relations": [
                 {"type": "composer", "artist": {"name": "IU"}},
                 {"type": "lyricist", "artist": {"name": "IU"}}]}}]}},
        {"position": 2, "title": "팔레트",
         "artist-credit": [{"name": "IU", "joinphrase": " feat. "}, {"name": "G-DRAGON"}],
         "recording": {"id": "rec-2"}}]}]}]}


def test_musicbrainz_discid_parse(monkeypatch):
    toc = Toc(1, 2, 30000, [TocTrack(1, 0), TocTrack(2, 15000)])
    monkeypatch.setattr(toc.__class__, "musicbrainz_discid", lambda self: "DISCID")
    monkeypatch.setattr(web, "_get", lambda *a, **k: MB_DISCID)
    [a] = web.mb_lookup_disc(toc)
    assert (a.albumartist, a.album, a.date, a.genre) == ("IU", "Palette", "2017-04-21", "K-Pop")
    assert (a.label, a.catalog, a.mb_discid) == ("LOEN Entertainment", "L100005234", "DISCID")
    assert a.tracks[0].composer == "IU" and a.tracks[0].isrc == "KRA381700001"
    assert a.tracks[1].artist == "IU feat. G-DRAGON"
    assert a.cover_url.endswith("/release/rel-1/front-500")


def test_itunes_and_deezer(monkeypatch):
    def fake(url, params=None, **k):
        if "itunes.apple.com/search" in url:
            return {"results": [{"artistName": "IU", "collectionName": "Palette", "collectionId": 9,
                                 "releaseDate": "2017-04-21T07:00:00Z", "trackCount": 2,
                                 "primaryGenreName": "K-Pop", "copyright": "℗ 2017 LOEN",
                                 "artworkUrl100": "https://x/100x100bb.jpg"}]}
        if "itunes.apple.com/lookup" in url:
            return {"results": [{"wrapperType": "collection"},
                                {"wrapperType": "track", "trackNumber": 2, "trackName": "B", "artistName": "IU"},
                                {"wrapperType": "track", "trackNumber": 1, "trackName": "A", "artistName": "IU"}]}
        if "deezer.com/search" in url:
            return {"data": [{"id": 5, "title": "Palette", "artist": {"name": "IU"}, "nb_tracks": 1,
                              "cover_big": "c"}]}
        if "deezer.com/album" in url:
            return {"release_date": "2017-04-21", "label": "LOEN", "upc": "1", "genres": {"data": [{"name": "Pop"}]},
                    "tracks": {"data": [{"title": "A", "artist": {"name": "IU"}, "isrc": "K1"}]}}
        return {"releases": []}
    monkeypatch.setattr(web, "_get", fake)
    res = web.search_all("IU", "Palette")
    it = next(a for a in res if a.source == "iTunes")
    assert it.cover_url.endswith("600x600bb.jpg") and it.label == "LOEN"
    full = web.complete(it)
    assert [t.title for t in full.tracks] == ["A", "B"]
    dz = web.complete(next(a for a in res if a.source == "Deezer"))
    assert dz.label == "LOEN" and dz.tracks[0].isrc == "K1"
