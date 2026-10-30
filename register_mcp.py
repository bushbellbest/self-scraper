"""Claude 데스크톱 앱 설정(claude_desktop_config.json)에 self-scraper를 등록/해제한다.

사용: python register_mcp.py <config.json 경로> [--remove]
기존 설정은 .bak 파일로 백업하고, 다른 MCP 서버 설정은 건드리지 않는다.
"""

import json
import shutil
import sys
from pathlib import Path

NAME = "self-scraper"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: register_mcp.py <claude_desktop_config.json> [--remove]")
        return 2
    cfg = Path(sys.argv[1])
    remove = "--remove" in sys.argv[2:]
    root = Path(__file__).resolve().parent
    # resolve()를 쓰면 가상환경 python이 원본 python으로 바뀔 수 있어 absolute()만 사용
    py = Path(sys.executable).absolute()

    data = {}
    if cfg.exists() and cfg.stat().st_size > 0:
        raw = cfg.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[!] {cfg} is not valid JSON ({e}). Not touching it.")
            return 1
        shutil.copy2(cfg, cfg.with_suffix(".json.bak"))

    servers = data.setdefault("mcpServers", {})
    if remove:
        servers.pop(NAME, None)
        print(f"[ok] removed {NAME} from {cfg}")
    else:
        servers[NAME] = {
            "command": str(py),
            "args": [str(root / "server.py")],
            "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        }
        print(f"[ok] registered {NAME} in {cfg}")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
