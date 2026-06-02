#!/usr/bin/env python3
"""Run Aily-Copilot smoke checks against the configured iCloud vault.

Origin: Created by Codex lead agent on 2026-05-23.
Role: Product smoke-runner source code only; generated evidence carries origin
headers through EvidenceRun.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.config import SETTINGS
from aily.verify.evidence import EvidenceRun, make_run_id
from aily.writer.vault_layout import ensure_v1_vault_layout


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Aily-Copilot real-vault smoke checks.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--vault", type=Path, default=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir)
    parser.add_argument("--use-live-llm", action="store_true")
    parser.add_argument("--seed-if-thin", action="store_true", default=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_id = args.run_id or make_run_id("aily_copilot_real_vault")
    vault_path = args.vault.expanduser().resolve()
    ensure_v1_vault_layout(vault_path)
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    trace_path = SETTINGS.llm_trace_log_path.expanduser() if SETTINGS.llm_trace_log_path else None
    trace_line_start = _line_count(trace_path) if args.use_live_llm and trace_path else 0
    seed_path = _seed_if_needed(vault_path) if args.seed_if_thin else None
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="aily_copilot_real_vault",
        vault_path=vault_path,
        graph_db_path=SETTINGS.graph_db_path,
        mocked=not args.use_live_llm,
        real_files=True,
        real_graph_db=False,
        real_vault=True,
        real_llm=args.use_live_llm,
        real_chat=args.use_live_llm,
        real_workflow=False,
        claimed_components=["files", "vault"] + (["llm", "chat"] if args.use_live_llm else []),
        command=sys.argv,
    )
    evidence.capture_before()

    failures: list[dict[str, Any]] = []
    api = args.api_base_url.rstrip("/")
    plugin_dir = vault_path / ".obsidian" / "plugins" / "aily-copilot"
    plugin_files = sorted(path.name for path in plugin_dir.glob("*")) if plugin_dir.exists() else []
    enabled_plugins = _read_json(vault_path / ".obsidian" / "community-plugins.json", default=[])
    if not {"manifest.json", "main.js", "styles.css"}.issubset(set(plugin_files)):
        failures.append({"check": "plugin_installed", "plugin_dir": str(plugin_dir), "files": plugin_files})
    if "aily-copilot" not in enabled_plugins:
        failures.append({"check": "plugin_enabled", "enabled_plugins": enabled_plugins})

    with httpx.Client(timeout=180.0, trust_env=False) as client:
        responses = {
            "status": _request(client, "GET", f"{api}/api/copilot/status"),
            "project": _request(
                client,
                "POST",
                f"{api}/api/copilot/projects/upsert",
                json={
                    "name": "Aily Copilot Product Smoke",
                    "include_dirs": ["03-Knowledge", "10-Dossiers"],
                    "source_terms": ["Aily-Copilot", "Obsidian", "dossier"],
                    "system_prompt": "Prefer product-readiness and human-readable vault evidence.",
                },
            ),
        }
        project_id = responses["project"].get("json", {}).get("project", {}).get("id", "")
        responses["search"] = _request(
            client,
            "POST",
            f"{api}/api/copilot/vault/search",
            json={"query": "Aily-Copilot Obsidian dossier product readiness", "limit": 5},
        )
        responses["chat"] = _request(
            client,
            "POST",
            f"{api}/api/copilot/chat",
            json={
                "message": "What is Aily-Copilot ready to do inside Obsidian, based only on vault evidence?",
                "search_query": "Aily-Copilot Obsidian dossier product readiness",
                "project_id": project_id,
                "limit": 5,
                "use_llm": bool(args.use_live_llm),
            },
        )
        responses["relevant"] = _request(
            client,
            "POST",
            f"{api}/api/copilot/vault/relevant",
            json={
                "query": "Aily-Copilot product readiness",
                "seed_paths": [seed_path.relative_to(vault_path).as_posix()] if seed_path else [],
                "project_id": project_id,
                "limit": 8,
            },
        )
        responses["proposal"] = _request(
            client,
            "POST",
            f"{api}/api/copilot/proposals/create",
            json={
                "title": "Aily Copilot Smoke Draft",
                "target_path": "10-Dossiers/Aily Copilot Smoke Draft.md",
                "content": "# Aily Copilot Smoke Draft\n\nThis is a preview-only smoke draft. It should be rejected by the smoke test.\n",
                "mode": "create",
                "rationale": "Verify preview-first write staging without altering the target vault note.",
                "source_citations": responses["chat"].get("json", {}).get("citations", []),
            },
        )
        proposal_id = responses["proposal"].get("json", {}).get("proposal", {}).get("id", "")
        responses["reject"] = _request(
            client,
            "POST",
            f"{api}/api/copilot/proposals/reject",
            json={"proposal_id": proposal_id},
        ) if proposal_id else {"status_code": 0, "json": {}, "text": "proposal not created"}

    for name, response in responses.items():
        if response["status_code"] != 200:
            failures.append({"check": f"{name}_status", "response": response})
    chat_payload = responses["chat"].get("json", {})
    if chat_payload.get("grounding_status") != "grounded":
        failures.append({"check": "chat_grounded", "chat": chat_payload})
    if args.use_live_llm and chat_payload.get("used_llm") is not True:
        failures.append({"check": "live_llm_used", "chat": chat_payload})
    llm_delta_path = None
    if args.use_live_llm and trace_path:
        llm_delta_path = runtime_dir / "llm-calls-delta.jsonl"
        delta_lines = _tail_lines(trace_path, trace_line_start)
        llm_delta_path.write_text("\n".join(delta_lines) + ("\n" if delta_lines else ""), encoding="utf-8")
        if not any('"workload": "copilot.chat"' in line and '"provider": "deepseek"' in line for line in delta_lines):
            failures.append({"check": "copilot_chat_deepseek_trace_present", "delta_record_count": len(delta_lines)})
    if not chat_payload.get("citations"):
        failures.append({"check": "chat_has_citations", "chat": chat_payload})
    if not responses["relevant"].get("json", {}).get("recommendations"):
        failures.append({"check": "relevant_notes_returned", "relevant": responses["relevant"].get("json", {})})
    if (vault_path / "10-Dossiers" / "Aily Copilot Smoke Draft.md").exists():
        failures.append({"check": "proposal_reject_left_target_unchanged"})

    evidence.write_json(
        "real-vault-smoke.json",
        {
            "vault_path": str(vault_path),
            "seed_path": str(seed_path) if seed_path else "",
            "plugin_dir": str(plugin_dir),
            "plugin_files": plugin_files,
            "enabled_plugins": enabled_plugins,
            "responses": responses,
            "use_live_llm": bool(args.use_live_llm),
        },
        generation_method="Aily-Copilot HTTP API smoke checks against configured real vault",
    )
    manifest = evidence.finalize(
        exit_code=0 if not failures else 1,
        result={
            "vault_path": str(vault_path),
            "plugin_installed": not any(item.get("check") == "plugin_installed" for item in failures),
            "plugin_enabled": "aily-copilot" in enabled_plugins,
            "chat_grounding_status": chat_payload.get("grounding_status"),
            "used_llm": chat_payload.get("used_llm"),
            "citation_count": len(chat_payload.get("citations", [])),
            "relevant_returned": responses["relevant"].get("json", {}).get("returned"),
        },
        failures=failures,
        llm_log_file=str(llm_delta_path) if llm_delta_path else None,
        repo_root=Path(__file__).resolve().parents[1],
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


def _seed_if_needed(vault_path: Path) -> Path | None:
    searchable = [
        path for path in vault_path.rglob("*.md")
        if not path.relative_to(vault_path).as_posix().startswith((".obsidian/", "99-MOC/", "99-System/"))
    ]
    target = vault_path / "03-Knowledge" / "Aily Copilot Product Readiness.md"
    companion = vault_path / "10-Dossiers" / "Aily Copilot Human Workflow.md"
    if target.exists():
        _ensure_companion_note(companion)
        return target
    has_product_note = any(
        "aily-copilot" in path.read_text(encoding="utf-8", errors="replace").lower()
        for path in searchable[:200]
    )
    if len(searchable) >= 4 and has_product_note:
        return next(
            (path for path in searchable if "aily-copilot" in path.read_text(encoding="utf-8", errors="replace").lower()),
            searchable[0] if searchable else None,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        "origin_creator: scripts/run_aily_copilot_real_vault_smoke.py\n"
        "origin_generation_method: real-vault smoke seed for Aily-Copilot product testing\n"
        "origin_modified_by_lead_agent: false\n"
        "tags: [aily-copilot, obsidian, product-readiness]\n"
        "---\n\n"
        "# Aily Copilot Product Readiness\n\n"
        "Aily-Copilot connects the Obsidian vault to Aily backend APIs for grounded chat, citation-backed source review, "
        "content-based relevant-note navigation, dossier generation, project-scoped retrieval, and preview-first draft proposals.\n\n"
        "It is intended to help a human work inside Obsidian while keeping substantive claims tied to vault evidence. "
        "Draft writing must be previewed and explicitly approved before a target note is changed.\n",
        encoding="utf-8",
    )
    _ensure_companion_note(companion)
    return target


def _ensure_companion_note(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "origin_creator: scripts/run_aily_copilot_real_vault_smoke.py\n"
        "origin_generation_method: companion note for Aily-Copilot relevant-note smoke testing\n"
        "origin_modified_by_lead_agent: false\n"
        "tags: [aily-copilot, obsidian, human-workflow]\n"
        "---\n\n"
        "# Aily Copilot Human Workflow\n\n"
        "This companion note describes the human workflow around [[Aily Copilot Product Readiness]]. "
        "A user can chat with the vault, inspect cited sources, open relevant notes, generate dossiers, "
        "and stage a draft as a preview before approving any write into the Obsidian vault.\n",
        encoding="utf-8",
    )


def _request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = client.request(method, url, **kwargs)
    except Exception as exc:
        return {"status_code": 0, "json": {}, "text": str(exc)}
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {}
    return {"status_code": response.status_code, "json": payload, "text": response.text[:2000]}


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _line_count(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _tail_lines(path: Path, start: int) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[max(0, start) :]


if __name__ == "__main__":
    raise SystemExit(main())
