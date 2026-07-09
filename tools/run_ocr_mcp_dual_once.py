from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ENGINEERING_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINEERING_ROOT))

from common.mcp import ensure_loaded, run as mcp_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one OCR dual MCP call and emit JSON.")
    parser.add_argument("--path", required=True)
    parser.add_argument("--goal", default="OCR97 challenger study extraction")
    parser.add_argument("--engine", default="gb10_auto")
    parser.add_argument("--route-mode", default="quality_first")
    parser.add_argument("--consensus", default="true")
    parser.add_argument("--use-gateway", default="true")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-chars", type=int, default=6000)
    args = parser.parse_args(argv)

    ensure_loaded()
    payload = {
        "path": str(Path(args.path).resolve()),
        "goal": str(args.goal),
        "engine": str(args.engine),
        "route_mode": str(args.route_mode),
        "consensus": str(args.consensus).strip().lower() not in {"0", "false", "no"},
        "use_gateway": str(args.use_gateway).strip().lower() not in {"0", "false", "no"},
        "max_pages": int(args.max_pages),
        "max_chars": int(args.max_chars),
    }
    result = mcp_run("ocr.dual", payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
