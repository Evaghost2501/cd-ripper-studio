"""Book sources (audiobooks). Fixtures mimic the structure of the real result pages / API."""
import pytest

from cdripper.sources import books, web

YES24_HTML = """
<ul id="yesSchList">
 <li data-goods-no="123456789">
  <div class="itemUnit">
   <div class="item_img"><a href="/Product/Goods/123456789">
     <img class="lazy" data-original="https://image.yes24.com/goods/123456789/M" src="blank.gif"></a></div>
   <div class="item_info">
    <div class="info_row info_name"><a class="gd_name" href="/Product/Goods/123456789">어린 왕자</a></div>
    <div class="info_row info_pubGrp">
      <span class="authPub info_auth"><a href="#">생텍쥐페리</a> 저 / <a href="#">김화영</a> 역</span>
      <span class="authPub info_pub"><a href="#">문학동네</a></span>
      <span class="authPub info_date">2007년 05월 21일</span>
    </div>
   </div>
  </div>
 </li>
 <li data-goods-no="987">
  <div class="itemUnit"><div class="item_info">
    <a class="gd_name" href="/Product/Goods/987">세이노의 가르침</a>
    <span class="authPub info_auth">세이노 글</span><span class="authPub info_pub">데이원</span>
    <span class="authPub info_date">2023년 03월</span></div></div>
 </li>
</ul>"""

AMAZON_HTML = """
<div data-component-type="s-search-result" data-asin="0156012197">
  <img class="s-image" src="https://m.media-amazon.com/images/I/71OZY035QKL._AC_UY218_.jpg">
  <h2><a class="a-link-normal" href="/Little-Prince/dp/0156012197"><span>The Little Prince</span></a></h2>
  <div class="a-row"><span>by </span><a>Antoine de Saint-Exupéry</a> <span>and Richard Howard</span>
    <span> | </span><span>May 15, 2000</span></div>
</div>
<div data-component-type="s-search-result" data-asin="X2">
  <img class="s-image" src="https://m.media-amazon.com/images/I/41abc._SY300_.png">
  <h2><span>Audio Book Two</span></h2>
  <div class="a-row"><span>by </span><a>Jane Doe</a>  <span>2019</span></div>
</div>"""


def test_parse_yes24():
    r = books.parse_yes24(YES24_HTML)
    assert len(r) == 2                                    # no duplicates
    a = r[0]
    assert (a.album, a.albumartist, a.label, a.date) == ("어린 왕자", "생텍쥐페리", "문학동네", "2007-05-21")
    assert a.cover_url == "https://image.yes24.com/goods/123456789/XL"   # large cover
    assert a.genre == "Audiobook" and a.source == "Yes24" and a._is_book and not a.tracks
    assert (r[1].albumartist, r[1].date) == ("세이노", "2023-03")


def test_parse_amazon():
    r = books.parse_amazon(AMAZON_HTML)
    assert [x.album for x in r] == ["The Little Prince", "Audio Book Two"]
    assert r[0].albumartist == "Antoine de Saint-Exupéry" and r[0].date == "2000-05-15"
    assert r[0].cover_url == "https://m.media-amazon.com/images/I/71OZY035QKL.jpg"   # original size
    assert r[1].cover_url.endswith("/41abc.png") and r[1].date == "2019"


def test_amazon_captcha_is_reported():
    with pytest.raises(books.BlockedError):
        books.parse_amazon("<html><form action='/errors/validateCaptcha'>Enter the characters "
                           "you see below (captcha)</form></html>")


def test_parse_google_books():
    data = {"items": [{"volumeInfo": {
        "title": "The Little Prince", "subtitle": "Audiobook", "authors": ["Antoine de Saint-Exupéry"],
        "publisher": "HMH", "publishedDate": "2000-06-29",
        "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1"
                                    "&zoom=1&edge=curl&source=gbs_api"}}}]}
    [a] = books.parse_google_books(data)
    assert (a.album, a.albumartist, a.label, a.date) == ("The Little Prince: Audiobook",
                                                         "Antoine de Saint-Exupéry", "HMH", "2000-06-29")
    assert a.cover_url.startswith("https://") and "zoom=0" in a.cover_url and "edge=curl" not in a.cover_url


def test_search_all_includes_books(monkeypatch):
    calls = {}

    def fake_search(name):
        def f(session, term, limit=20):
            calls[name] = term
            return [books._book(f"{name} book", "A", "P", "2020", "", name)]
        return f
    monkeypatch.setattr(books, "SEARCHERS", {n: fake_search(n) for n in ("Yes24", "Amazon", "Google Books")})
    res = web.search_all("생텍쥐페리", "어린 왕자", sources=("Yes24", "Amazon", "Google Books"))
    assert {a.source for a in res} == {"Yes24", "Amazon", "Google Books"}
    assert calls["Yes24"] == "생텍쥐페리 어린 왕자"


def test_book_result_applies_album_fields_and_audiobook(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    w = mw.MainWindow()
    d = w.drive()
    w._disc_loaded((d.read_toc(), d.read_cdtext()))
    titles = [t.title for t in w.album.tracks]
    w.apply_album(books.parse_yes24(YES24_HTML)[0], replace=True)
    assert (w.album.album, w.album.albumartist, w.album.label) == ("어린 왕자", "생텍쥐페리", "문학동네")
    assert w.album.genre == "Audiobook" and w.genre_btns["Audiobook"].isChecked()
    assert [t.title for t in w.album.tracks] == titles      # track titles untouched
    w.close()


def test_label_preset_button(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("CDRIPPER_FAKE", "1")
    from PySide6.QtWidgets import QPushButton
    from cdripper.gui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "lookup_disc", lambda self, manual=False: None)
    w = mw.MainWindow()
    btn = next(b for b in w.findChildren(QPushButton) if b.text() == "JYbooks")
    btn.click()
    assert w.album.label == "JYbooks" and w.album_edits["label"].text() == "JYbooks"
    w.close()
