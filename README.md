# self-scraper

내 PC에서 직접 돌리는 무료 웹 수집기. Claude 데스크톱 앱에 MCP 서버로 등록되어, Claude가 Firecrawl보다 먼저 이걸로 수집한다. 크레딧이 들지 않는다.

## 구조

```
요청 → [1단계] HTTP (브라우저 TLS 지문 위장, 빠름·데이터 적음)
          │ 차단·빈 페이지
          ▼
       [2단계] 스텔스 브라우저 (설치된 크롬 사용, 쿠키 유지 프로필, 이미지 차단)
          │ 실패
          ▼
       fallback_hint 반환 → Claude가 Firecrawl로 넘김 (레딧 제외: Firecrawl 미지원)
          │ 캡차
          ▼
       open_browser_for_human → 사용자가 화면에서 직접 통과 → 쿠키 저장 후 재시도
```

- 엔진: [Scrapling](https://github.com/D4Vinci/Scrapling) 0.5 (BSD-3)
- 도메인별 요청 간격 자동 적용 (레딧 3~6초, 네이버·카카오 1.5~3초, 기타 1~2.5초)
- 캡차 자동 풀이 기능은 넣지 않았다

## 도구

| 도구 | 용도 |
|---|---|
| `fetch_page` | 일반 페이지 → 마크다운 (auto / http / browser) |
| `capture_json` | 페이지가 내부적으로 받는 JSON 응답 가로채기 |
| `reddit_search` / `reddit_listing` / `reddit_post` | 레딧 검색·목록·본문+댓글 트리 (www.reddit.com 화면 파싱, 로그인 불필요) |
| `naver_search` / `naver_place` / `naver_place_raw` | 네이버 플레이스 목록·상세(리뷰 수, 영업시간) — HTTP만 사용 |
| `kakao_search` / `kakao_place` | 카카오맵 검색(별점·리뷰 수·전화·주소) — 브라우저 사용. 상세(kakao_place)는 현재 빈 화면이 와서 실패 시 Firecrawl로 넘김 |
| `open_browser_for_human` | 캡차·로그인을 사람이 직접 처리 |
| `scraper_status` | 도메인별 성공/실패 통계 |

## 설치 (Windows)

이 폴더에서 PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

또는 탐색기에서 `install.bat` 더블클릭. 실사이트 점검은 `live_test.bat`(결과: `live_test_result.json`).

1. uv(파이썬 관리 도구) 설치 → 2. 파이썬 3.12 가상환경과 패키지 설치 → 3. 크롬이 있으면 브라우저 다운로드 생략 → 4. Claude 데스크톱 설정에 등록(기존 설정은 .bak 백업) → 5. 자체 테스트

끝나면 Claude 데스크톱 앱을 트레이 아이콘에서 **완전히 종료 후 다시 실행**.

데이터 사용량(추정): uv·파이썬·패키지 약 100~200MB, 크롬이 없으면 Chromium 약 150MB 추가.

등록 해제: `.\install.ps1 -Uninstall`

## 새 PC에 설치 (처음 한 번만)

`Win + R` → 아래 한 줄 붙여넣기 → Enter

```
powershell -ep bypass -c "irm https://raw.githubusercontent.com/bushbellbest/self-scraper/main/bootstrap.ps1 | iex"
```

- `%USERPROFILE%\self-scraper`에 설치되고 Claude 데스크톱에 자동 등록된다.
- 시작프로그램에 `self-scraper-autoupdate.vbs`가 등록되어, 이후 Windows 로그인 때마다 화면 없이 새 버전을 확인·설치한다(로그: `update.log`).
- 업데이트된 코드는 Claude 데스크톱 앱을 다음에 켤 때부터 적용된다.
- 자동 업데이트 끄기: 시작프로그램 폴더(`shell:startup`)에서 `self-scraper-autoupdate.vbs` 삭제.

## 레딧 로그인 (선택)

비로그인으로 막힐 때만. Claude에게 "레딧 로그인 창 열어줘"라고 하면 `open_browser_for_human('https://www.reddit.com/login')`으로 수집기 전용 브라우저가 뜬다. 거기서 로그인하면 이후 브라우저 단계가 그 로그인을 쓴다. 평소 쓰는 크롬 프로필과는 분리되어 있다. 과도한 수집은 계정 제재 위험이 있다.

## 설정 (환경변수, 선택)

| 변수 | 기본 | 설명 |
|---|---|---|
| `SCRAPER_SAVE_DATA` | `1` | 브라우저 단계에서 이미지·폰트·영상 차단 |
| `SCRAPER_REAL_CHROME` | `auto` | 설치된 크롬 사용 여부 |
| `SCRAPER_DATA_DIR` | `./data` | 프로필·로그 저장 위치 |

## 알려진 한계

- 프록시 IP 풀이 없어 대량 수집 시 IP 차단될 수 있음
- Cloudflare 최고 단계 등 강한 보호는 통과 보장 안 됨
- 네이버·카카오 내부 주소는 예고 없이 바뀜 → `capture_json`의 `observed_json_urls`로 새 패턴 확인
- 레딧 화면 구조가 바뀌면 `scraper/reddit.py` 파서 수정 필요 (`tests/test_offline.py`, `live_test.bat`로 확인)
- 2026-10 기준 old.reddit.com은 비로그인 시 로그인 화면으로 보내고, .json은 403이라 사용하지 않음
- 2026-11 기준 레딧 검색 페이지와 댓글 조각(/svc/shreddit/comments)은 HTTP 단계에서 403이 자주 난다. 이때 브라우저 단계로 넘어가고, 첫 요청이 브라우저로만 열렸으면 다음 페이지도 브라우저로 가져온다. 브라우저 단계까지 403이면 수집기 전용 프로필에 레딧 로그인이 필요하다(`open_browser_for_human`). 로그인은 PC마다 따로 한다.
