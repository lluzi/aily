#!/usr/bin/env python3
"""Score generated Obsidian vault Markdown for human-readable Zettelkasten quality."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.config import SETTINGS
from aily.verify.obsidian_quality import QualityThresholds, score_vault_output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score Aily Obsidian vault quality.")
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path),
        help="Vault path to inspect.",
    )
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=[],
        help="Specific generated markdown path to score. May be repeated. Defaults to all vault markdown.",
    )
    parser.add_argument(
        "--strict-human-gate",
        action="store_true",
        help="Apply stricter thresholds for 10-PDF human-readable vault acceptance.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    thresholds = (
        QualityThresholds(
            overall_score=90.0,
            dimension_floor=85.0,
            source_clarity=80.0,
            content_substance=85.0,
            report_substance=85.0,
            note_pass_rate=1.0,
            high_value_note_floor=85.0,
            max_index_link_count=0,
            max_index_link_note_ratio=0.0,
            max_generic_tag_share=0.15,
            max_unresolved_link_count=0,
            min_valid_connector_ratio=0.95,
            min_info_connector_coverage=0.75,
            min_info_pair_density=0.20,
        )
        if args.strict_human_gate
        else None
    )
    report = score_vault_output(args.vault_path, generated_paths=args.path or None, thresholds=thresholds)
    wrapped = {
        "_origin": {
            "creator": "quality-scorer",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "generation_method": "scripts/score_obsidian_vault_quality.py deterministic markdown scoring",
            "evidence_class": "quality_gate",
            "modified_by_lead_agent": False,
        },
        "data": report,
    }
    text = json.dumps(wrapped, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
