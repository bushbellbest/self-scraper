"""실측 테스트: 실제 사이트에 접속해 각 도구가 동작하는지 확인. 결과는 live_test_result.json."""
import json, sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import logging; logging.getLogger("scrapling").setLevel(logging.WARNING); logging.getLogger("trafilatura").setLevel(logging.ERROR)
from scraper import core, reddit, maps

def short(v, n=600):
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s[:n]

cases = [
    ("fetch_example", lambda: core.fetch_auto("https://example.com", max_chars=300)),
    ("fetch_wiki", lambda: core.fetch_auto("https://ko.wikipedia.org/wiki/웹_스크래핑", max_chars=300)),
    ("reddit_listing", lambda: reddit.listing("webscraping", "top", "week", 5)),
    ("reddit_search", lambda: reddit.search("scrapling", None, "relevance", "year", 5)),
    ("reddit_post", None),  # 목록의 첫 글로 채움
    ("naver_search", lambda: maps.naver_search("병점역 카페", 5)),
    ("naver_place", None),
    ("kakao_search", lambda: maps.kakao_search("병점역 카페")),
    ("kakao_place", None),
]
out = {}
listing_res = None
naver_res = None
kakao_res = None
ONLY = set(sys.argv[1:])
for name, fn in cases:
    if ONLY and name not in ONLY and name != "kakao_search":
        continue
    t0 = time.time()
    try:
        if name == "reddit_post":
            posts = (listing_res or {}).get("posts") or []
            if not posts:
                out[name] = {"skipped": "no listing"}; continue
            res = reddit.post(posts[0]["permalink"], "top", 20, 3)
        elif name == "kakao_place":
            places = (kakao_res or {}).get("places") or []
            if not places:
                out[name] = {"skipped": "no kakao result"}; continue
            res = maps.kakao_place(str(places[0]["id"]), max_chars=800)
        elif name == "naver_place":
            places = (naver_res or {}).get("places") or []
            if not places:
                out[name] = {"skipped": "no naver search result"}; continue
            res = maps.naver_place(str(places[0]["id"]))
        else:
            res = fn()
        if name == "reddit_listing": listing_res = res
        if name == "naver_search": naver_res = res
        if name == "kakao_search": kakao_res = res
        summary = {k: res.get(k) for k in ("ok", "engine", "status", "reason", "count", "title", "chars", "error", "comments_collected") if k in res}
        summary["secs"] = round(time.time() - t0, 1)
        summary["attempts"] = res.get("attempts")
        if res.get("posts"): summary["first_post"] = res["posts"][0]
        if res.get("comment_tree"): summary["first_comment"] = short(res["comment_tree"][0], 300)
        if res.get("places"): summary["first_place"] = res["places"][0]
        for k in ("name","category","visitor_reviews","rating","hours","status_now","comments_collected"):
            if res.get(k) is not None: summary[k] = res[k]
        if res.get("comment_tree"): summary["first_comment"] = short(res["comment_tree"][0], 300)
        if res.get("captured"): summary["captured"] = [ {"url": c["url"][:200], "sample": short(c.get("data") or c.get("json_text"), 400)} for c in res["captured"][:3]]
        if res.get("observed_json_urls"): summary["observed_json_urls"] = res["observed_json_urls"][:25]
        if res.get("content"): summary["content_head"] = res["content"][:200]
        out[name] = summary
    except Exception as e:
        out[name] = {"exception": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-1500:]}
    print(name, "->", short(out[name], 200), flush=True)
Path(__file__).with_name("live_test_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("DONE")
