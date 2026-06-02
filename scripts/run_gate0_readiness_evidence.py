#!/usr/bin/env python3
"""Generate Gate 0 readiness evidence.

Origin: Created by Codex lead agent on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for any gate.

This script is intended to be run by an operator, test harness, or independent
auditor. It generates evidence files through `EvidenceRun`; the lead agent must
not manually edit the generated artifacts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.config import SETTINGS
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file
from aily.writer.vault_layout import inspect_v1_vault_layout


DEFAULT_PDFS = [
    {
        "role": "small_pdf",
        "path": Path("/Users/luzi/aily_chaos/pdf/wb7-02-ayyagari-pres-user.pdf"),
        "selection_reason": "small representative original PDF for readiness source truth",
    },
    {
        "role": "medium_pdf",
        "path": Path("/Users/luzi/aily_chaos/pdf/tb3-02-ju-pres-user.pdf"),
        "selection_reason": "medium representative original PDF for readiness source truth",
    },
    {
        "role": "large_pdf",
        "path": Path("/Users/luzi/aily_chaos/pdf/dd-12-akash-pres-user.pdf"),
        "selection_reason": "large representative original PDF for readiness source truth",
    },
]

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Aily Gate 0 readiness evidence.")
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=SETTINGS.evidence_runs_dir,
        help="Evidence root directory.",
    )
    return parser.parse_args()


def _run_compileall(repo_root: Path) -> dict[str, Any]:
    command = ["uv", "run", "python", "-m", "compileall", "-q", "aily", "scripts"]
    completed = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _obsidian_rest_probe() -> dict[str, Any]:
    key = SETTINGS.obsidian_rest_api_key
    if not key:
        return {"configured": False, "reachable": False, "status": None, "error": "missing api key"}
    if key.lower().startswith("bearer "):
        return {"configured": True, "reachable": False, "status": None, "error": "api key includes bearer prefix"}

    request = Request(
        f"http://127.0.0.1:{SETTINGS.obsidian_rest_api_port}/",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return {
                "configured": True,
                "reachable": True,
                "status": response.status,
                "server": response.headers.get("Server", ""),
            }
    except HTTPError as exc:
        return {"configured": True, "reachable": False, "status": exc.code, "error": "http error"}
    except URLError as exc:
        return {"configured": True, "reachable": False, "status": None, "error": exc.reason}


def _selected_sources() -> list[dict[str, Any]]:
    records = []
    for item in DEFAULT_PDFS:
        path = item["path"].expanduser().resolve()
        records.append(
            {
                "role": item["role"],
                "path": str(path),
                "selection_reason": item["selection_reason"],
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": sha256_file(path) if path.exists() else None,
            }
        )
    return records


def _folder_presence(vault_path: Path) -> dict[str, Any]:
    return inspect_v1_vault_layout(vault_path)


def _config_snapshot() -> dict[str, Any]:
    return {
        "tavily_api_key_configured": bool(SETTINGS.tavily_api_key),
        "obsidian_rest_api_key_configured": bool(SETTINGS.obsidian_rest_api_key),
        "obsidian_key_has_bearer_prefix": SETTINGS.obsidian_rest_api_key.lower().startswith("bearer "),
        "orchestrator_enabled": SETTINGS.orchestrator_enabled,
        "orchestrator_shadow_mode": SETTINGS.orchestrator_shadow_mode,
        "inbox_watcher_enabled": SETTINGS.inbox_watcher_enabled,
        "dikiwi_foundation_only_ingestion": SETTINGS.dikiwi_foundation_only_ingestion,
        "email_delivery_enabled": SETTINGS.email_delivery_enabled,
        "inbox_path": str(SETTINGS.inbox_path.expanduser().resolve()),
        "inbox_path_exists": SETTINGS.inbox_path.expanduser().exists(),
        "evidence_runs_dir": str(SETTINGS.evidence_runs_dir.expanduser().resolve()),
        "evidence_runs_dir_exists": SETTINGS.evidence_runs_dir.expanduser().exists(),
        "obsidian_vault_path": str(Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path).expanduser().resolve()),
    }


def _gate0_failures(
    *,
    compileall: dict[str, Any],
    config: dict[str, Any],
    rest_probe: dict[str, Any],
    folder_presence: dict[str, Any],
    selected_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if compileall["exit_code"] != 0:
        failures.append({"check": "compileall", "error": "compileall failed"})
    required_true = [
        "tavily_api_key_configured",
        "obsidian_rest_api_key_configured",
        "orchestrator_enabled",
        "inbox_watcher_enabled",
        "dikiwi_foundation_only_ingestion",
        "email_delivery_enabled",
        "inbox_path_exists",
        "evidence_runs_dir_exists",
    ]
    for key in required_true:
        if not config.get(key):
            failures.append({"check": key, "error": "required readiness flag is false"})
    if config.get("orchestrator_shadow_mode"):
        failures.append({"check": "orchestrator_shadow_mode", "error": "shadow mode must be false for gate runs"})
    if config.get("obsidian_key_has_bearer_prefix"):
        failures.append({"check": "obsidian_rest_api_key", "error": "key must not include literal Bearer prefix"})
    if not rest_probe.get("reachable") or rest_probe.get("status") != 200:
        failures.append({"check": "obsidian_rest", "error": "Obsidian REST is not reachable with HTTP 200"})
    if folder_presence.get("missing_required_directories"):
        failures.append({"check": "vault_folders", "missing": folder_presence["missing_required_directories"]})
    for source in selected_sources:
        if not source.get("exists"):
            failures.append({"check": "selected_source", "path": source["path"], "error": "missing selected source"})
    return failures


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    run_id = args.run_id or make_run_id("gate0_readiness")
    selected_sources = _selected_sources()
    source_paths = [Path(str(record["path"])) for record in selected_sources if record.get("exists")]
    vault_path = Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path)

    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="gate0_readiness",
        vault_path=vault_path,
        graph_db_path=SETTINGS.graph_db_path,
        source_paths=source_paths,
        source_selector="default_gate0_pdf_set",
        source_contexts={
            str(record["path"]): {
                "role": record["role"],
                "selection_reason": record["selection_reason"],
            }
            for record in selected_sources
            if record.get("exists")
        },
        mocked=False,
        real_files=True,
        real_graph_db=False,
        real_vault=True,
        real_llm=False,
        claimed_components=["files", "vault"],
        command=sys.argv,
    )
    evidence.capture_before()

    compileall = _run_compileall(repo_root)
    config = _config_snapshot()
    rest_probe = _obsidian_rest_probe()
    folders = _folder_presence(vault_path)
    failures = _gate0_failures(
        compileall=compileall,
        config=config,
        rest_probe=rest_probe,
        folder_presence=folders,
        selected_sources=selected_sources,
    )
    result = {
        "gate": "Gate 0",
        "compileall": compileall,
        "config": config,
        "obsidian_rest": rest_probe,
        "vault_folder_presence": folders,
        "selected_sources": selected_sources,
    }
    evidence.write_json("compileall-result.json", compileall, generation_method="subprocess compileall readiness check")
    evidence.write_json("config-readiness.json", config, generation_method="redacted Settings readiness snapshot")
    evidence.write_json("obsidian-rest-probe.json", rest_probe, generation_method="authenticated Obsidian REST GET / probe")
    evidence.write_json("vault-folder-presence.json", folders, generation_method="filesystem vault folder inspection")
    evidence.write_json("selected-sources.json", selected_sources, generation_method="default Gate 0 PDF selection")

    exit_code = 0 if not failures else 1
    manifest = evidence.finalize(
        exit_code=exit_code,
        result=result,
        failures=failures,
        stderr_text=json.dumps(failures, ensure_ascii=False),
        repo_root=repo_root,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
