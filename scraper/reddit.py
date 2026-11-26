"""레딧 전용 수집 (www.reddit.com 새 화면 기준).

2026년 기준 확인된 사실 (가정용/모바일 IP에서 실측):
- old.reddit.com 은 비로그인 시 로그인 화면으로 돌려보냄 → 사용 안 함
- .json 은 403 → 사용 안 함
- www.reddit.com 의 서버 렌더링 HTML 과 /svc/shreddit/ 조각(partial) HTML 은 비로그인으로 열림
Firecrawl은 레딧을 지원하지 않으므로 대체 경로로 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin, urlparse

from . import core

WWW = "https://www.reddit.com"
HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
POST_RE = re.compile(r"/r/([^/]+)/comments/([a-z0-9]+)", re.I)


# ---------------------------------------------------------------- 공통

def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    m = re.search(r"-?\d[\d,]*", str(v))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _x(el, xpath: str) -> Optional[str]:
    try:
        v = el.xpath(xpath).get()
        return str(v).strip() if v is not None else None
    except Exception:
        return None


def _text(els, max_len: int = 0) -> str:
    try:
        if not els:
            return ""
        t = str(els[0].get_all_text(separator="\n", strip=True))
    except Exception:
        return ""
    if max_len and len(t) > max_len:
        t = t[:max_len] + "…"
    return t


def _abs(href: Optional[str]) -> Optional[str]:
    return urljoin(WWW, href) if href else None


def to_www(url: str) -> str:
    p = urlparse(url.strip() if "://" in url else "https://" + url.strip())
    host = (p.hostname or "").lower()
    if host.endswith("redd.it") and p.path.strip("/"):
        return f"{WWW}/comments/{p.path.strip('/')}/"
    q = f"?{p.query}" if p.query else ""
    return f"{WWW}{p.path or '/'}{q}"


def get_html(url: str, fragment: bool = False, allow_browser: bool = True, wait_selector: Optional[str] = None,
             browser_only: bool = False) -> dict:
    """HTTP 먼저, 막히면 브라우저. fragment=True면 조각 HTML이라 '빈 페이지' 판정을 하지 않는다.
    browser_only=True면 HTTP를 건너뛴다(첫 요청에서 HTTP가 막혔을 때 이어지는 페이지용).
    레딧이 HTTP를 계속 막는 동안(core.skip_http)에는 처음부터 브라우저를 쓴다."""
    attempts = []
    if browser_only or (allow_browser and core.skip_http(url)):
        return _browser_step(url, fragment, wait_selector, attempts)
    core.throttle(url)
    t0 = time.monotonic()
    page, err = core.fetch_http(url, headers=HEADERS)
    reason = None
    if page is None:
        reason = "network_error"
    elif "/login" in str(page.url):
        reason = "login_wall"
    else:
        tl = core.MIN_TEXT_CHARS if fragment else max(core.visible_text_len(page), 0)
        reason = core.classify(page.status, str(page.html_content), tl)
        if fragment and reason == "empty":
            reason = None
    will_retry = page is None or reason is not None
    will_retry = will_retry and allow_browser and reason != "not_found"
    attempts.append({"engine": "http", "status": getattr(page, "status", None), "reason": reason or "ok", "error": err})
    core.log_event(url=url, engine="reddit_http", status=getattr(page, "status", None), reason=reason or "ok",
                   secs=round(time.monotonic() - t0, 2), final=not will_retry)
    if page is not None and reason is None:
        return {"ok": True, "page": page, "engine": "http", "attempts": attempts}
    if not will_retry:
        return {"ok": False, "reason": reason, "attempts": attempts}
    res = _browser_step(url, fragment, wait_selector, attempts)
    if res["ok"] and reason in {"forbidden", "empty", "blocked", "captcha"}:
        core.mark_http_blocked(url)
    return res


def _browser_step(url: str, fragment: bool, wait_selector: Optional[str], attempts: list) -> dict:
    core.throttle(url)
    t0 = time.monotonic()
    page, err = core.fetch_browser(url, network_idle=False, wait_ms=2500, wait_selector=wait_selector)
    if page is None:
        reason = "browser_error"
    elif "/login" in str(page.url):
        reason = "login_wall"
    else:
        reason = core.classify(page.status, str(page.html_content), max(core.visible_text_len(page), core.MIN_TEXT_CHARS))
    attempts.append({"engine": "browser", "status": getattr(page, "status", None), "reason": reason or "ok", "error": err})
    core.log_event(url=url, engine="reddit_browser", status=getattr(page, "status", None), reason=reason or "ok",
                   secs=round(time.monotonic() - t0, 2), final=True)
    if page is not None and reason is None:
        return {"ok": True, "page": page, "engine": "browser", "attempts": attempts}
    return {"ok": False, "reason": reason, "attempts": attempts}


def _fail(res: dict, url: str) -> dict:
    return {
        "ok": False,
        "url": url,
        "reason": res.get("reason"),
        "attempts": res.get("attempts"),
        "fallback_hint": "레딧이 막힘(로그인 풀림 또는 차단). 다른 경로로 조용히 넘어가지 말고, 사용자에게 '수집기 전용 로그인 창을 열까요?'라고 먼저 요청할 것. 동의하면 open_browser_for_human('https://www.reddit.com/login', max_wait_s=300)으로 사용자가 직접 로그인하게 한 뒤 같은 요청을 재시도. 사용자가 자리에 없는 작업이면 창을 열지 말고 '로그인 필요'라고 보고. (Firecrawl은 레딧 미지원)",
    }


def next_partial(page, must_contain: str) -> Optional[str]:
    try:
        for fp in page.css("faceplate-partial"):
            src = fp.attrib.get("src") or ""
            if must_contain in src:
                return _abs(src)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 파서

def parse_posts(page, body_max: int = 1500) -> list[dict]:
    """서브레딧 목록·글 페이지의 <shreddit-post> 요소."""
    bodies: dict[str, str] = {}
    for b in page.css("shreddit-post-text-body"):
        pid = b.attrib.get("post-id")
        if pid and pid not in bodies:
            bodies[pid] = _text([b], body_max)
    posts, seen = [], set()
    for el in page.css("shreddit-post"):
        a = el.attrib
        pid = a.get("id")
        if not pid or pid in seen or a.get("is-promoted") is not None or a.get("promoted") is not None:
            continue
        seen.add(pid)
        posts.append({
            "id": pid,
            "title": a.get("post-title") or "",
            "subreddit": a.get("subreddit-name") or (a.get("subreddit-prefixed-name") or "").removeprefix("r/") or None,
            "author": a.get("author"),
            "score": _int(a.get("score")),
            "comments": _int(a.get("comment-count")),
            "created_utc": a.get("created-timestamp"),
            "permalink": _abs(a.get("permalink")),
            "link_url": a.get("content-href"),
            "post_type": a.get("post-type"),
            "selftext": bodies.get(pid) or None,
        })
    return posts


def parse_search(page) -> list[dict]:
    """검색 결과의 search-telemetry-tracker[data-testid=search-sdui-post] 요소."""
    out, seen = [], set()
    for el in page.css('search-telemetry-tracker[data-testid="search-sdui-post"]'):
        try:
            ctx = json.loads(el.attrib.get("data-faceplate-tracking-context") or "{}")
        except Exception:
            ctx = {}
        post = ctx.get("post") or {}
        pid = post.get("id") or el.attrib.get("data-thingid")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        href = _x(el, './/a[@data-testid="post-title-text"]/@href') or _x(el, './/a[@data-testid="post-title"]/@href')
        nums = el.xpath('.//div[@data-testid="search-counter-row"]//faceplate-number/@number')
        nums = [_int(n) for n in (nums.getall() if hasattr(nums, "getall") else nums)]
        out.append({
            "id": pid,
            "title": post.get("title") or _text(el.xpath('.//a[@data-testid="post-title-text"]')),
            "subreddit": (ctx.get("subreddit") or {}).get("name"),
            "author": (ctx.get("profile") or {}).get("name"),
            "score": nums[0] if len(nums) > 0 else None,
            "comments": nums[1] if len(nums) > 1 else None,
            "created_utc": _x(el, ".//faceplate-timeago/@ts"),
            "permalink": _abs(href),
            "nsfw": bool(post.get("nsfw")),
        })
    return out


def parse_comments(page, body_max: int = 2000) -> list[dict]:
    """<shreddit-comment> 를 문서 순서대로 읽어 depth 로 트리를 만든다."""
    flat = []
    for el in page.css("shreddit-comment"):
        a = el.attrib
        tid = a.get("thingid")
        if not tid:
            continue
        body = _text(page.xpath(f'//div[@id="{tid}-comment-rtjson-content"]'), body_max)
        flat.append({
            "id": tid,
            "author": a.get("author") or "[deleted]",
            "score": _int(a.get("score")),
            "created_utc": a.get("created"),
            "depth": _int(a.get("depth")) or 0,
            "body": body or ("[deleted]" if a.get("is-comment-deleted") is not None else ""),
        })
    return flat


def build_tree(flat: list[dict], max_depth: int) -> list[dict]:
    roots: list[dict] = []
    stack: list[dict] = []
    for c in flat:
        node = {k: v for k, v in c.items() if k != "depth"}
        d = c["depth"]
        if d >= max_depth:
            continue
        while stack and stack[-1]["_d"] >= d:
            stack.pop()
        node["_d"] = d
        if stack:
            stack[-1].setdefault("replies", []).append(node)
        else:
            roots.append(node)
        stack.append(node)

    def clean(n):
        n.pop("_d", None)
        for r in n.get("replies", []):
            clean(r)
        return n

    return [clean(r) for r in roots]


# ---------------------------------------------------------------- 공개 함수

def listing(subreddit: str, sort: str = "hot", time_filter: str = "week", limit: int = 25) -> dict:
    sub = subreddit.strip().removeprefix("r/").strip("/")
    sort = sort if sort in {"hot", "new", "top", "rising", "controversial", "best"} else "hot"
    url = f"{WWW}/r/{sub}/{sort}/"
    if sort in {"top", "controversial"}:
        url += f"?t={time_filter}"
    limit = max(1, min(limit, 100))
    res = get_html(url, wait_selector="shreddit-post")
    if not res["ok"]:
        return _fail(res, url)
    attempts = list(res["attempts"])
    posts = parse_posts(res["page"])
    seen = {p["id"] for p in posts}
    nxt = next_partial(res["page"], "more-posts")
    via_browser = res["engine"] == "browser"
    pages = 0
    while nxt and len(posts) < limit and pages < 6:
        r = get_html(nxt, fragment=True, allow_browser=False, browser_only=via_browser)
        attempts.extend(r["attempts"])
        if not r["ok"]:
            break
        got = [p for p in parse_posts(r["page"]) if p["id"] not in seen]
        if not got:
            break
        posts.extend(got)
        seen.update(p["id"] for p in got)
        nxt = next_partial(r["page"], "more-posts")
        pages += 1
    return {"ok": True, "url": url, "engine": res["engine"], "count": min(len(posts), limit),
            "posts": posts[:limit], "attempts": attempts}


def search(query: str, subreddit: Optional[str] = None, sort: str = "relevance", time_filter: str = "year",
           limit: int = 25) -> dict:
    q = quote_plus(query)
    sort = sort if sort in {"relevance", "hot", "top", "new", "comments"} else "relevance"
    if subreddit:
        sub = subreddit.strip().removeprefix("r/").strip("/")
        url = f"{WWW}/r/{sub}/search/?q={q}&type=posts&restrict_sr=1&sort={sort}&t={time_filter}"
    else:
        url = f"{WWW}/search/?q={q}&type=posts&sort={sort}&t={time_filter}"
    limit = max(1, min(limit, 100))
    res = get_html(url)
    if not res["ok"]:
        return _fail(res, url)
    attempts = list(res["attempts"])
    posts = parse_search(res["page"])
    seen = {p["id"] for p in posts}
    nxt = next_partial(res["page"], "cursor=")
    via_browser = res["engine"] == "browser"
    pages = 0
    while nxt and len(posts) < limit and pages < 6:
        r = get_html(nxt, fragment=True, allow_browser=False, browser_only=via_browser)
        attempts.extend(r["attempts"])
        if not r["ok"]:
            break
        got = [p for p in parse_search(r["page"]) if p["id"] not in seen]
        if not got:
            break
        posts.extend(got)
        seen.update(p["id"] for p in got)
        nxt = next_partial(r["page"], "cursor=")
        pages += 1
    out = {"ok": True, "url": url, "engine": res["engine"], "count": min(len(posts), limit),
           "posts": posts[:limit], "attempts": attempts}
    if not posts:
        out["note"] = "검색 결과 0건. 레딧이 검색어를 자동 교정했을 수 있음. 다른 표현이나 subreddit 지정으로 재시도."
    return out


def post(url: str, sort: str = "top", comment_limit: int = 150, max_depth: int = 6) -> dict:
    m = POST_RE.search(url)
    if not m:
        return {"ok": False, "url": url, "reason": "invalid_url", "hint": "https://www.reddit.com/r/<sub>/comments/<id>/... 형식이어야 함"}
    sub, pid = m.group(1), m.group(2).lower()
    sort = sort if sort in {"top", "best", "new", "controversial", "old", "qa"} else "top"
    comment_limit = max(0, min(comment_limit, 500))
    attempts: list = []

    # 1) 글 본문: HTTP 페이지에 <shreddit-post>가 있으면 사용, 없으면 브라우저로 렌더링
    post_url = f"{WWW}/r/{sub}/comments/{pid}/"
    info: dict[str, Any] = {}
    r = get_html(post_url, wait_selector="shreddit-post")
    attempts.extend(r["attempts"])
    posts = parse_posts(r["page"], body_max=20_000) if r["ok"] else []
    if not posts and r.get("engine") == "http":
        # HTTP 화면에 글 요소가 없으면 브라우저로 다시 그린다
        rb = _browser_step(post_url, False, "shreddit-post", attempts)
        if rb["ok"]:
            posts = parse_posts(rb["page"], body_max=20_000)
    if posts:
        info = posts[0]
        # 글 페이지에서는 본문이 shreddit-post 안의 다른 슬롯에 있을 수 있음
    # 2) 댓글: 조각 HTML
    flat: list[dict] = []
    if comment_limit:
        curl = f"{WWW}/svc/shreddit/comments/r/{sub}/t3_{pid}?sort={sort}"
        rc = get_html(curl, fragment=True, allow_browser=True)
        via_browser = rc.get("engine") == "browser"
        attempts.extend(rc["attempts"])
        if rc["ok"]:
            flat = parse_comments(rc["page"])
            nxt = next_partial(rc["page"], "top-level=1")
            pages = 0
            while nxt and len(flat) < comment_limit and pages < 5:
                rn = get_html(nxt, fragment=True, allow_browser=False, browser_only=via_browser)
                attempts.extend(rn["attempts"])
                if not rn["ok"]:
                    break
                more = parse_comments(rn["page"])
                if not more:
                    break
                flat.extend(more)
                nxt = next_partial(rn["page"], "top-level=1")
                pages += 1
    flat = flat[:comment_limit]
    if not info and not flat:
        return _fail({"reason": "no_content", "attempts": attempts}, url)
    out = {"ok": True, "url": url, **info, "comment_tree": build_tree(flat, max(1, min(max_depth, 10))),
           "comments_collected": len(flat), "attempts": attempts}
    if not info:
        out["warning"] = "글 본문을 못 가져옴(댓글만 수집). fetch_page(mode='browser')로 원문 확인 가능."
    return out
