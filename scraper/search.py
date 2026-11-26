"""Firecrawl 대체 기능: 웹 검색, 여러 주소 한꺼번에 수집, 사이트 주소 목록(map), 사이트 따라가며 수집(crawl).

웹 검색 순서 (2026-11-26 이 PC에서 실측)
  1) DuckDuckGo Lite (lite.duckduckgo.com) — HTTP로 바로 열림, 한국어 결과 양호
  2) Bing — HTTP로 열림(결과 10개). 한국어 질의 품질은 DuckDuckGo보다 떨어짐
  Google·네이버 웹검색은 HTTP로 결과를 주지 않아 쓰지 않는다(네이버 장소는 naver_search 사용).
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from . import core

TIME_MAP = {"day": "d", "week": "w", "month": "m", "year": "y"}


# ---------------------------------------------------------------- 웹 검색

def _clean(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t or "")
    t = (t.replace("&amp;", "&").replace("&quot;", '"').replace("&#x27;", "'").replace("&#39;", "'")
         .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", t).strip()


def _ddg_url(href: str) -> str:
    href = href.replace("&amp;", "&")
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    if "uddg" in q:
        return unquote(q["uddg"][0])
    return href


def _parse_ddg_lite(html: str) -> list[dict]:
    out = []
    # 광고 행은 class에 result-sponsored 가 붙는다
    rows = re.split(r"<tr[^>]*>\s*<td[^>]*valign=\"top\"[^>]*>", html)
    for row in rows[1:]:
        m = re.search(r"<a[^>]*href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", row, re.S) \
            or re.search(r"<a[^>]*class=['\"]result-link['\"][^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", row, re.S)
        if not m:
            continue
        if "result-sponsored" in row or "/y.js?" in m.group(1):
            continue
        sn = re.search(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", row, re.S)
        url = _ddg_url(m.group(1))
        if not url.startswith("http") or "duckduckgo.com" in urlparse(url).netloc:
            continue
        out.append({"title": _clean(m.group(2)), "url": url, "snippet": _clean(sn.group(1)) if sn else ""})
    return out


def _bing_url(href: str) -> str:
    href = href.replace("&amp;", "&")
    if "bing.com/ck/a" in href:
        u = (parse_qs(urlparse(href).query).get("u") or [""])[0]
        if u.startswith("a1"):
            b = u[2:]
            try:
                return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore")
            except Exception:
                pass
    return href


def _parse_bing(html: str) -> list[dict]:
    out = []
    for blk in re.split(r'<li class="b_algo"', html)[1:]:
        blk = blk[:8000]
        m = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", blk, re.S)
        if not m:
            continue
        sn = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        url = _bing_url(m.group(1))
        if not url.startswith("http"):
            continue
        out.append({"title": _clean(m.group(2)), "url": url, "snippet": _clean(sn.group(1)) if sn else ""})
    return out


def _get(url: str) -> tuple[Optional[str], dict]:
    core.throttle(url)
    t0 = time.monotonic()
    page, err = core.fetch_http(url, headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    status = getattr(page, "status", None)
    html = str(page.html_content) if page is not None else None
    ok = page is not None and status == 200
    core.log_event(url=url, engine="search_http", status=status, reason="ok" if ok else (err and "network_error") or f"status_{status}",
                   secs=round(time.monotonic() - t0, 2), final=True)
    return (html if ok else None), {"engine": urlparse(url).netloc, "status": status, "error": err}


def web_search(query: str, limit: int = 10, site: Optional[str] = None, time_filter: Optional[str] = None,
               region: str = "kr-kr", fetch_top: int = 0, max_chars_each: int = 5000) -> dict:
    """웹 검색. fetch_top>0이면 상위 N개 결과의 본문도 수집(fetch_page와 같은 방식)."""
    q = query.strip()
    if site:
        q += f" site:{site.strip()}"
    limit = max(1, min(limit, 30))
    attempts: list[dict] = []
    results: list[dict] = []
    engine = None

    url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(q)}&kl={region}"
    tf = TIME_MAP.get((time_filter or "").lower())
    if tf:
        url += f"&df={tf}"
    html, info = _get(url)
    attempts.append(info)
    if html:
        results = _parse_ddg_lite(html)
        engine = "duckduckgo"
    if not results:
        burl = f"https://www.bing.com/search?q={quote_plus(q)}&setlang=ko&cc=KR"
        if tf:
            burl += {"d": "&filters=ex1%3a%22ez1%22", "w": "&filters=ex1%3a%22ez2%22", "m": "&filters=ex1%3a%22ez3%22"}.get(tf, "")
        html, info = _get(burl)
        attempts.append(info)
        if html:
            results = _parse_bing(html)
            engine = "bing"

    seen, uniq = set(), []
    for r in results:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        uniq.append(r)
    results = uniq[:limit]
    if not results:
        return {"ok": False, "query": query, "reason": "no_results", "attempts": attempts,
                "fallback_hint": "검색 결과를 못 가져옴. 검색어를 바꿔 한 번 더 시도하고, 그래도 없으면 Firecrawl(firecrawl_search)로 넘길 것."}

    for r in results[:max(0, min(fetch_top, 10))]:
        got = core.fetch_auto(r["url"], max_chars=max_chars_each)
        if got.get("ok"):
            r["content"] = got.get("content")
            r["content_chars"] = got.get("chars")
        else:
            r["content_error"] = got.get("reason")
    return {"ok": True, "query": query, "engine": engine, "count": len(results), "results": results,
            "attempts": attempts}


# ---------------------------------------------------------------- 여러 주소 수집

def fetch_many(urls: list[str], max_chars_each: int = 6000, main_content_only: bool = True) -> dict:
    urls = [u.strip() for u in urls if u and u.strip()][:30]
    pages, failed = [], []
    for u in urls:
        r = core.fetch_auto(u, max_chars=max_chars_each, main_content_only=main_content_only)
        if r.get("ok"):
            pages.append({k: r.get(k) for k in ("url", "final_url", "title", "engine", "chars", "truncated", "content")})
        else:
            failed.append({"url": u, "reason": r.get("reason"), "fallback_hint": r.get("fallback_hint")})
    out = {"ok": bool(pages), "count": len(pages), "pages": pages, "failed": failed}
    if failed:
        out["note"] = "failed의 주소는 각 fallback_hint를 따를 것(대개 Firecrawl firecrawl_scrape)."
    return out


# ---------------------------------------------------------------- 사이트 주소 목록 (map)

def _same_site(u: str, host: str) -> bool:
    h = (urlparse(u).hostname or "").lower()
    return h == host or h.endswith("." + host) or host.endswith("." + h)


def _sitemap_urls(root: str, cap: int) -> list[str]:
    """robots.txt의 Sitemap: 줄과 /sitemap.xml 을 읽는다(하위 sitemap 1단계까지)."""
    found: list[str] = []
    maps = []
    page, _ = core.fetch_http(urljoin(root, "/robots.txt"))
    if page is not None and page.status == 200:
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", str(page.html_content))
    if not maps:
        maps = [urljoin(root, "/sitemap.xml")]
    queue = maps[:5]
    seen = set()
    while queue and len(found) < cap and len(seen) < 15:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        core.throttle(sm)
        page, _ = core.fetch_http(sm)
        if page is None or page.status != 200:
            continue
        body = str(getattr(page, "body", b"") or b"", "utf-8", "ignore") if isinstance(getattr(page, "body", None), bytes) \
            else str(page.html_content)
        locs = [l.strip() for l in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.S | re.I)]
        if "<sitemapindex" in body.lower():
            queue += locs[:10]
        else:
            found += locs
    return found[:cap]


def map_site(url: str, limit: int = 200, search: Optional[str] = None, use_sitemap: bool = True) -> dict:
    """사이트의 주소 목록. sitemap + 첫 페이지 링크. search가 있으면 주소에 그 단어가 들어간 것만."""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    limit = max(1, min(limit, 2000))
    urls: list[str] = []
    src = {}
    if use_sitemap:
        sm = _sitemap_urls(root, limit * 3)
        urls += sm
        src["sitemap"] = len(sm)
    r = core.fetch_page_obj(url)
    if r["ok"]:
        links = [u for u in core.page_links(r["page"]) if _same_site(u, host)]
        urls += links
        src["page_links"] = len(links)
    seen, out = set(), []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        if search and search.lower() not in unquote(u).lower():
            continue
        out.append(u)
    if not out:
        return {"ok": False, "url": url, "reason": r.get("reason") or "no_links", "sources": src,
                "fallback_hint": "주소를 못 찾음. Firecrawl(firecrawl_scrape)로 첫 페이지를 받아 링크를 확인할 것."}
    return {"ok": True, "url": url, "count": min(len(out), limit), "sources": src, "urls": out[:limit]}


# ---------------------------------------------------------------- 사이트 따라가며 수집 (crawl)

def crawl_site(url: str, max_pages: int = 15, same_path_only: bool = True, include: Optional[str] = None,
               exclude: Optional[str] = None, max_chars_each: int = 6000, max_depth: int = 2) -> dict:
    """시작 주소에서 같은 사이트 링크를 따라가며 여러 페이지를 수집.
    same_path_only=True면 시작 주소의 경로 아래만(예: /docs/ 아래). include/exclude는 주소에 포함될 단어."""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    base_path = urlparse(url).path.rsplit("/", 1)[0] + "/" if same_path_only else "/"
    max_pages = max(1, min(max_pages, 50))
    skip_ext = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|mp3|css|js|ico|woff2?)(\?|$)", re.I)

    def allowed(u: str) -> bool:
        p = urlparse(u)
        if not _same_site(u, host) or skip_ext.search(p.path):
            return False
        if not p.path.startswith(base_path):
            return False
        if include and include.lower() not in u.lower():
            return False
        if exclude and exclude.lower() in u.lower():
            return False
        return True

    queue: list[tuple[str, int]] = [(url, 0)]
    seen = {url}
    pages, failed = [], []
    while queue and len(pages) < max_pages:
        u, depth = queue.pop(0)
        r = core.fetch_page_obj(u)
        if not r["ok"]:
            failed.append({"url": u, "reason": r.get("reason")})
            if len(failed) >= 5 and not pages:
                break
            continue
        page = r["page"]
        md = core.to_markdown(page, None, True)
        content, cut = core.truncate(md, max_chars_each)
        pages.append({"url": u, "title": core.page_title(page), "engine": r["engine"], "chars": len(md),
                      "truncated": cut, "content": content})
        if depth < max_depth:
            for link in core.page_links(page):
                if link not in seen and allowed(link):
                    seen.add(link)
                    queue.append((link, depth + 1))
    out = {"ok": bool(pages), "start_url": url, "count": len(pages), "pages": pages,
           "not_visited": len(queue), "failed": failed}
    if not pages:
        out["fallback_hint"] = "시작 페이지부터 막힘. fetch_page의 fallback_hint를 따르거나 Firecrawl로 넘길 것."
    return out
