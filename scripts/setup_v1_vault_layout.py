#!/usr/bin/env python3
"""Create or inspect the Aily V1 Obsidian vault directory layout.

Origin: Created by Codex lead agent on 2026-05-17.
Role: Operational setup source code only; not acceptance evidence for any gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.config import SETTINGS
from aily.writer.vault_layout import ensure_v1_vault_layout


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up the Aily V1 Obsidian vault directory layout.")
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path),
        help="Obsidian vault path. Defaults to configured Aily vault.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect changes without creating directories.")
    parser.add_argument("--legacy-compat", action="store_true", help="Also create retired legacy compatibility directories.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = ensure_v1_vault_layout(
        args.vault_path,
        include_legacy_compatibility=args.legacy_compat,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
