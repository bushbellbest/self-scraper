"""네트워크 없이 파서·분류 로직을 확인하는 테스트.  실행: python tests/test_offline.py
픽스처는 2026-10-30 실제 www.reddit.com / 네이버 / 카카오 응답 구조를 줄여 옮긴 것이다."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapling.parser import Selector  # noqa: E402

from scraper import core, maps, reddit, results, search  # noqa: E402

LISTING = """
<shreddit-post permalink="/r/webscraping/comments/abc/hello/" content-href="https://example.com" comment-count="56"
  created-timestamp="2026-10-26T15:05:57.065000+0000" id="t3_abc" post-title="Hello world" post-type="text"
  score="1234" subreddit-prefixed-name="r/webscraping" author="alice" subreddit-name="webscraping">
  <shreddit-post-text-body slot="text-body" post-id="t3_abc"><div class="md"><p>Body preview</p></div></shreddit-post-text-body>
  <shreddit-post-overflow-menu></shreddit-post-overflow-menu>
</shreddit-post>
<shreddit-ad-post id="t3_ad" post-title="AD"></shreddit-ad-post>
<faceplate-partial src="/svc/shreddit/community-more-posts/top/?t=WEEK&amp;name=webscraping&amp;after=xyz"></faceplate-partial>
"""

SEARCH = """
<search-telemetry-tracker data-testid="search-sdui-post" data-thingid="t3_s1"
  data-faceplate-tracking-context='{"post":{"id":"t3_s1","nsfw":false,"title":"Best scraper?"},"profile":{"name":"bob"},"subreddit":{"name":"LocalLLaMA"}}'>
  <div data-testid="search-post-unit"><faceplate-timeago ts="2026-07-01T09:30:12+0000"></faceplate-timeago>
  <a data-testid="post-title-text" href="/r/LocalLLaMA/comments/s1/best_scraper/"> Best scraper? </a>
  <div data-testid="search-counter-row"><span><faceplate-number number="1024"></faceplate-number> votes</span>
  <span><faceplate-number number="88"></faceplate-number> comments</span></div></div>
</search-telemetry-tracker>
<faceplate-partial src="/svc/shreddit/search/?q=x&amp;type=posts&amp;cursor=abc"></faceplate-partial>
"""

COMMENTS = """
<shreddit-comment thingid="t1_a" author="c1" score="42" depth="0" created="2026-01-01T00:00:00+0000">
  <div id="t1_a-comment-rtjson-content" slot="comment"><p>Top comment</p></div>
  <shreddit-comment thingid="t1_b" author="c2" score="5" depth="1">
    <div id="t1_b-comment-rtjson-content" slot="comment"><p>Reply</p></div>
    <shreddit-comment thingid="t1_c" author="c3" score="1" depth="2">
      <div id="t1_c-comment-rtjson-content" slot="comment"><p>Deep</p></div>
    </shreddit-comment>
  </shreddit-comment>
</shreddit-comment>
<shreddit-comment thingid="t1_d" author="c4" score="3" depth="0">
  <div id="t1_d-comment-rtjson-content" slot="comment"><p>Second</p></div>
