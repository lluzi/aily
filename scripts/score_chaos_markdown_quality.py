#!/usr/bin/env python3
"""Score source-equivalent Markdown quality for 00-Chaos chaos notes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.config import SETTINGS
from aily.verify.chaos_quality import score_chaos_vault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path),
    )
    parser.add_argument("--path", type=Path, action="append", default=None, help="Specific 00-Chaos note to score.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    report = score_chaos_vault(args.vault_path, paths=args.path)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
