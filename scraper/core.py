"""수집 엔진 핵심.

단계
  1) HTTP 단계  : Scrapling Fetcher (브라우저 TLS 지문 위장, 브라우저 없이 빠름)
  2) 브라우저 단계: Scrapling StealthyFetcher (patchright 스텔스 크롬, 쿠키 유지 프로필)
  실패 시       : 실패 이유와 다음 행동(fallback_hint)을 돌려줌 → Claude가 Firecrawl 등으로 넘김

캡차는 자동으로 풀지 않는다. 감지되면 멈추고 open_browser_for_human()으로 사람에게 넘긴다.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SCRAPER_DATA_DIR", BASE_DIR / "data"))
PROFILE_DIR = DATA_DIR / "browser-profile"
LOG_FILE = DATA_DIR / "fetch_log.jsonl"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 데이터 절약: 브라우저 단계에서 이미지·폰트·영상 차단 (기본 켬)
SAVE_DATA = os.environ.get("SCRAPER_SAVE_DATA", "1") != "0"

# 도메인별 요청 간격(초). 차단을 피하는 가장 중요한 설정.
DOMAIN_DELAY: dict[str, tuple[float, float]] = {
    "reddit.com": (3.0, 6.0),
    "naver.com": (1.5, 3.0),
    "kakao.com": (1.5, 3.0),
}
DEFAULT_DELAY = (1.0, 2.5)

MIN_TEXT_CHARS = 200  # 이보다 짧으면 '빈 페이지(JS 렌더링 필요)'로 판단

CAPTCHA_MARKERS = (
    "cf-turnstile",
    "challenges.cloudflare.com",
    "g-recaptcha",
    "h-captcha",
    "hcaptcha.com",
    "captcha-delivery.com",
    "px-captcha",
    "verify you are human",
    "are you a robot",
    "로봇이 아닙니다",
    "자동입력 방지",
)
BLOCK_MARKERS = (
    "just a moment...",
    "attention required! | cloudflare",
    "access denied",
    "you've been blocked",
    "blocked by network security",
    "request blocked",
    "whoa there, pardner",
)

# HTTP 단계가 계속 막히는 사이트는 한동안 HTTP를 건너뛰고 바로 브라우저로 간다(시간·요청 낭비 방지).
SKIP_HTTP_HOURS = float(os.environ.get("SCRAPER_SKIP_HTTP_HOURS", "6"))
STATE_FILE = DATA_DIR / "domain_state.json"
_state_lock = threading.Lock()

_throttle_lock = threading.Lock()
_next_allowed: dict[str, float] = {}
# 브라우저 프로필은 동시에 하나만 열 수 있으므로 잠금
browser_lock = threading.Lock()


# ---------------------------------------------------------------- 유틸

def domain_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "or", "go", "ac", "ne", "re", "pe"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def throttle(url: str) -> float:
    """도메인별 최소 간격을 지키도록 대기. 실제 대기한 초를 반환."""
    key = domain_key(url)
    lo, hi = DOMAIN_DELAY.get(key, DEFAULT_DELAY)
    with _throttle_lock:
        now = time.monotonic()
        start = max(now, _next_allowed.get(key, 0.0))
        _next_allowed[key] = start + random.uniform(lo, hi)
    wait = start - now
    if wait > 0:
        time.sleep(wait)
    return wait


def log_event(**fields: Any) -> None:
    fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def skip_http(url: str) -> bool:
    """이 도메인이 최근 HTTP 단계에서 막히고 브라우저로는 성공했으면 True."""
    until = (_load_state().get("skip_http") or {}).get(domain_key(url), 0)
    return time.time() < float(until or 0)


def mark_http_blocked(url: str) -> None:
    """HTTP는 막혔는데 브라우저는 성공한 경우 호출. SKIP_HTTP_HOURS 동안 HTTP를 건너뛴다."""
    if SKIP_HTTP_HOURS <= 0:
        return
    with _state_lock:
        st = _load_state()
        st.setdefault("skip_http", {})[domain_key(url)] = time.time() + SKIP_HTTP_HOURS * 3600
        try:
            STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def find_chrome() -> Optional[str]:
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def use_real_chrome() -> bool:
    v = os.environ.get("SCRAPER_REAL_CHROME", "auto").lower()
    if v in {"1", "true", "yes"}:
        return True
    if v in {"0", "false", "no"}:
        return False
    return find_chrome() is not None


def classify(status: Optional[int], html: str, text_len: int) -> Optional[str]:
    """None이면 정상. 아니면 실패 이유 문자열."""
    low = (html or "")[:300_000].lower()
    if any(m in low for m in CAPTCHA_MARKERS) and text_len < 20_000:
        return "captcha"
    if not status:
        return "network_error"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not_found"
    if status >= 500:
        return "server_error"
    if any(m in low for m in BLOCK_MARKERS) and text_len < 5_000:
        return "blocked"
    # 글자가 거의 없는데 HTML은 큰 경우 = 자바스크립트로 그리는 페이지(브라우저 필요).
    # HTML 자체가 작으면 원래 짧은 정상 페이지로 본다.
    if text_len < MIN_TEXT_CHARS and len(html or "") > 3_000:
        return "empty"
    return None


def visible_text_len(page: Any) -> int:
    try:
        return len(page.get_all_text(separator=" ", strip=True))
    except Exception:
        return 0


def page_title(page: Any) -> str:
    try:
        return str(page.css("title::text").get() or "").strip()
    except Exception:
        return ""


def to_markdown(page: Any, css_selector: Optional[str], main_content_only: bool) -> str:
    """본문만 추려 마크다운으로. trafilatura → 실패 시 Scrapling 변환."""
    if css_selector:
        try:
            return page.markdown(css_selector=css_selector)
        except Exception:
            pass
    if main_content_only:
        try:
            import trafilatura

            md = trafilatura.extract(
                str(page.html_content),
                url=getattr(page, "url", None),
                output_format="markdown",
                include_links=True,
                include_tables=True,
                favor_recall=True,
            )
            if md and len(md) >= MIN_TEXT_CHARS:
                return md
        except Exception:
            pass
    try:
        return page.markdown(main_content_only=main_content_only)
    except Exception:
        return page.get_all_text(separator="\n", strip=True)


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n\n…(잘림)", True
    return text, False


# ---------------------------------------------------------------- 단계별 수집

def fetch_http(url: str, headers: Optional[dict] = None, cookies: Optional[dict] = None, timeout: int = 25):
    """1단계. 실패하면 (None, 오류문자열)."""
    from scrapling.fetchers import Fetcher

    try:
        page = Fetcher.get(
            url,
            impersonate="chrome",
            stealthy_headers=True,
            follow_redirects=True,
            timeout=timeout,
            retries=1,
            headers=headers or {},
            cookies=cookies or {},
        )
        return page, None
    except Exception as e:  # 네트워크 오류 등
        return None, f"{type(e).__name__}: {e}"[:300]


def fetch_browser(
    url: str,
    *,
    headless: bool = True,
    wait_selector: Optional[str] = None,
    wait_ms: int = 0,
    network_idle: bool = True,
    page_setup: Optional[Callable] = None,
    page_action: Optional[Callable] = None,
    disable_resources: Optional[bool] = None,
    timeout_ms: int = 45_000,
):
    """2단계. 쿠키·로그인이 유지되는 전용 프로필(data/browser-profile)을 쓴다."""
    from scrapling.fetchers import StealthyFetcher

    kwargs: dict[str, Any] = dict(
        headless=headless,
        disable_resources=SAVE_DATA if disable_resources is None else disable_resources,
        network_idle=network_idle,
        user_data_dir=str(PROFILE_DIR),
        real_chrome=use_real_chrome(),
        solve_cloudflare=False,  # 캡차 자동 풀이는 하지 않음
        block_ads=True,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        timeout=timeout_ms,
    )
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
    if wait_ms:
        kwargs["wait"] = wait_ms
    if page_setup:
        kwargs["page_setup"] = page_setup
    if page_action:
        kwargs["page_action"] = page_action
    with browser_lock:
        try:
            return StealthyFetcher.fetch(url, **kwargs), None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"[:300]


# ---------------------------------------------------------------- 통합 수집

def _fallback_hint(url: str, reason: Optional[str]) -> str:
    if "reddit.com" in domain_key(url):
        if reason == "captcha":
            return "open_browser_for_human으로 직접 통과/로그인한 뒤 mode='browser'로 재시도. (Firecrawl은 레딧 미지원)"
        if reason in {"forbidden", "login_wall"}:
            return "레딧이 막힘(로그인 풀림 또는 차단). 다른 경로로 조용히 넘어가지 말고, 사용자에게 '수집기 전용 로그인 창을 열까요?'라고 먼저 요청할 것. 동의하면 open_browser_for_human('https://www.reddit.com/login', max_wait_s=300)으로 사용자가 직접 로그인하게 한 뒤 같은 요청을 재시도. 사용자가 자리에 없는 작업이면 창을 열지 말고 '로그인 필요'라고 보고. (Firecrawl은 레딧 미지원)"
        return "reddit_search / reddit_listing / reddit_post 도구를 쓰거나 open_browser_for_human으로 레딧 로그인 후 재시도. (Firecrawl은 레딧 미지원)"
    if reason == "captcha":
        return "open_browser_for_human(url)로 사람이 직접 확인을 통과한 뒤 mode='browser'로 재시도하거나, Firecrawl(firecrawl_scrape)로 넘길 것."
    if reason in {"forbidden", "login_wall"}:
        return ("로그인하면 풀릴 수 있는 차단. Firecrawl로 넘기기 전에 사용자에게 '수집기 전용 로그인 창을 열까요?'라고 먼저 요청하고, "
                "동의하면 open_browser_for_human(로그인 주소, max_wait_s=300) 후 mode='browser'로 재시도. "
                "사용자가 없거나 거절하면 Firecrawl(firecrawl_scrape)로 같은 URL을 수집.")
    if reason in {"not_found"}:
        return "주소가 맞는지 확인. 재시도 불필요."
    return "Firecrawl(firecrawl_scrape)로 같은 URL을 수집할 것."


def _result(url: str, page: Any, engine: str, css_selector, main_content_only, max_chars) -> dict:
    md = to_markdown(page, css_selector, main_content_only)
    content, cut = truncate(md, max_chars)
    return {
        "ok": True,
        "url": url,
        "final_url": str(getattr(page, "url", url)),
        "status": getattr(page, "status", None),
        "engine": engine,
        "title": page_title(page),
        "chars": len(md),
        "truncated": cut,
        "content": content,
    }


def fetch_page_obj(url: str, mode: str = "auto", wait_selector: Optional[str] = None) -> dict:
    """페이지 객체를 가져온다. 성공: {ok, page, engine, attempts}, 실패: {ok:False, reason, attempts, fallback_hint}.
    mode: auto(HTTP→브라우저, 단 HTTP가 계속 막히는 사이트는 바로 브라우저) | http | browser"""
    attempts: list[dict] = []
    http_failed = False
    if mode == "http" or (mode == "auto" and not skip_http(url)):
        throttle(url)
        t0 = time.monotonic()
        page, err = fetch_http(url)
        if page is not None:
            reason = classify(page.status, str(page.html_content), visible_text_len(page))
        else:
            reason = "network_error"
        attempts.append({"engine": "http", "status": getattr(page, "status", None), "reason": reason or "ok", "error": err})
        log_event(url=url, engine="http", status=getattr(page, "status", None), reason=reason or "ok",
                  secs=round(time.monotonic() - t0, 2), final=(reason is None or mode == "http"
                                                              or reason in {"not_found", "rate_limited"}))
        if page is not None and reason is None:
            return {"ok": True, "page": page, "engine": "http", "attempts": attempts}
        # 404·속도제한이면 브라우저로 넘겨도 의미 없음
        if mode == "http" or reason in {"not_found", "rate_limited"}:
            return {"ok": False, "url": url, "reason": reason, "attempts": attempts,
                    "fallback_hint": _fallback_hint(url, reason)}
        http_failed = reason in {"forbidden", "empty", "blocked", "captcha"}

    throttle(url)
    t0 = time.monotonic()
    page, err = fetch_browser(url, wait_selector=wait_selector)
    if page is not None:
        reason = classify(page.status, str(page.html_content), visible_text_len(page))
    else:
        reason = "browser_error"
    attempts.append({"engine": "browser", "status": getattr(page, "status", None), "reason": reason or "ok", "error": err})
    log_event(url=url, engine="browser", status=getattr(page, "status", None), reason=reason or "ok",
              secs=round(time.monotonic() - t0, 2), final=True)
    if page is not None and reason is None:
        if http_failed:
            mark_http_blocked(url)
        return {"ok": True, "page": page, "engine": "browser", "attempts": attempts}
    return {"ok": False, "url": url, "reason": reason, "attempts": attempts,
            "fallback_hint": _fallback_hint(url, reason)}


def fetch_auto(
    url: str,
    mode: str = "auto",
    css_selector: Optional[str] = None,
    main_content_only: bool = True,
    max_chars: int = 20_000,
    wait_selector: Optional[str] = None,
) -> dict:
    """mode: auto(HTTP→브라우저) | http | browser"""
    r = fetch_page_obj(url, mode, wait_selector)
    if not r["ok"]:
        return r
    res = _result(url, r["page"], r["engine"], css_selector, main_content_only, max_chars)
    res["attempts"] = r["attempts"]
    return res


def page_links(page: Any) -> list[str]:
    """페이지 안의 링크(절대 주소, #조각 제거, 중복 제거)."""
    from urllib.parse import urljoin, urldefrag

    base = str(getattr(page, "url", "") or "")
    out, seen = [], set()
    try:
        hrefs = page.css("a::attr(href)").getall()
    except Exception:
        hrefs = []
    for h in hrefs:
        h = str(h or "").strip()
        if not h or h.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        u = urldefrag(urljoin(base, h))[0]
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ---------------------------------------------------------------- 네트워크 JSON 캡처

def parse_jsonish(text: str) -> Any:
    """JSON 또는 JSONP(callback(...)) 응답을 파싱."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.match(r"^[\w$.]+\s*\((.*)\)\s*;?\s*$", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def capture_json(
    url: str,
    url_contains: list[str],
    wait_ms: int = 4000,
    scroll_times: int = 0,
    max_items: int = 10,
    max_chars_each: int = 30_000,
) -> dict:
    """브라우저로 페이지를 열고, 페이지가 내부적으로 받아오는 JSON 응답을 가로챈다.
    url_contains 중 하나라도 응답 URL에 포함되면 수집. 빈 리스트면 모든 JSON 응답."""
    seen: list[Any] = []
    out: list[dict] = []
    all_urls: list[str] = []

    def setup(page):
        page.on("response", lambda resp: seen.append(resp))

    def action(page):
        page.wait_for_timeout(wait_ms)
        for _ in range(max(0, scroll_times)):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1200)
        for resp in list(seen):
            try:
                rurl = resp.url
                ctype = (resp.headers or {}).get("content-type", "")
            except Exception:
                continue
            if "json" not in ctype and "javascript" not in ctype:
                continue
            all_urls.append(rurl[:300])
            if url_contains and not any(k in rurl for k in url_contains):
                continue
            if len(out) >= max_items:
                break
            try:
                body = resp.text()
            except Exception:
                continue
            data = parse_jsonish(body)
            if data is None:
                continue
            raw = json.dumps(data, ensure_ascii=False)
            if len(raw) > max_chars_each:
                out.append({"url": rurl, "status": resp.status, "truncated": True, "json_text": raw[:max_chars_each]})
            else:
                out.append({"url": rurl, "status": resp.status, "truncated": False, "data": data})

    throttle(url)
    t0 = time.monotonic()
    page, err = fetch_browser(url, network_idle=False, page_setup=setup, page_action=action)
    reason = None
    if page is None:
        reason = "browser_error"
    else:
        reason = classify(page.status, str(page.html_content), max(visible_text_len(page), MIN_TEXT_CHARS))
    log_event(url=url, engine="capture_json", status=getattr(page, "status", None),
              reason=reason or "ok", captured=len(out), secs=round(time.monotonic() - t0, 2))
    res = {
        "ok": bool(out),
        "url": url,
        "status": getattr(page, "status", None),
        "reason": reason if not out else None,
        "error": err,
        "captured": out,
    }
    if not out:
        # 패턴이 틀렸을 때 다음 시도를 돕도록 관측된 JSON 주소 일부를 돌려줌
        res["observed_json_urls"] = sorted(set(all_urls))[:40]
        res["fallback_hint"] = _fallback_hint(url, reason) if reason else "url_contains 패턴을 observed_json_urls 참고해 바꿔서 재시도."
    return res


# ---------------------------------------------------------------- 사람에게 넘기기

def open_browser_for_human(url: str, max_wait_s: int = 90) -> dict:
    """화면에 브라우저를 띄워 사람이 캡차 통과·로그인을 직접 하게 한다.
    통과 결과(쿠키)는 전용 프로필에 저장되어 이후 브라우저 단계 수집에 쓰인다."""
    state = {"cleared": False, "elapsed": 0.0}

    def action(page):
        start = time.monotonic()
        page.wait_for_timeout(3000)
        while time.monotonic() - start < max_wait_s:
            try:
                html = page.content().lower()
            except Exception:
                break
            if not any(m in html for m in CAPTCHA_MARKERS) and not any(m in html for m in BLOCK_MARKERS):
                state["cleared"] = True
                # 로그인 페이지라면 사람이 로그인할 시간을 조금 더 준다
                if "login" in page.url.lower():
                    state["cleared"] = False
                else:
                    break
            page.wait_for_timeout(2000)
        state["elapsed"] = round(time.monotonic() - start, 1)

    page, err = fetch_browser(
        url, headless=False, network_idle=False, page_action=action,
        disable_resources=False, timeout_ms=max(60_000, (max_wait_s + 30) * 1000),
    )
    log_event(url=url, engine="human", status=getattr(page, "status", None),
              reason="cleared" if state["cleared"] else "not_cleared")
    return {
        "ok": state["cleared"],
        "url": url,
        "final_url": str(getattr(page, "url", url)) if page is not None else None,
        "waited_s": state["elapsed"],
        "error": err,
        "note": "통과/로그인 상태는 전용 브라우저 프로필에 저장됨. 이제 mode='browser'로 다시 수집하면 됨."
        if state["cleared"] else "시간 안에 통과되지 않음. 다시 호출하거나 Firecrawl로 넘길 것.",
    }


# ---------------------------------------------------------------- 통계

def recent_stats(last_n: int = 300) -> dict:
    """요청 단위 최종 결과로 집계한다.
    HTTP 단계가 막혔어도 같은 주소가 곧이어(3분 안) 브라우저로 성공했으면 '성공'으로 센다.
    first_try_blocked = 첫 시도(HTTP)가 막힌 횟수(참고용)."""
    if not LOG_FILE.exists():
        return {"total": 0, "by_domain": {}}
    lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-last_n:]
    evs = []
    for ln in lines:
        try:
            ev = json.loads(ln)
            ev["_t"] = datetime.fromisoformat(ev.get("ts")).timestamp() if ev.get("ts") else 0
            evs.append(ev)
        except Exception:
            continue
    by: dict[str, dict[str, int]] = {}
    for i, ev in enumerate(evs):
        d = by.setdefault(domain_key(ev.get("url", "")), {"ok": 0, "fail": 0})
        reason = ev.get("reason")
        if reason in {"ok", "cleared"}:
            d["ok"] += 1
            continue
        # 뒤따르는 다른 단계가 같은 주소를 성공시켰는가?
        recovered = False
        if ev.get("final") is not True:
            for nxt in evs[i + 1:i + 6]:
                if nxt.get("url") == ev.get("url") and nxt["_t"] - ev["_t"] <= 180:
                    if nxt.get("reason") in {"ok", "cleared"}:
                        recovered = True
                    break
        if recovered:
            d["first_try_blocked"] = d.get("first_try_blocked", 0) + 1
            continue
        if ev.get("final") is False:
            # 다음 단계로 넘어갔지만 기록이 없는 경우(옛 기록). 실패로 센다.
            pass
        d["fail"] += 1
        d[reason or "unknown"] = d.get(reason or "unknown", 0) + 1
    st = _load_state().get("skip_http") or {}
    now = time.time()
    return {
        "total_log_lines": len(lines),
        "by_domain": dict(sorted(by.items(), key=lambda kv: -kv[1]["fail"])),
        "skip_http_now": {k: round((v - now) / 3600, 1) for k, v in st.items() if v > now},
        "real_chrome": use_real_chrome(),
        "save_data": SAVE_DATA,
        "log_file": str(LOG_FILE),
        "note": "ok/fail은 요청의 최종 결과. first_try_blocked는 첫 시도(HTTP)만 막히고 브라우저로 성공한 횟수. "
                "skip_http_now는 HTTP를 건너뛰고 바로 브라우저를 쓰는 도메인(남은 시간)."
    }
