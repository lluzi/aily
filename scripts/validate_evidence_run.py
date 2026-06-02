#!/usr/bin/env python3
"""Validate structure, origins, hashes, and basic secret hygiene for a run.

Origin: Created by Codex lead agent on 2026-05-17.
Role: Validator source code only; not acceptance evidence for any gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.verify.evidence_validator import validate_evidence_run


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an Aily evidence run directory.")
    parser.add_argument("run_path", type=Path, help="Path to ~/.aily/runs/<run_id>")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = validate_evidence_run(args.run_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
