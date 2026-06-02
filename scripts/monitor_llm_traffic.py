#!/usr/bin/env python3
"""Build an LLM traffic-monitor evidence artifact.

Origin: Created by Codex lead agent on 2026-05-18.
Role: Evidence-monitor source code only; not acceptance evidence by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.verify.llm_traffic import TrafficMonitorThresholds, build_traffic_monitor


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate sanitized Kimi/DeepSeek LLM traffic evidence.")
    parser.add_argument("--run-root", type=Path, default=None, help="Run root containing runtime/ or runs/*/runtime.")
    parser.add_argument("--trace", action="append", type=Path, default=[], help="Explicit llm-calls.jsonl path.")
    parser.add_argument("--run-id", default="", help="Logical run id for the monitor artifact.")
    parser.add_argument("--scenario", default="", help="Scenario name.")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path.")
    parser.add_argument("--no-require-kimi", action="store_true", help="Do not require Kimi coverage for this monitor.")
    parser.add_argument("--no-require-deepseek", action="store_true", help="Do not require DeepSeek coverage for this monitor.")
    return parser.parse_args()


def _trace_paths(args: argparse.Namespace) -> list[Path]:
    paths = [path.expanduser().resolve() for path in args.trace]
    if args.run_root:
        root = args.run_root.expanduser().resolve()
        paths.extend(sorted(root.glob("runtime/llm-calls.jsonl")))
        paths.extend(sorted(root.glob("runs/*/runtime/llm-calls.jsonl")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        unique.append(path)
    return unique


def main() -> int:
    args = _parse_args()
    report = build_traffic_monitor(
        _trace_paths(args),
        run_id=args.run_id,
        scenario=args.scenario,
        thresholds=TrafficMonitorThresholds(
            require_kimi=not args.no_require_kimi,
            require_deepseek=not args.no_require_deepseek,
        ),
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
