"""Book sources for audiobooks: Yes24 (Korea), Amazon US, Google Books.

Yes24 and Amazon have no public search API, so their normal search result pages are read.
That works as long as the sites keep their page layout and do not block the request
(Amazon in particular sometimes answers automated requests with a CAPTCHA page).
Google Books has a free official API and serves as a dependable fallback.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..core.metadata import AlbumMeta

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

YES24_SEARCH = "https://www.yes24.com/Product/Search"
AMAZON_SEARCH = "https://www.amazon.com/s"
GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"


class BlockedError(RuntimeError):
    """The site answered with a CAPTCHA / robot check instead of results."""


def _book(title, author, publisher, date, cover, source, url="") -> AlbumMeta:
    a = AlbumMeta(album=title.strip(), albumartist=author.strip(), label=publisher.strip(),
                  date=date, genre="Audiobook", source=source, cover_url=cover)
    a._book_url = url
    a._is_book = True
    return a


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ----------------------------------------------------------------------------- Yes24
def _yes24_date(text: str) -> str:
    """'2021년 03월 15일' / '2021년 03월' → '2021-03-15' / '2021-03'."""
    m = re.search(r"(\d{4})\s*년(?:\s*(\d{1,2})\s*월)?(?:\s*(\d{1,2})\s*일)?", text or "")
    if not m:
        return ""
    y, mo, d = m.groups()
    return "-".join([y] + [f"{int(x):02d}" for x in (mo, d) if x])


def parse_yes24(html: str, limit: int = 20) -> list[AlbumMeta]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    items = soup.select("#yesSchList > li") or soup.select("div.itemUnit")
    for item in items:
        name = item.select_one("a.gd_name")
        if not name:
            continue
        href = name.get("href", "")
        m = re.search(r"/Goods/(\d+)", href) or re.search(r"(\d{5,})", item.get("data-goods-no", ""))
        goods = m.group(1) if m else ""
        auth = item.select_one(".info_auth")
        # "생텍쥐페리 저 / 김화영 역" → first credit only (the author), role word removed
        first_credit = _clean(auth.get_text(" ") if auth else "").split("/")[0]
        author = re.sub(r"\s*(저|글|지음|원작|그림|편|엮음|역|옮김)\s*$", "", first_credit).strip()
        pub = item.select_one(".info_pub")
        date = item.select_one(".info_date")
        img = item.select_one("img")
        thumb = (img.get("data-original") or img.get("src") or "") if img else ""
        cover = f"https://image.yes24.com/goods/{goods}/XL" if goods else thumb
        out.append(_book(_clean(name.get_text()), author, _clean(pub.get_text() if pub else ""),
                         _yes24_date(date.get_text() if date else ""), cover, "Yes24",
                         urljoin("https://www.yes24.com", href)))
        if len(out) >= limit:
            break
    return out


def yes24_search(session, query: str, limit: int = 20) -> list[AlbumMeta]:
    r = session.get(YES24_SEARCH, params={"domain": "BOOK", "query": query}, headers=BROWSER_HEADERS,
                    timeout=15)
    r.raise_for_status()
    return parse_yes24(r.text, limit)


# ----------------------------------------------------------------------------- Amazon
_AMZ_SIZE = re.compile(r"\._[^/]*_\.(jpg|jpeg|png)$", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _amazon_date(text: str) -> str:
    m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", text or "")
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return m.group(0) if m else ""


def parse_amazon(html: str, limit: int = 20) -> list[AlbumMeta]:
    if "captcha" in html.lower() and "s-search-result" not in html:
        raise BlockedError("Amazon이 자동 검색을 막았습니다 (로봇 확인 페이지)")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select('div[data-component-type="s-search-result"]'):
        asin = item.get("data-asin", "")
        h2 = item.select_one("h2")
        title = _clean(h2.get_text()) if h2 else ""
        if not title:
            continue
        img = item.select_one("img.s-image")
        thumb = img.get("src", "") if img else ""
        cover = _AMZ_SIZE.sub(r".\1", thumb)          # strip size suffix → original resolution
        row_text = _clean(item.get_text(" "))
        author = ""
        by = re.search(r"\bby\s+(.+?)(?:\s+\|\s+|\s{2,}|\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                       r"[a-z]*\.? \d{1,2}, \d{4}|$)", row_text)
        if by:
            author = by.group(1).split(" and ")[0].strip(" ,")
        link = item.select_one("h2 a, a.a-link-normal[href*='/dp/']")
        url = urljoin("https://www.amazon.com", link.get("href", "")) if link else ""
        out.append(_book(title, author, "", _amazon_date(row_text), cover, "Amazon", url))
        out[-1]._asin = asin
        if len(out) >= limit:
            break
    return out


def amazon_search(session, query: str, limit: int = 20) -> list[AlbumMeta]:
    r = session.get(AMAZON_SEARCH, params={"k": query, "i": "stripbooks"}, headers=BROWSER_HEADERS,
                    timeout=15)
    if r.status_code in (503, 429):
        raise BlockedError("Amazon이 자동 검색을 막았습니다 (HTTP %d)" % r.status_code)
    r.raise_for_status()
    return parse_amazon(r.text, limit)


# ----------------------------------------------------------------------------- Google Books
def parse_google_books(data: dict, limit: int = 20) -> list[AlbumMeta]:
    out = []
    for it in data.get("items", [])[:limit]:
        v = it.get("volumeInfo", {})
        links = v.get("imageLinks", {})
        thumb = links.get("extraLarge") or links.get("large") or links.get("medium") or \
            links.get("thumbnail") or links.get("smallThumbnail") or ""
        thumb = thumb.replace("http://", "https://").replace("&edge=curl", "")
        if "zoom=" in thumb and not (links.get("extraLarge") or links.get("large")):
            thumb = re.sub(r"zoom=\d", "zoom=0", thumb)   # largest size Google Books serves
        title = v.get("title", "")
        if v.get("subtitle"):
            title += f": {v['subtitle']}"
        out.append(_book(title, ", ".join(v.get("authors", [])), v.get("publisher", ""),
                         v.get("publishedDate", ""), thumb, "Google Books", v.get("infoLink", "")))
    return out


def google_books_search(session, query: str, limit: int = 20) -> list[AlbumMeta]:
    r = session.get(GOOGLE_BOOKS, params={"q": query, "maxResults": min(limit, 40), "printType": "books"},
                    timeout=15)
    r.raise_for_status()
    return parse_google_books(r.json(), limit)


SEARCHERS = {"Yes24": yes24_search, "Amazon": amazon_search, "Google Books": google_books_search}
