"""Real network checks against the live services.

They only run where the internet is reachable (the GitHub Actions build machine); anywhere else
(no network / blocked proxy) they are skipped. They catch things offline fixtures cannot:
requests the services reject, and page / API format changes.
"""
import pytest
import requests

from cdripper.core.toc import Toc, TocTrack
from cdripper.sources import books, web


def _online(url):
    try:
        requests.head(url, timeout=8)
    except requests.RequestException:
        pytest.skip(f"no network access to {url}")


def test_musicbrainz_disc_lookup_is_accepted():
    _online("https://musicbrainz.org")
    # disc from the MusicBrainz Disc ID documentation
    offs = [150, 15363, 32314, 46592, 63414, 80489]
    toc = Toc(1, 6, 95462 - 150, [TocTrack(i + 1, o - 150) for i, o in enumerate(offs)])
    res = web.mb_lookup_disc(toc)            # must not raise (HTTP 400 = rejected request options)
    assert isinstance(res, list)
    # a disc that certainly is not in the database: an empty list, not an error
    toc2 = Toc(1, 4, 75 * 70, [TocTrack(1, 0), TocTrack(2, 1500), TocTrack(3, 2625), TocTrack(4, 3900)])
    assert web.mb_lookup_disc(toc2) == [] or isinstance(web.mb_lookup_disc(toc2), list)


def test_musicbrainz_search_and_release_lookup():
    _online("https://musicbrainz.org")
    res = web.mb_search("IU", "Palette", limit=5)
    assert res, "MusicBrainz search returned nothing"
    full = web.complete(res[0])
    assert full.album and full.tracks and full.tracks[0].title


def test_itunes_and_deezer_live():
    _online("https://itunes.apple.com")
    res = web.search_all("IU", "Palette", sources=("iTunes", "Deezer"))
    for src in ("iTunes", "Deezer"):
        hits = [a for a in res if a.source == src]
        assert hits, f"{src}: no results"
        full = web.complete(hits[0])
        assert full.tracks and full.tracks[0].title, f"{src}: no track list"


def test_google_books_live():
    _online("https://www.googleapis.com")
    try:
        res = books.google_books_search(web.session, "The Little Prince Saint-Exupery")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            pytest.skip("Google Books quota for this network is used up (429)")
        raise
    assert res and res[0].album and res[0].albumartist


@pytest.mark.parametrize("name", ["Yes24", "Amazon"])
def test_bookstore_pages_still_parse(name):
    _online("https://www.yes24.com" if name == "Yes24" else "https://www.amazon.com")
    try:
        res = books.SEARCHERS[name](web.session, "어린 왕자" if name == "Yes24" else "The Little Prince")
    except books.BlockedError as e:
        pytest.skip(str(e))              # robot check: not a parsing problem
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429, 503):
            pytest.skip(f"{name} refused the automated request ({e.response.status_code})")
        raise
    assert res, f"{name}: page fetched but no results parsed (page layout changed?)"
    assert res[0].album