</shreddit-comment>
<faceplate-partial src="/svc/shreddit/more-comments/x/t3_p?sort=top&amp;top-level=1&amp;comments-remaining=5"></faceplate-partial>
"""

NAVER_LIST = """<script>window.__APOLLO_STATE__ = {"PlaceListBusinessesItem:111:111":{"__typename":"PlaceListBusinessesItem",
"id":"111","name":"테스트카페","category":"카페","roadAddress":"병점로 1","fullAddress":"경기도 화성시 병점동 1",
"virtualPhone":"0507-1","visitorReviewCount":"22","blogCafeReviewCount":"3","x":"127.03","y":"37.2"}};
window.__PLACE_STATE__ = {};</script>"""

NAVER_PLACE = """<script>window.__APOLLO_STATE__ = {"PlaceDetailBase:1234567":{"id":"1234567","name":"테스트카페","category":"카페",
"roadAddress":"경기 화성시","visitorReviewsTotal":321,"visitorReviewsScore":4.5},
"VisitorReviewStatsResult:1234567":{"review":{"avgRating":4.5},"analysis":{"themes":[{"label":"맛","count":40}]}},
"ROOT_QUERY":{"placeDetail({\\"input\\":1})":{"newBusinessHours":[{"businessStatusDescription":{"status":"영업 중","description":"19:00에 영업 종료"},
"businessHours":[{"day":"월","businessHours":{"start":"08:00","end":"19:00"},"breakHours":[]}]}]}}};
window.__X__ = {};</script>"""

KAKAO = json.dumps(json.dumps({"place": [{"confirmid": "99", "name": "투썸", "tel": "031-1", "address": "병점동",
                                          "new_address": "떡전골로 98", "rating_average": 3.5, "rating_count": 36,
                                          "reviewCount": 12, "cate_name_depth1": "음식점", "cate_name_depth2": "카페"}]}))


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        check.failed = True


check.failed = False


def main():
    page = Selector(LISTING, url="https://www.reddit.com/")
    posts = reddit.parse_posts(page)
    check(len(posts) == 1 and posts[0]["title"] == "Hello world", "listing: 광고 제외, 제목")
    check(posts[0]["score"] == 1234 and posts[0]["comments"] == 56, "listing: 점수·댓글 수")
    check(posts[0]["permalink"] == "https://www.reddit.com/r/webscraping/comments/abc/hello/", "listing: permalink")
    check(posts[0]["selftext"] == "Body preview", "listing: 본문 미리보기")
    nxt = reddit.next_partial(page, "more-posts")
    check(nxt and nxt.startswith("https://www.reddit.com/svc/shreddit/community-more-posts/") and "after=xyz" in nxt, "listing: 다음 조각")

    page = Selector(SEARCH)
    res = reddit.parse_search(page)
    check(len(res) == 1 and res[0]["title"] == "Best scraper?", "search: 제목")
    check(res[0]["score"] == 1024 and res[0]["comments"] == 88, "search: 투표·댓글 수")
    check(res[0]["subreddit"] == "LocalLLaMA" and res[0]["author"] == "bob", "search: 서브레딧·작성자")
    check(res[0]["permalink"] == "https://www.reddit.com/r/LocalLLaMA/comments/s1/best_scraper/", "search: permalink")
    check(reddit.next_partial(page, "cursor=") is not None, "search: 다음 조각")

    page = Selector(COMMENTS)
    flat = reddit.parse_comments(page)
    check([c["body"] for c in flat] == ["Top comment", "Reply", "Deep", "Second"], "comments: 본문(자식 글 섞이지 않음)")
    tree = reddit.build_tree(flat, 6)
    check(len(tree) == 2 and tree[0]["replies"][0]["replies"][0]["body"] == "Deep", "comments: 트리 구성")
    check(len(reddit.build_tree(flat, 2)[0]["replies"][0].get("replies", [])) == 0, "comments: max_depth 제한")
    check(reddit.next_partial(page, "top-level=1") is not None, "comments: 다음 조각")
    check(reddit.POST_RE.search("https://www.reddit.com/r/a/comments/xYz12/t/").group(2) == "xYz12", "post URL 파싱")

    items = maps.parse_naver_list(maps.extract_apollo(NAVER_LIST))
    check(len(items) == 1 and items[0]["visitor_reviews"] == 22 and items[0]["phone"] == "0507-1", "naver: 목록")
    info = maps.parse_naver_place(maps.extract_apollo(NAVER_PLACE), "1234567")
    check(info["visitor_reviews"] == 321 and info["visitor_score"] == 4.5, "naver: 상세 리뷰")
    check(info["hours"] == ["월 08:00-19:00"] and "영업 중" in info["status_now"], "naver: 영업시간")
    kk = maps.parse_kakao_search(json.loads(KAKAO))
    check(kk and kk[0]["rating"] == 3.5 and kk[0]["rating_count"] == 36 and kk[0]["category"] == "음식점 > 카페", "kakao: 검색 결과")

    check(core.parse_jsonish('cb({"a":1});') == {"a": 1}, "JSONP 파싱")
    check(core.classify(200, "<div class='cf-turnstile'></div>", 50) == "captcha", "분류: 캡차")
    check(core.classify(403, "<html></html>", 10) == "forbidden", "분류: 403")
    check(core.classify(200, "<html>" + "<script>x</script>" * 300 + "</html>", 10) == "empty", "분류: JS 빈 페이지")
    check(core.classify(200, "<html><p>short page</p></html>", 10) is None, "분류: 짧은 정상 페이지")
    check(core.classify(200, "<p>access denied in a long article</p>", 20000) is None, "분류: 긴 정상글은 차단 아님")
    check(core.domain_key("https://m.place.naver.com/a") == "naver.com", "도메인 키")
    check(core.domain_key("https://www.example.co.kr/") == "example.co.kr", "도메인 키 co.kr")

    # 큰 결과 → 파일 저장 + 요약본
    import tempfile
    results.RESULT_DIR = Path(tempfile.mkdtemp())
    small = {"ok": True, "title": "짧은 결과"}
    check(results.wrap("t", small) is small, "results: 작은 결과는 그대로")
    big = {"ok": True, "url": "https://www.reddit.com/r/a/comments/x/", "title": "긴 글", "body": "본문 " * 3000,
           "comment_tree": [{"author": f"u{i}", "score": i, "body": f"댓글{i} " + "내용 " * 200,
                             "replies": [{"author": "r", "body": "답글 " * 100 + ("숨은단어" if i == 120 else "")}]}
                            for i in range(150)],
           "attempts": [{"engine": "http"}] * 20}
    out = results.wrap("reddit_post", big)
    rid = out.get("saved", {}).get("id", "")
    check(len(results._dumps(out)) <= results.INLINE_MAX + 400, "results: 요약본 크기 제한")
    check(bool(rid) and (results.RESULT_DIR / f"{rid}.json").exists(), "results: 전체 파일 저장")
    check("attempts" not in out and out["title"] == "긴 글", "results: 요약본 핵심 유지·진단정보 제거")
    hit = results.read_saved(rid, query="숨은단어")
    check(hit["matches"] == 1 and hit["hits"][0]["path"].startswith("comment_tree.120"), "results: 검색")
    part = results.read_saved(rid, path="comment_tree.120.author")
    check(part["text"] == "u120", "results: 위치로 읽기")
    body = results.read_saved(rid, path="body", max_chars=1000)
    check(len(body["text"]) == 1000 and body["next_offset"] == 1000, "results: 이어 읽기")
    check("outline" in results.read_saved(rid), "results: 구조 보기")
    check(results.read_saved("../x")["reason"] == "not_found", "results: 잘못된 id 차단")

    # 웹 검색 파서 (2026-11-26 실제 DuckDuckGo Lite / Bing 구조)
    ddg = """<tr><td valign="top">1.&nbsp;</td><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x" class='result-link'>Example &amp; A</a></td></tr>
<tr><td>&nbsp;</td><td class='result-snippet'>Snippet <b>text</b></td></tr>
<tr class="result-sponsored"><td valign="top">2.&nbsp;</td><td><a rel="nofollow" href="https://duckduckgo.com/y.js?ad=1" class='result-link'>AD</a></td></tr>"""
    got = search._parse_ddg_lite(ddg)
    check(len(got) == 1 and got[0]["url"] == "https://example.com/a" and got[0]["title"] == "Example & A"
          and got[0]["snippet"] == "Snippet text", "검색: DuckDuckGo 결과·광고 제외")
    bing = '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=x&amp;u=a1aHR0cHM6Ly93d3cubmF2ZXIuY29tLw&amp;ntb=1">NAVER</a></h2><p class="b_lineclamp2">desc</p></li>'
    got = search._parse_bing(bing)
    check(got and got[0]["url"] == "https://www.naver.com/" and got[0]["snippet"] == "desc", "검색: Bing 주소 복원")

    print("\n결과:", "실패 있음" if check.failed else "모두 통과")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
