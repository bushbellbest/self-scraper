"""큰 수집 결과는 파일로 저장하고 Claude에게는 요약본만 돌려준다.

왜: 수집 결과가 통째로 대화에 들어가면 토큰을 많이 쓴다(레딧 댓글 150개, 긴 페이지 등).
방식: 결과를 JSON으로 바꿨을 때 INLINE_MAX자를 넘으면
  1) 전체를 data/results/<id>.json 에 저장하고
  2) 긴 글은 앞부분만, 긴 목록은 앞 몇 개만 남긴 요약본 + 저장 id 를 돌려준다.
  3) 필요한 부분은 read_saved(id, path=..., query=...) 로 골라 읽는다.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any, Optional

from . import core

INLINE_MAX = int(os.environ.get("SCRAPER_INLINE_MAX", "6000"))
KEEP_FILES = int(os.environ.get("SCRAPER_KEEP_RESULTS", "300"))
RESULT_DIR = core.DATA_DIR / "results"
ID_RE = re.compile(r"^[0-9A-Za-z_\-]+$")

# (긴 글 최대 글자 수, 목록 최대 개수) — 요약본이 INLINE_MAX 안에 들어올 때까지 차례로 줄인다
_STEPS = [(800, 15), (400, 10), (250, 6), (150, 4), (80, 3), (40, 2)]
# 결과가 성공이면 요약본에서 빼는 진단용 항목
_DROP_WHEN_OK = {"attempts"}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _shrink(obj: Any, str_max: int, list_max: int, top: bool = False) -> Any:
    if isinstance(obj, str):
        limit = str_max * 4 if top else str_max  # 맨 위 본문(예: fetch_page의 content)은 조금 더 길게
        if len(obj) > limit:
            return obj[:limit] + f" …(+{len(obj) - limit}자, 전체는 read_saved)"
        return obj
    if isinstance(obj, list):
        out = [_shrink(v, str_max, list_max) for v in obj[:list_max]]
        if len(obj) > list_max:
            out.append(f"…(+{len(obj) - list_max}개 더, 전체는 read_saved)")
        return out
    if isinstance(obj, dict):
        return {k: _shrink(v, str_max, list_max, top=top and k in {"content", "body", "json_text"})
                for k, v in obj.items()}
    return obj


def _compact(result: dict) -> dict:
    base = dict(result)
    if base.get("ok"):
        for k in _DROP_WHEN_OK:
            base.pop(k, None)
    small: dict = base
    for str_max, list_max in _STEPS:
        small = {k: _shrink(v, str_max, list_max, top=True) for k, v in base.items()}
        if len(_dumps(small)) <= INLINE_MAX:
            break
    return small


def _cleanup() -> None:
    files = sorted(RESULT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for p in files[:-KEEP_FILES] if len(files) > KEEP_FILES else []:
        try:
            p.unlink()
        except OSError:
            pass


def save(tool: str, result: dict) -> str:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    rid = f"{time.strftime('%Y%m%d-%H%M%S')}_{tool}_{secrets.token_hex(2)}"
    (RESULT_DIR / f"{rid}.json").write_text(_dumps(result), encoding="utf-8")
    _cleanup()
    return rid


def wrap(tool: str, result: Any) -> Any:
    """작은 결과는 그대로, 큰 결과는 저장 후 요약본을 돌려준다."""
    if not isinstance(result, dict):
        return result
    full = _dumps(result)
    if len(full) <= INLINE_MAX:
        return result
    rid = save(tool, result)
    small = _compact(result)
    small["saved"] = {
        "id": rid,
        "full_chars": len(full),
        "how_to_read": "요약본입니다. 잘린 부분은 read_saved(id, path='posts.3' 같은 위치, 또는 query='검색어')로 필요한 것만 읽으세요.",
    }
    return small


# ---------- 저장된 결과 읽기 ----------

def _load(rid: str) -> Optional[Any]:
    if not ID_RE.match(rid or ""):
        return None
    p = RESULT_DIR / f"{rid}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _get_path(obj: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p != ""]:
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj[part]
        else:
            raise KeyError(part)
    return obj


def _walk(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}.{i}" if prefix else str(i))
    else:
        yield prefix, obj


def _outline(obj: Any, depth: int = 0) -> Any:
    """구조만 보여주기: 목록은 개수, 글은 길이."""
    if isinstance(obj, dict):
        if depth >= 2:
            return f"{{…{len(obj)}개 항목}}"
        return {k: _outline(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return f"[목록 {len(obj)}개]"
    if isinstance(obj, str) and len(obj) > 60:
        return f"(글 {len(obj)}자)"
    return obj


def read_saved(rid: str, path: str = "", query: str = "", offset: int = 0, max_chars: int = 4000) -> dict:
    data = _load(rid)
    if data is None:
        recent = sorted(RESULT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)[-5:] if RESULT_DIR.exists() else []
        return {"ok": False, "reason": "not_found", "recent_ids": [p.stem for p in reversed(recent)]}
    max_chars = max(500, min(int(max_chars), 20_000))
    try:
        target = _get_path(data, path) if path and path != "$" else data
    except (KeyError, IndexError, ValueError):
        return {"ok": False, "reason": "bad_path", "path": path, "outline": _outline(data)}

    if query:
        q = query.lower()
        hits = []
        for p, v in _walk(target, "" if path == "$" else path):
            s = v if isinstance(v, str) else str(v)
            i = s.lower().find(q)
            if i >= 0:
                hits.append({"path": p, "snippet": s[max(0, i - 150): i + len(query) + 150]})
                if len(hits) >= 30:
                    break
        return {"ok": True, "id": rid, "query": query, "matches": len(hits), "hits": hits}

    if not path and offset == 0:
        # 처음 열 때는 전체 대신 구조부터 보여준다
        return {"ok": True, "id": rid, "outline": _outline(data),
                "hint": "path='posts.0' 처럼 위치를 주거나 query로 검색. 전체 원문은 path='$' (offset으로 이어 읽기)."}

    text = target if isinstance(target, str) else json.dumps(target, ensure_ascii=False, indent=1, default=str)
    offset = max(0, int(offset))
    chunk = text[offset: offset + max_chars]
    end = offset + len(chunk)
    return {"ok": True, "id": rid, "path": path, "offset": offset, "total_chars": len(text),
            "next_offset": end if end < len(text) else None, "text": chunk}
