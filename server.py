"""self-scraper MCP 서버 (stdio). Claude 데스크톱 앱이 이 파일을 실행한다."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logging.getLogger("trafilatura").setLevel(logging.ERROR)

import anyio  # noqa: E402
from mcp.server import MCPServer  # noqa: E402

from scraper import core, maps, reddit  # noqa: E402

INSTRUCTIONS = """내 PC에서 직접 수집하는 무료 수집기. 크레딧이 들지 않으므로 웹 페이지 수집은 이 도구를 먼저 쓴다.
규칙:
1. 일반 페이지는 fetch_page를 먼저 쓴다. ok=false이면 fallback_hint를 따른다(대개 Firecrawl의 firecrawl_scrape로 넘김).
2. 레딧은 reddit_search / reddit_listing / reddit_post를 쓴다. Firecrawl은 레딧을 지원하지 않으므로 레딧 실패 시 Firecrawl로 넘기지 않는다.
3. 네이버 지도는 naver_search → naver_place(영업시간·리뷰 수), 카카오맵은 kakao_search(별점·리뷰 수) → kakao_place(상세 본문). 구조를 모르는 사이트의 내부 데이터는 capture_json.
4. reason='captcha'이면 자동으로 풀려 하지 말고, 사용자에게 알린 뒤 open_browser_for_human으로 사용자가 직접 통과하게 한다.
5. 같은 사이트를 짧은 시간에 대량으로 요청하지 않는다(수집기가 도메인별 간격을 자동으로 둔다)."""

mcp = MCPServer(name="self-scraper", instructions=INSTRUCTIONS, version="0.1.0")


async def _run(fn, *args, **kwargs):
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))


@mcp.tool()
async def fetch_page(
    url: str,
    mode: str = "auto",
    css_selector: Optional[str] = None,
    main_content_only: bool = True,
    max_chars: int = 20000,
    wait_selector: Optional[str] = None,
) -> dict:
    """웹 페이지를 마크다운으로 수집. mode: auto(HTTP→실패 시 스텔스 브라우저) | http | browser.
    css_selector를 주면 그 부분만 추출. 실패하면 reason과 fallback_hint를 돌려준다."""
    return await _run(core.fetch_auto, url, mode, css_selector, main_content_only, max_chars, wait_selector)


@mcp.tool()
async def capture_json(
    url: str,
    url_contains: list[str],
    wait_ms: int = 4000,
    scroll_times: int = 0,
    max_items: int = 10,
) -> dict:
    """브라우저로 페이지를 열어, 페이지가 내부적으로 받아오는 JSON 응답 중 URL에 url_contains가 포함된 것을 가로챈다.
    빈 리스트면 모든 JSON. 못 찾으면 observed_json_urls로 실제 관측된 주소를 알려준다."""
    return await _run(core.capture_json, url, url_contains, wait_ms, scroll_times, max_items)


@mcp.tool()
async def reddit_search(
    query: str,
    subreddit: Optional[str] = None,
    sort: str = "relevance",
    time_filter: str = "year",
    limit: int = 25,
) -> dict:
    """레딧 검색(www.reddit.com). sort: relevance|hot|top|new|comments, time_filter: hour|day|week|month|year|all.
    레딧이 검색어를 자동 교정할 수 있으니 결과 제목을 확인할 것."""
    return await _run(reddit.search, query, subreddit, sort, time_filter, limit)


@mcp.tool()
async def reddit_listing(subreddit: str, sort: str = "hot", time_filter: str = "week", limit: int = 25) -> dict:
    """서브레딧 글 목록. sort: hot|new|top|rising|controversial (top/controversial은 time_filter 적용)."""
    return await _run(reddit.listing, subreddit, sort, time_filter, limit)


@mcp.tool()
async def reddit_post(url: str, sort: str = "top", comment_limit: int = 150, max_depth: int = 6) -> dict:
    """레딧 글 본문과 댓글 트리. sort: top|best|new|controversial|old."""
    return await _run(reddit.post, url, sort, comment_limit, max_depth)


@mcp.tool()
async def naver_place(place: str) -> dict:
    """네이버 플레이스 상세(이름·카테고리·주소·리뷰 수/평점 등). 숫자 ID나 URL."""
    return await _run(maps.naver_place, place)


@mcp.tool()
async def naver_place_raw(place: str, key_filter: str = "") -> dict:
    """네이버 플레이스 내부 데이터 원본(APOLLO_STATE). key_filter로 항목 이름 일부를 걸러 볼 수 있다(예: BusinessHour)."""
    return await _run(maps.naver_place_raw, place, key_filter)


@mcp.tool()
async def naver_search(query: str, max_items: int = 15) -> dict:
    """네이버 플레이스 검색(예: '병점역 카페'). 결과의 id를 naver_place에 넣어 상세 조회."""
    return await _run(maps.naver_search, query, max_items)


@mcp.tool()
async def kakao_place(place: str) -> dict:
    """카카오맵 장소 상세 페이지 본문(영업시간·메뉴 등)을 브라우저로 렌더링해 가져온다. 숫자 ID나 place.map.kakao.com URL."""
    return await _run(maps.kakao_place, place)


@mcp.tool()
async def kakao_search(query: str) -> dict:
    """카카오맵 검색. 장소별 별점 평균·별점 수·후기 수·전화·주소. 결과 id를 kakao_place에 넣어 상세 확인."""
    return await _run(maps.kakao_search, query)


@mcp.tool()
async def open_browser_for_human(url: str, max_wait_s: int = 90) -> dict:
    """사용자 PC 화면에 브라우저를 띄워 사용자가 캡차 통과·로그인을 직접 하게 한다. 쓰기 전에 사용자에게 알릴 것.
    통과 상태는 수집기 전용 프로필에 저장되어 이후 브라우저 단계 수집에 쓰인다."""
    return await _run(core.open_browser_for_human, url, max_wait_s)


@mcp.tool()
async def scraper_status() -> dict:
    """최근 수집 기록을 도메인별 성공/실패로 요약. 자주 실패하는 사이트 파악용."""
    return await _run(core.recent_stats)


if __name__ == "__main__":
    mcp.run()
