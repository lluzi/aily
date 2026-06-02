#!/usr/bin/env python3
"""Run deterministic Aily-Copilot backend evidence.

Origin: Created by Codex lead agent on 2026-05-23.
Role: Evidence-runner source code only; generated evidence carries origin
headers through EvidenceRun.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aily.config import SETTINGS
from aily.copilot.context import CopilotContextEnvelopeBuilder
from aily.copilot.router import create_copilot_router
from aily.copilot.vault import VaultSearchService
from aily.verify.evidence import EvidenceRun, make_run_id
from aily.writer.vault_layout import ensure_v1_vault_layout


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Aily-Copilot backend evidence.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir)
    return parser.parse_args()


def _write_fixture_vault(vault_path: Path) -> dict[str, str]:
    ensure_v1_vault_layout(vault_path)
    notes = {
        "03-Knowledge/JR-GO Technical Proposal.md": (
            "---\n"
            "origin_creator: evidence-runner\n"
            "origin_generation_method: Aily-Copilot backend fixture\n"
            "origin_modified_by_lead_agent: false\n"
            "tags: [jr-go, chiplet, eda]\n"
            "---\n\n"
            "# JR-GO Technical Proposal\n\n"
            "JR-GO combines EDA workflow automation, chiplet planning, and AI reasoning. "
            "The proposal depends on [[EDA Workflow Evidence]] and should be reviewed as a semiconductor product system.\n"
        ),
        "02-Information/EDA Workflow Evidence.md": (
            "---\n"
            "origin_creator: evidence-runner\n"
            "origin_generation_method: Aily-Copilot backend fixture\n"
            "origin_modified_by_lead_agent: false\n"
            "tags: [eda, workflow]\n"
            "---\n\n"
            "# EDA Workflow Evidence\n\n"
            "The source notes describe timing closure, verification bottlenecks, and reusable automation patterns for EDA teams.\n"
        ),
        "10-Dossiers/JR-GO Learning Dossier.md": (
            "---\n"
            "origin_creator: evidence-runner\n"
            "origin_generation_method: Aily-Copilot backend fixture\n"
            "origin_modified_by_lead_agent: false\n"
            "tags: [jr-go, dossier]\n"
            "---\n\n"
            "# JR-GO Learning Dossier\n\n"
            "The dossier explains why JR-GO must connect product claims to source evidence before leadership review.\n"
        ),
    }
    written: dict[str, str] = {}
    for relative_path, text in notes.items():
        path = vault_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written[relative_path] = str(path)
    return written


def _failure(check: str, **details: Any) -> dict[str, Any]:
    return {"check": check, **details}


def main() -> int:
    args = _parse_args()
    run_id = args.run_id or make_run_id("aily_copilot_backend")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    vault_path = runtime_dir / "fixture-vault"
    graph_db_path = runtime_dir / "graph.db"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    fixture_files = _write_fixture_vault(vault_path)
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="aily_copilot_backend",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        mocked=True,
        real_files=True,
        real_graph_db=False,
        real_vault=True,
        real_llm=False,
        real_chat=False,
        real_workflow=False,
        claimed_components=["files", "vault"],
        command=sys.argv,
    )
    evidence.capture_before()

    service = VaultSearchService(vault_path)
    builder = CopilotContextEnvelopeBuilder()
    failures: list[dict[str, Any]] = []

    search_result = service.search("JR-GO EDA chiplet", limit=5)
    if search_result["returned"] < 2:
        failures.append(_failure("search_returned_expected_notes", search=search_result))
    if not any(item["relative_path"] == "03-Knowledge/JR-GO Technical Proposal.md" for item in search_result["results"]):
        failures.append(_failure("search_finds_exact_topic_note", search=search_result))

    read_result = service.read_note("03-Knowledge/JR-GO Technical Proposal.md")
    if "EDA Workflow Evidence" not in read_result["wikilinks"]:
        failures.append(_failure("read_note_extracts_wikilinks", read=read_result))
    if not read_result["backlinks"]:
        # This fixture has no backlinks to JR-GO; absence is acceptable but recorded.
        read_result["backlink_observation"] = "No backlink fixture points to JR-GO Technical Proposal."

    neighborhood = service.neighborhood("03-Knowledge/JR-GO Technical Proposal.md")
    if not any(item["target"] == "EDA Workflow Evidence" for item in neighborhood["outgoing"]):
        failures.append(_failure("neighborhood_reports_outgoing_link", neighborhood=neighborhood))

    try:
        service.read_note("../.env")
        failures.append(_failure("path_traversal_rejected", error="read_note accepted parent traversal"))
    except ValueError:
        pass

    envelope_a = builder.build(
        user_message="Explain JR-GO from the vault.",
        search_results=search_result["results"],
    )
    envelope_b = builder.build(
        user_message="Explain JR-GO from the vault.",
        search_results=search_result["results"],
    )
    if envelope_a["combined_hash"] != envelope_b["combined_hash"]:
        failures.append(_failure("context_hash_stability", first=envelope_a, second=envelope_b))
    if not envelope_a["citation_catalog"]:
        failures.append(_failure("citation_catalog_present", envelope=envelope_a))

    app = FastAPI()
    app.include_router(create_copilot_router(vault_path=vault_path, state_dir=runtime_dir / "copilot-state"))
    client = TestClient(app)
    api_status = client.get("/api/copilot/status")
    api_project = client.post(
        "/api/copilot/projects/upsert",
        json={
            "name": "JR-GO EDA project",
            "include_dirs": ["02-Information", "03-Knowledge", "10-Dossiers"],
            "source_terms": ["JR-GO", "EDA", "chiplet"],
            "system_prompt": "Focus on semiconductor product evidence.",
        },
    )
    project_id = api_project.json().get("project", {}).get("id", "") if api_project.status_code == 200 else ""
    api_search = client.post("/api/copilot/vault/search", json={"query": "JR-GO EDA", "limit": 3})
    api_read = client.post(
        "/api/copilot/vault/read",
        json={"path": "03-Knowledge/JR-GO Technical Proposal.md", "chunk_lines": 50},
    )
    api_envelope = client.post(
        "/api/copilot/context/envelope",
        json={"user_message": "Explain JR-GO", "search_results": api_search.json().get("results", [])},
    )
    api_chat = client.post(
        "/api/copilot/chat",
        json={
            "message": "Explain JR-GO as a product system.",
            "search_query": "JR-GO EDA chiplet",
            "project_id": project_id,
            "use_llm": False,
        },
    )
    api_relevant = client.post(
        "/api/copilot/vault/relevant",
        json={
            "query": "JR-GO product evidence",
            "seed_paths": ["03-Knowledge/JR-GO Technical Proposal.md"],
            "project_id": project_id,
            "limit": 5,
        },
    )
    api_dossier = client.post(
        "/api/copilot/dossiers/generate",
        json={
            "topic": "JR-GO EDA chiplet product system",
            "query_terms": ["JR-GO", "EDA", "chiplet"],
            "seed_claims": ["JR-GO connects EDA workflow evidence to product reasoning."],
            "max_vault_evidence": 20,
        },
    )
    api_proposal = client.post(
        "/api/copilot/proposals/create",
        json={
            "title": "JR-GO Copilot Evidence Brief",
            "target_path": "10-Dossiers/JR-GO Copilot Evidence Brief.md",
            "content": "# JR-GO Copilot Evidence Brief\n\nJR-GO combines EDA automation and chiplet planning based on cited vault evidence.\n",
            "mode": "create",
            "rationale": "Evidence-runner preview-first write test.",
            "source_citations": [{"citation_id": "V001", "relative_path": "03-Knowledge/JR-GO Technical Proposal.md"}],
        },
    )
    proposal_id = api_proposal.json().get("proposal", {}).get("id", "") if api_proposal.status_code == 200 else ""
    api_apply = client.post("/api/copilot/proposals/apply", json={"proposal_id": proposal_id}) if proposal_id else None
    for name, response in {
        "api_status": api_status,
        "api_project": api_project,
        "api_search": api_search,
        "api_read": api_read,
        "api_envelope": api_envelope,
        "api_chat": api_chat,
        "api_relevant": api_relevant,
        "api_dossier": api_dossier,
        "api_proposal": api_proposal,
        "api_apply": api_apply,
    }.items():
        if response is None:
            failures.append(_failure(name, status_code=0, body="request not executed"))
            continue
        if response.status_code != 200:
            failures.append(_failure(name, status_code=response.status_code, body=response.text))
    if api_chat.status_code == 200:
        chat_payload = api_chat.json()
        if chat_payload.get("grounding_status") != "grounded":
            failures.append(_failure("chat_grounded", chat=chat_payload))
        if not chat_payload.get("citations"):
            failures.append(_failure("chat_has_citations", chat=chat_payload))
        if "[V001]" not in chat_payload.get("answer", ""):
            failures.append(_failure("chat_answer_uses_citation", chat=chat_payload))
    if api_dossier.status_code == 200:
        dossier_payload = api_dossier.json()
        dossier_path = vault_path / str(dossier_payload.get("relative_path") or "")
        if not str(dossier_payload.get("relative_path") or "").startswith("10-Dossiers/"):
            failures.append(_failure("dossier_written_to_10_dossiers", dossier=dossier_payload))
        if not dossier_path.is_file():
            failures.append(_failure("dossier_file_exists", dossier=dossier_payload))
    if api_relevant.status_code == 200 and not api_relevant.json().get("recommendations"):
        failures.append(_failure("relevant_notes_present", relevant=api_relevant.json()))
    if api_apply and api_apply.status_code == 200:
        applied_payload = api_apply.json()
        applied_path = vault_path / str(applied_payload.get("proposal", {}).get("target_path", ""))
        if not applied_path.is_file():
            failures.append(_failure("proposal_apply_writes_target_after_approval", proposal=applied_payload))

    result = {
        "fixture_files": fixture_files,
        "search_returned": search_result["returned"],
        "top_result": search_result["results"][0] if search_result["results"] else {},
        "read_note_path": read_result["relative_path"],
        "neighborhood_outgoing_count": len(neighborhood["outgoing"]),
        "context_combined_hash": envelope_a["combined_hash"],
        "citation_count": len(envelope_a["citation_catalog"]),
        "api_search_returned": api_search.json().get("returned") if api_search.status_code == 200 else None,
        "api_chat_grounding_status": api_chat.json().get("grounding_status") if api_chat.status_code == 200 else None,
        "api_relevant_returned": api_relevant.json().get("returned") if api_relevant.status_code == 200 else None,
        "api_dossier_relative_path": api_dossier.json().get("relative_path") if api_dossier.status_code == 200 else None,
        "api_project_id": project_id,
        "api_proposal_status": api_apply.json().get("proposal", {}).get("status") if api_apply and api_apply.status_code == 200 else None,
    }
    evidence.write_json("fixture-files.json", fixture_files, generation_method="Aily-Copilot fixture vault creation")
    evidence.write_json("vault-search.json", search_result, generation_method="VaultSearchService.search")
    evidence.write_json("read-note.json", read_result, generation_method="VaultSearchService.read_note")
    evidence.write_json("graph-neighborhood.json", neighborhood, generation_method="VaultSearchService.neighborhood")
    evidence.write_json("context-envelope.json", envelope_a, generation_method="CopilotContextEnvelopeBuilder.build")
    evidence.write_json(
        "api-smoke.json",
        {
            "status": api_status.json() if api_status.status_code == 200 else api_status.text,
            "project": api_project.json() if api_project.status_code == 200 else api_project.text,
            "search": api_search.json() if api_search.status_code == 200 else api_search.text,
            "read": api_read.json() if api_read.status_code == 200 else api_read.text,
            "envelope": api_envelope.json() if api_envelope.status_code == 200 else api_envelope.text,
            "chat": api_chat.json() if api_chat.status_code == 200 else api_chat.text,
            "relevant": api_relevant.json() if api_relevant.status_code == 200 else api_relevant.text,
            "dossier": api_dossier.json() if api_dossier.status_code == 200 else api_dossier.text,
            "proposal": api_proposal.json() if api_proposal.status_code == 200 else api_proposal.text,
            "apply": api_apply.json() if api_apply and api_apply.status_code == 200 else (api_apply.text if api_apply else ""),
        },
        generation_method="FastAPI TestClient calls against create_copilot_router",
    )
    manifest = evidence.finalize(
        exit_code=0 if not failures else 1,
        result=result,
        failures=failures,
        repo_root=Path(__file__).resolve().parents[1],
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
