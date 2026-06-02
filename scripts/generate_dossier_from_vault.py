#!/usr/bin/env python3
"""Generate an evidence-bound Deep Learning Dossier from Vault and Tavily records.

Origin: Created by Codex lead agent on 2026-05-19.
Role: Dossier generator source code only; generated dossiers must carry their
own origin header and verification section.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.config import SETTINGS
from aily.dossier import DossierBuildRequest, DossierService
from aily.research import ResearchStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an evidence-bound Aily dossier.")
    parser.add_argument("--topic", required=True, help="Dossier topic.")
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path),
        help="Obsidian vault path.",
    )
    parser.add_argument("--term", action="append", default=[], help="Vault search term. Repeat for multiple terms.")
    parser.add_argument("--claim", action="append", default=[], help="Seed claim to reconcile against evidence.")
    parser.add_argument("--research-store-db", type=Path, default=None, help="Optional ResearchStore sqlite path containing Tavily packets.")
    parser.add_argument("--research-limit", type=int, default=20)
    parser.add_argument("--max-vault-evidence", type=int, default=40)
    parser.add_argument("--max-tavily-evidence", type=int, default=20)
    parser.add_argument("--output", type=Path, default=None, help="Optional output markdown path. Defaults to 10-Dossiers in the vault.")
    parser.add_argument("--json-summary", action="store_true", help="Print JSON summary instead of the markdown path only.")
    return parser.parse_args()


async def _load_research_jobs(db_path: Path | None, limit: int) -> list[dict]:
    if db_path is None:
        return []
    store = ResearchStore(db_path)
    await store.initialize()
    try:
        return await store.list_research_jobs(limit=max(1, min(500, limit)))
    finally:
        await store.close()


async def _main() -> int:
    args = _parse_args()
    research_jobs = await _load_research_jobs(args.research_store_db, args.research_limit)
    request = DossierBuildRequest(
        topic=args.topic,
        vault_path=args.vault_path,
        query_terms=args.term or [args.topic],
        seed_claims=args.claim,
        tavily_research_jobs=research_jobs,
        max_vault_evidence=args.max_vault_evidence,
        max_tavily_evidence=args.max_tavily_evidence,
    )
    result = DossierService().build_and_write(request, output_path=args.output)
    summary = {
        "dossier_id": result.draft.dossier_id,
        "output_path": str(result.output_path or ""),
        "verification_passed": result.draft.verification.passed if result.draft.verification else False,
        "claim_count": len(result.draft.claims),
        "evidence_count": len(result.draft.evidence),
        "failure_count": len(result.draft.verification.failures) if result.draft.verification else 0,
    }
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(summary["output_path"])
    return 0 if summary["verification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
