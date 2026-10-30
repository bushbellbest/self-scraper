"""네이버 지도·카카오맵 전용 수집.

실측(2026-10-30, 가정용 IP) 결과:
- 네이버: m.place.naver.com/restaurant/list?query=… 와 /restaurant/<id>/home 의 HTML 안
  window.__APOLLO_STATE__ JSON 에 목록·상세(리뷰 수, 영업시간 등)가 들어 있음 → HTTP만으로 수집(가벼움)
- 카카오: map.kakao.com 검색 시 내부 API(mapsearch/map) 응답에 평점·리뷰 수·전화·주소가 들어 있음 → 브라우저로 가로챔
내부 구조는 예고 없이 바뀔 수 있다. 바뀌면 *_raw 도구나 capture_json 의 observed_json_urls 로 확인한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote

from . import core

APOLLO_RE = re.compile(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*(?:window\.|</script>)", re.S)
NAVER_HEADERS = {"Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://m.place.naver.com/"}


def _place_id(v: str) -> Optional[str]:
    m = re.search(r"(\d{5,})", v or "")
    return m.group(1) if m else None


def _scalars(d: dict, max_len: int = 300) -> dict:
    out = {}
    for k, v in d.items():
        if k.startswith("__"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v[:max_len] if isinstance(v, str) else v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v[:20]):
            out[k] = v[:20]
    return out


def _num(v: Any) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        f = float(str(v).replace(",", ""))
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def extract_apollo(html: str) -> Optional[dict]:
    m = APOLLO_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _naver_get(url: str):
    core.throttle(url)
    page, err = core.fetch_http(url, headers=NAVER_HEADERS)
    state = extract_apollo(str(page.html_content)) if page is not None else None
    if not state:
        # 차단·구조 변경 시 브라우저로 한 번 더
        core.throttle(url)
        page, err2 = core.fetch_browser(url, network_idle=False, wait_ms=2500)
        state = extract_apollo(str(page.html_content)) if page is not None else None
        err = err or err2
    core.log_event(url=url, engine="naver", status=getattr(page, "status", None), reason="ok" if state else "no_apollo")
    return state, page, err


# ---------------------------------------------------------------- 네이버 파서

def parse_naver_list(state: dict) -> list[dict]:
    items = []
    for k, v in state.items():
        if not isinstance(v, dict) or v.get("__typename") != "PlaceListBusinessesItem":
            continue
        items.append({
            "id": v.get("id"),
            "name": v.get("name"),
            "category": v.get("category"),
            "road_address": v.get("roadAddress"),
            "address": v.get("fullAddress") or v.get("address"),
            "phone": v.get("phone") or v.get("virtualPhone"),
            "visitor_reviews": _num(v.get("visitorReviewCount")),
            "visitor_score": _num(v.get("visitorReviewScore")),
            "blog_reviews": _num(v.get("blogCafeReviewCount")),
            "options": v.get("options"),
            "new_opening": v.get("newOpening"),
            "lat": _num(v.get("y")),
            "lng": _num(v.get("x")),
            "url": f"https://m.place.naver.com/restaurant/{v.get('id')}/home" if v.get("id") else None,
        })
    return items


def parse_naver_place(state: dict, pid: str) -> dict:
    base = None
    stats = None
    for k, v in state.items():
        if not isinstance(v, dict):
            continue
        if k.startswith("PlaceDetailBase:") and pid in k:
            base = v
        elif k.startswith("VisitorReviewStatsResult:") and pid in k:
            stats = v
    detail = {}
    rq = state.get("ROOT_QUERY") or {}
    for k, v in rq.items():
        if k.startswith("placeDetail(") and isinstance(v, dict):
            detail = v
            break
    if not base:
        return {}
    out = {
        "id": pid,
        "name": base.get("name"),
        "category": base.get("category"),
        "road_address": base.get("roadAddress"),
        "address": base.get("address"),
        "phone": base.get("phone") or base.get("virtualPhone"),
        "visitor_reviews": _num(base.get("visitorReviewsTotal")),
        "visitor_text_reviews": _num(base.get("visitorReviewsTextReviewTotal")),
        "visitor_score": _num(base.get("visitorReviewsScore")),
        "blog_reviews": _num(base.get("cafeBlogReviewsTotal")),
        "conveniences": base.get("conveniences"),
        "payment": base.get("paymentInfo"),
        "directions": base.get("road"),
    }
    if stats:
        rv = stats.get("review") or {}
        out["avg_rating"] = _num(rv.get("avgRating"))
        themes = (stats.get("analysis") or {}).get("themes") or []
        out["review_themes"] = [{"label": t.get("label"), "count": t.get("count")} for t in themes[:10]]
    hours = detail.get("newBusinessHours") or []
    if hours:
        h0 = hours[0] or {}
        st = h0.get("businessStatusDescription") or {}
        out["status_now"] = " / ".join(x for x in (st.get("status"), st.get("description")) if x)
        week = []
        for d in h0.get("businessHours") or []:
            bh = d.get("businessHours") or {}
            line = f"{d.get('day')} {bh.get('start', '')}-{bh.get('end', '')}".strip()
            brk = d.get("breakHours") or []
            if brk:
                line += " (브레이크 " + ", ".join(f"{b.get('start')}-{b.get('end')}" for b in brk) + ")"
            if d.get("description"):
                line += f" [{d['description']}]"
            week.append(line)
        out["hours"] = week
    out["url"] = f"https://m.place.naver.com/restaurant/{pid}/home"
    return out


# ---------------------------------------------------------------- 네이버 공개 함수

def naver_search(query: str, max_items: int = 15) -> dict:
    url = f"https://m.place.naver.com/restaurant/list?query={quote(query)}"
    state, page, err = _naver_get(url)
    if not state:
        return {"ok": False, "query": query, "reason": "no_data", "error": err,
                "fallback_hint": "fetch_page(url, mode='browser') 또는 Firecrawl로 같은 URL 시도."}
    items = parse_naver_list(state)[:max_items]
    res = {"ok": bool(items), "query": query, "engine": "http", "count": len(items), "places": items,
           "note": "서버가 첫 화면에 넣어 주는 항목만(보통 4~10개). id를 naver_place에 넣으면 영업시간 등 상세."}
    if not items:
        res["hint"] = "목록 항목을 못 찾음. naver_place_raw 로 구조 확인 필요."
    return res


def naver_place(place: str) -> dict:
    pid = _place_id(place)
    if not pid:
        return {"ok": False, "reason": "invalid_place_id", "hint": "네이버 플레이스 숫자 ID나 URL을 넣을 것."}
    url = f"https://m.place.naver.com/restaurant/{pid}/home"
    state, page, err = _naver_get(url)
    if not state:
        return {"ok": False, "place_id": pid, "reason": "no_data", "error": err,
                "fallback_hint": "fetch_page(url, mode='browser') 또는 Firecrawl로 시도."}
    info = parse_naver_place(state, pid)
    if not info:
        return {"ok": False, "place_id": pid, "reason": "parse_failed", "hint": "naver_place_raw 로 구조 확인."}
    return {"ok": True, "engine": "http", **info}


def naver_place_raw(place: str, key_filter: str = "", max_chars: int = 40_000) -> dict:
    """APOLLO_STATE 원본(또는 key_filter가 포함된 항목). 필드를 찾을 때 사용."""
    pid = _place_id(place)
    if not pid:
        return {"ok": False, "reason": "invalid_place_id"}
    state, page, err = _naver_get(f"https://m.place.naver.com/restaurant/{pid}/home")
    if not state:
        return {"ok": False, "reason": "no_data", "error": err}
    if key_filter:
        state = {k: v for k, v in state.items() if key_filter in k}
    text, cut = core.truncate(json.dumps(state, ensure_ascii=False), max_chars)
    return {"ok": True, "place_id": pid, "truncated": cut, "json_text": text}


# ---------------------------------------------------------------- 카카오

def parse_kakao_search(data: Any) -> list[dict]:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if not isinstance(data, dict):
        return []
    out = []
    for p in data.get("place") or []:
        cats = [p.get(f"cate_name_depth{i}") for i in range(1, 5)]
        out.append({
            "id": p.get("confirmid"),
            "name": p.get("name"),
            "category": " > ".join(c for c in cats if c),
            "road_address": p.get("new_address"),
            "address": p.get("address"),
            "phone": p.get("tel") or None,
            "rating": _num(p.get("rating_average")),
            "rating_count": _num(p.get("rating_count")),
            "review_count": _num(p.get("reviewCount")),
            "lat": _num(p.get("lat")),
            "lng": _num(p.get("lon")),
            "url": f"https://place.map.kakao.com/{p.get('confirmid')}" if p.get("confirmid") else None,
        })
    return out


def kakao_search(query: str, wait_ms: int = 3500, max_items: int = 15) -> dict:
    url = f"https://map.kakao.com/?q={quote(query)}"
    cap = core.capture_json(url, ["mapsearch/map"], wait_ms=wait_ms, max_items=2, max_chars_each=500_000)
    places: list[dict] = []
    for c in cap.get("captured") or []:
        data = c.get("data") if "data" in c else c.get("json_text")
        places = parse_kakao_search(data)
        if places:
            break
    if not places:
        cap["query"] = query
        cap["ok"] = False
        cap.setdefault("fallback_hint", "검색 응답을 못 잡음. 잠시 후 재시도하거나 Firecrawl로 시도.")
        return cap
    return {"ok": True, "query": query, "engine": "browser_capture", "count": min(len(places), max_items),
            "places": places[:max_items],
            "note": "rating=카카오 별점 평균, rating_count=별점 수, review_count=후기 수. 영업시간은 kakao_place로 확인."}


def kakao_place(place: str, max_chars: int = 6000) -> dict:
    """장소 상세 페이지를 브라우저로 렌더링해 본문 텍스트(영업시간·메뉴·후기 요약 등)를 가져온다."""
    pid = _place_id(place)
    if not pid:
        return {"ok": False, "reason": "invalid_place_id", "hint": "카카오맵 장소 숫자 ID나 place.map.kakao.com URL을 넣을 것."}
    url = f"https://place.map.kakao.com/{pid}"
    core.throttle(url)
    # 상세 화면은 스크립트가 늦게 그리므로 충분히 기다린다
    page, err = core.fetch_browser(url, network_idle=False, wait_ms=4000)
    reason = "browser_error" if page is None else core.classify(page.status, str(page.html_content), core.visible_text_len(page))
    core.log_event(url=url, engine="kakao_place", status=getattr(page, "status", None), reason=reason or "ok")
    if page is None or reason:
        return {"ok": False, "place_id": pid, "url": url, "reason": reason, "error": err,
                "fallback_hint": "kakao_search 결과(별점·리뷰 수)로 대신하거나 Firecrawl로 시도."}
    text = str(page.get_all_text(separator="\n", strip=True))
    content, cut = core.truncate(text, max_chars)
    return {"ok": True, "place_id": pid, "url": url, "engine": "browser", "title": core.page_title(page),
            "chars": len(text), "truncated": cut, "content": content}
