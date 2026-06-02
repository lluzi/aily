#!/usr/bin/env python3
"""Run the 20-PDF product-maturity evidence pass.

Origin: Created by Codex lead agent on 2026-05-25.
Role: Evidence-runner source code only; not acceptance evidence by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.business import BusinessPlanStore
from aily.chaos.chaos_markdown import render_chaos_markdown
from aily.chaos.types import ExtractedContentMultimodal
from aily.config import SETTINGS
from aily.dossier import DossierBuildRequest, DossierService
from aily.gating.drainage import RainDrop, RainType, StreamType
from aily.graph.db import GraphDB
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.orchestration.chat_store import ChatStore
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.source_foundation_graph import _enum_name, _safe_chaos_base_name, _stage_results_payload
from aily.processing.canonical_markdown import CanonicalMarkdownConverter
from aily.processing.router import ProcessingRouter
from aily.research import ResearchStore
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file, vault_counts
from aily.verify.chaos_quality import score_chaos_vault
from aily.verify.llm_traffic import build_traffic_monitor
from aily.verify.obsidian_quality import QualityThresholds, score_vault_output
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter
from aily.writer.vault_layout import ensure_v1_vault_layout, write_canonical_markdown_vault_artifact
from scripts.run_m5_graph_owned_iwi_evidence import _emit, _run_payload
from scripts.run_m7_business_plan_evidence import _run_business_graph, _sanitize_packet, _sqlite_summary


DEFAULT_PDF_DIR = Path("/Users/luzi/aily_chaos/pdf")
DEFAULT_VISIBLE_VAULT = Path("/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily")
PREVIOUS_10PDF_NAMES = {
    "lp-01-tu-paper.pdf",
    "lp-02-wu-paper.pdf",
    "verification-2-tu-paper.pdf",
    "tb01-03-imperato-paper.pdf",
    "meloux-paper.pdf",
    "lowpower-9-meloux-pres-user.pdf",
    "paper16-hua.pdf",
    "paper10-heng.pdf",
    "publish-only-139-mehta.pdf",
    "publish-only-102-bisht.pdf",
}
FOUNDATION_STAGE_DIRS = ("01-Data", "02-Information", "03-Knowledge")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Aily 20-PDF product maturity evidence.")
    parser.add_argument("--pdf", action="append", type=Path, default=[], help="Explicit selected PDF. Repeat 20 times.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--pdf-count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260525)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir)
    parser.add_argument("--vault-path", type=Path, default=DEFAULT_VISIBLE_VAULT)
    parser.add_argument("--conversion-workers", type=int, default=2)
    parser.add_argument("--foundation-workers", type=int, default=1)
    parser.add_argument("--per-source-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--timeout-seconds", type=float, default=5400.0)
    parser.add_argument("--research-model", default="mini", choices=["mini", "pro"])
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--manage-backend", action="store_true", help="Stop duplicate Aily backends and start one clean backend.")
    parser.add_argument("--dry-run", action="store_true", help="Select PDFs and write a non-acceptance preflight manifest only.")
    return parser.parse_args()


def _read_selected_names_from_prior_runs(runs_root: Path) -> set[str]:
    names = set(PREVIOUS_10PDF_NAMES)
    root = runs_root.expanduser().resolve()
    if not root.exists():
        return names
    for path in root.glob("*/selected-pdfs.json"):
        if "10pdf" not in path.parent.name.lower() and "20pdf" not in path.parent.name.lower():
            continue
        with suppress(Exception):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw_path in (payload.get("pdfs") or {}).keys():
                names.add(Path(raw_path).name)
    return names


def _select_pdfs(args: argparse.Namespace) -> tuple[list[Path], set[str]]:
    expected = int(args.pdf_count)
    if expected < 1:
        raise ValueError("--pdf-count must be >= 1")
    excluded = _read_selected_names_from_prior_runs(args.runs_root)
    if args.pdf:
        selected = [path.expanduser().resolve() for path in args.pdf]
        expected = len(selected)
    else:
        candidates = [
            path.resolve()
            for path in sorted(args.pdf_dir.expanduser().glob("*.pdf"))
            if path.name not in excluded
        ]
        if len(candidates) < expected:
            raise ValueError(f"Expected at least {expected} new candidate PDFs, got {len(candidates)}")
        selected = random.Random(args.seed).sample(candidates, expected)
    if len(selected) != expected:
        raise ValueError(f"Expected exactly {expected} PDFs, got {len(selected)}")
    missing = [str(path) for path in selected if not path.is_file() or path.suffix.lower() != ".pdf"]
    if missing:
        raise FileNotFoundError(f"Invalid selected PDFs: {missing}")
    if len({path.resolve() for path in selected}) != len(selected):
        raise ValueError("Selected PDFs must be distinct")
    return selected, excluded


def _backend_processes() -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if "uv run python -m aily.main" not in stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        with suppress(ValueError):
            records.append({"pid": int(pid_text), "command": command.strip()})
    return records


def _manage_backend(repo_root: Path, runtime_dir: Path, enabled: bool) -> dict[str, Any]:
    before = _backend_processes()
    result: dict[str, Any] = {"enabled": enabled, "before": before, "terminated": [], "started": None, "after": before}
    if not enabled:
        return result
    for proc in before:
        pid = int(proc["pid"])
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
            result["terminated"].append(pid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _backend_processes():
        time.sleep(0.25)
    for proc in _backend_processes():
        with suppress(ProcessLookupError):
            os.kill(int(proc["pid"]), signal.SIGKILL)
    stdout = (runtime_dir / "backend.stdout.log").open("w", encoding="utf-8")
    stderr = (runtime_dir / "backend.stderr.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["uv", "run", "python", "-m", "aily.main"],
        cwd=repo_root,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    time.sleep(5)
    result["started"] = {"pid": process.pid, "running": process.poll() is None}
    result["after"] = _backend_processes()
    return result


def _preflight(vault_path: Path, runtime_dir: Path, *, manage_backend: bool, repo_root: Path) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    ensure_v1_vault_layout(vault)
    probe = vault / "99-System" / ".aily_write_probe"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("ok\n", encoding="utf-8")
    with suppress(FileNotFoundError):
        probe.unlink()
    return {
        "backend": _manage_backend(repo_root, runtime_dir, manage_backend),
        "providers": {
            "kimi_api_key_present": bool(SETTINGS.kimi_api_key or SETTINGS.llm_api_key),
            "kimi_model": SETTINGS.kimi_model,
            "deepseek_api_key_present": bool(SETTINGS.deepseek_api_key),
            "deepseek_model": SETTINGS.deepseek_model,
            "tavily_api_key_present": bool(SETTINGS.tavily_api_key),
            "secrets_redacted": True,
        },
        "vault": {"path": str(vault), "exists": vault.exists(), "writable": os.access(vault, os.W_OK)},
    }


async def _admit_sources(
    *,
    selected_pdfs: list[Path],
    run_id: str,
    source_store: SourceStore,
) -> list[dict[str, Any]]:
    admitted: list[dict[str, Any]] = []
    for index, pdf_path in enumerate(selected_pdfs, start=1):
        upload_id = f"{run_id}-{index:02d}-{pdf_path.stem}"[:180]
        metadata = {
            "intake": "20pdf_product_maturity",
            "evidence_run_id": run_id,
            "batch_id": run_id,
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "selection_reason": "new 20-PDF maturity rerun; prior 10-PDF and failed 20-PDF selections excluded",
        }
        source = await source_store.store_upload(
            upload_id=upload_id,
            filename=pdf_path.name,
            content_type="application/pdf",
            data=pdf_path.read_bytes(),
            metadata=metadata,
        )
        job = await source_store.enqueue_source_job(
            source_id=source["source_id"],
            job_type="process_upload_source",
            payload={
                "upload_id": upload_id,
                "filename": pdf_path.name,
                "content_type": "application/pdf",
                "origin_path": str(pdf_path),
                "source_kind": "20pdf_maturity_pdf",
                "batch_id": run_id,
                "evidence_run_id": run_id,
            },
        )
        admitted.append({"pdf_path": str(pdf_path), "source": source, "source_job": job, "metadata": metadata})
    return admitted


async def _convert_one_source(
    *,
    admission: dict[str, Any],
    source_store: SourceStore,
    vault_path: Path,
    run_id: str,
) -> dict[str, Any]:
    pdf_path = Path(str(admission["pdf_path"]))
    source = admission["source"]
    source_id = str(source["source_id"])
    data = await source_store.read_stored_object(source_id)
    extracted = await ProcessingRouter().process(data, filename=pdf_path.name, http_content_type="application/pdf")
    await source_store.update_status(
        source_id,
        "extracted",
        {
            "evidence_run_id": run_id,
            "batch_id": run_id,
            "source_type": extracted.source_type,
            "extracted_chars": len(extracted.text or ""),
            "title": extracted.title or pdf_path.stem,
        },
    )
    package = await CanonicalMarkdownConverter(source_store=source_store).convert_extracted(
        source_id=source_id,
        extracted=extracted,
        fallback_title=pdf_path.stem,
        metadata={
            "created_from": "20pdf_product_maturity_conversion_phase",
            "evidence_run_id": run_id,
            "batch_id": run_id,
            "origin_path": str(pdf_path),
            "storage_path": str(source.get("storage_path") or ""),
        },
    )
    canonical_artifact = write_canonical_markdown_vault_artifact(
        vault_path,
        source_id=source_id,
        package_id=package.package_id,
        markdown_sha256=package.markdown_sha256,
        title=package.title,
        source_type=package.source_type,
        markdown=package.markdown,
        origin_path=str(pdf_path),
        storage_path=str(source.get("storage_path") or ""),
    )
    source_file = Path(str(source.get("storage_path") or "")).expanduser().resolve()
    base_name = _safe_chaos_base_name(pdf_path.name)
    chaos_path = vault_path / "00-Chaos" / f"{base_name}.md"
    chaos_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = await render_chaos_markdown(
        extracted=ExtractedContentMultimodal(
            text=extracted.text,
            title=extracted.title or pdf_path.stem,
            source_type=extracted.source_type,
            source_path=source_file,
            metadata={
                **dict(getattr(extracted, "metadata", {}) or {}),
                "source_id": source_id,
                "batch_id": run_id,
                "evidence_run_id": run_id,
            },
            processing_method="20pdf_product_maturity_pdf_chaos",
        ),
        source_path=source_file,
        base_name=base_name,
        vault_path=vault_path,
        source_display_name=pdf_path.name,
        extra_origin_fields={"source_id": source_id, "batch_id": run_id, "evidence_run_id": run_id},
    )
    chaos_path.write_text(rendered.markdown, encoding="utf-8")
    await source_store.update_status(
        source_id,
        "converted",
        {
            "evidence_run_id": run_id,
            "batch_id": run_id,
            "canonical_markdown_package_id": package.package_id,
            "canonical_markdown_path": package.package_path,
            "canonical_markdown_sha256": package.markdown_sha256,
            "canonical_markdown_vault_path": canonical_artifact["path"],
            "chaos_markdown_vault_path": str(chaos_path),
        },
    )
    return {
        "source_id": source_id,
        "pdf_path": str(pdf_path),
        "canonical_markdown_package": asdict(package),
        "canonical_markdown_vault_artifact": canonical_artifact,
        "chaos_markdown_vault_artifact": {
            "path": str(chaos_path),
            "relative_path": str(chaos_path.relative_to(vault_path)),
            "page_count": rendered.page_count,
            "screenshot_count": rendered.screenshot_count,
            "screenshot_renderer": rendered.screenshot_renderer,
            "screenshot_error": rendered.screenshot_error,
        },
    }


async def _convert_sources(
    *,
    admissions: list[dict[str, Any]],
    source_store: SourceStore,
    vault_path: Path,
    run_id: str,
    workers: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, workers))

    async def run_one(admission: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await _convert_one_source(
                    admission=admission,
                    source_store=source_store,
                    vault_path=vault_path,
                    run_id=run_id,
                )
            except Exception as exc:
                source_id = str((admission.get("source") or {}).get("source_id") or "")
                if source_id:
                    await source_store.update_status(source_id, "failed", {"error": str(exc), "failed_phase": "conversion"})
                return {"source_id": source_id, "pdf_path": admission.get("pdf_path", ""), "status": "failed", "error": str(exc)}

    return await asyncio.gather(*(run_one(admission) for admission in admissions))


def _knowledge_stage_completed(result: Any) -> bool:
    return any(
        str(stage.get("stage") or "").upper() == "KNOWLEDGE" and bool(stage.get("success"))
        for stage in _stage_results_payload(result)
    )


async def _process_foundation_job(
    *,
    source_store: SourceStore,
    workflow_store: WorkflowRunStore,
    mind: DikiwiMind,
    converted_by_source: dict[str, dict[str, Any]],
    run_id: str,
    worker_id: str,
    timeout_seconds: float,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    job = await source_store.claim_next_source_job(worker_id=worker_id)
    if job is None:
        return None
    source_id = str(job["source_id"])
    converted = converted_by_source.get(source_id, {})
    source = await source_store.get_source(source_id)
    package = await source_store.get_markdown_package(source_id)
    if source is None or package is None:
        error = "source or canonical markdown package missing before foundation processing"
        await source_store.update_status(source_id, "failed", {"error": error, "failed_phase": "foundation_preflight"})
        await source_store.fail_source_job(str(job["job_id"]), error=error)
        return {"source_id": source_id, "job": job, "status": "failed", "error": error}
    workflow_run = await workflow_store.create_run(
        workflow_kind="source_foundation",
        input_summary=f"20-PDF maturity foundation: {source.get('filename') or source_id}",
        metadata={"source_id": source_id, "job_id": job["job_id"], "runner": "20pdf_product_maturity", "evidence_run_id": run_id},
    )
    markdown = await source_store.read_markdown_package(source_id)
    source_paths = [
        f"source_id:{source_id}",
        str((source.get("metadata") or {}).get("origin_path") or ""),
        str(source.get("storage_path") or ""),
        str(package.get("package_path") or ""),
        str(converted.get("canonical_markdown_vault_artifact", {}).get("path") or ""),
        str(converted.get("chaos_markdown_vault_artifact", {}).get("path") or ""),
    ]
    source_paths = [item for item in source_paths if item]
    drop = RainDrop(
        id="",
        rain_type=RainType.DOCUMENT,
        content=markdown,
        raw_bytes=markdown.encode("utf-8"),
        source="20pdf_product_maturity",
        source_id=source_id,
        stream_type=StreamType.EXTRACT_ANALYZE,
        metadata={
            "workflow_run_id": workflow_run.workflow_run_id,
            "langgraph_thread_id": workflow_run.langgraph_thread_id,
            "source_id": source_id,
            "job_id": job["job_id"],
            "upload_id": (job.get("payload") or {}).get("upload_id", ""),
            "batch_id": run_id,
            "evidence_run_id": run_id,
            "filename": source.get("filename", ""),
            "content_type": source.get("content_type", ""),
            "source_type": package.get("source_type", ""),
            "processing_method": "20pdf_product_maturity_foundation",
            "canonical_markdown_package_id": package.get("package_id", ""),
            "canonical_markdown_path": package.get("package_path", ""),
            "canonical_markdown_vault_path": converted.get("canonical_markdown_vault_artifact", {}).get("path", ""),
            "canonical_markdown_sha256": package.get("markdown_sha256", ""),
            "origin_path": (source.get("metadata") or {}).get("origin_path", ""),
            "storage_path": source.get("storage_path", ""),
            "source_paths": source_paths,
        },
    )
    started = time.monotonic()
    try:
        await _emit(events, "source_foundation_started", source_id=source_id, job_id=job["job_id"], workflow_run_id=workflow_run.workflow_run_id)
        await workflow_store.update_status(workflow_run.workflow_run_id, status="running", current_node="foundation_dikiwi")
        result = await asyncio.wait_for(mind.process_input_foundation(drop), timeout=timeout_seconds)
        stage_results = _stage_results_payload(result)
        completed = _knowledge_stage_completed(result)
        status = "completed" if completed else "failed"
        error = "" if completed else "DIKIWI foundation did not reach KNOWLEDGE"
        await source_store.update_status(
            source_id,
            status,
            {
                "evidence_run_id": run_id,
                "batch_id": run_id,
                "pipeline_id": getattr(result, "pipeline_id", ""),
                "final_stage": _enum_name(getattr(result, "final_stage_reached", "")),
                "stage_results": stage_results,
                "error": error,
            },
        )
        if completed:
            await source_store.complete_source_job(str(job["job_id"]))
            await workflow_store.update_status(workflow_run.workflow_run_id, status="completed", current_node="knowledge_completed")
        else:
            await source_store.fail_source_job(str(job["job_id"]), error=error)
            await workflow_store.update_status(workflow_run.workflow_run_id, status="failed", current_node="foundation_failed", last_error=error)
        await _emit(
            events,
            "source_foundation_completed" if completed else "source_foundation_failed",
            source_id=source_id,
            job_id=job["job_id"],
            workflow_run_id=workflow_run.workflow_run_id,
            status=status,
            error=error or None,
        )
        return {
            "source_id": source_id,
            "job": job,
            "workflow_run": _run_payload(workflow_run),
            "status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "pipeline_id": getattr(result, "pipeline_id", ""),
            "final_stage": _enum_name(getattr(result, "final_stage_reached", "")),
            "stage_results": stage_results,
            "error": error,
        }
    except asyncio.TimeoutError:
        error = f"foundation processing timed out after {timeout_seconds:.0f}s"
    except Exception as exc:
        error = str(exc)
    await source_store.update_status(source_id, "failed", {"error": error, "failed_phase": "foundation", "evidence_run_id": run_id})
    await source_store.fail_source_job(str(job["job_id"]), error=error)
    await workflow_store.update_status(workflow_run.workflow_run_id, status="failed", current_node="foundation_failed", last_error=error)
    await _emit(events, "source_foundation_failed", source_id=source_id, job_id=job["job_id"], error=error)
    return {
        "source_id": source_id,
        "job": job,
        "workflow_run": _run_payload(workflow_run),
        "status": "failed",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stage_results": [],
        "error": error,
    }


async def _process_foundations(
    *,
    source_store: SourceStore,
    workflow_store: WorkflowRunStore,
    mind: DikiwiMind,
    conversions: list[dict[str, Any]],
    run_id: str,
    workers: int,
    timeout_seconds: float,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    converted_by_source = {str(item.get("source_id")): item for item in conversions if item.get("source_id") and item.get("status") != "failed"}
    results: list[dict[str, Any]] = []

    async def worker(index: int) -> None:
        while True:
            payload = await _process_foundation_job(
                source_store=source_store,
                workflow_store=workflow_store,
                mind=mind,
                converted_by_source=converted_by_source,
                run_id=run_id,
                worker_id=f"20pdf-product-maturity-{index}",
                timeout_seconds=timeout_seconds,
                events=events,
            )
            if payload is None:
                return
            results.append(payload)

    await asyncio.gather(*(worker(index + 1) for index in range(max(1, workers))))
    return results


def _notes_for_run(vault_path: Path, *, run_id: str, source_ids: list[str], directories: tuple[str, ...]) -> list[Path]:
    vault = vault_path.expanduser().resolve()
    needles = [run_id, *source_ids]
    paths: list[Path] = []
    for directory in directories:
        root = vault / directory
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(needle and needle in text for needle in needles):
                paths.append(path)
    return sorted(set(paths))


def _note_count(vault_path: Path, *, run_id: str, source_ids: list[str], directory: str) -> int:
    return len(_notes_for_run(vault_path, run_id=run_id, source_ids=source_ids, directories=(directory,)))


def _source_reconciliation(
    *,
    source_ids: list[str],
    source_records: dict[str, Any],
    source_jobs: dict[str, Any],
    conversions: list[dict[str, Any]],
    foundation_results: list[dict[str, Any]],
    vault_path: Path,
    run_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    conversion_ids = {str(item.get("source_id")) for item in conversions}
    foundation_ids = {str(item.get("source_id")) for item in foundation_results}
    db_ids = {str(item.get("source_id")) for item in source_records.get("sources", [])}
    job_ids = {str(item.get("source_id")) for item in source_jobs.get("jobs", [])}
    vault_hits = {
        source_id: [
            str(path.relative_to(vault_path))
            for path in _notes_for_run(vault_path, run_id=run_id, source_ids=[source_id], directories=("00-Chaos", *FOUNDATION_STAGE_DIRS, "08-Evaluations", "09-Business-Plans", "10-Dossiers"))
        ]
        for source_id in source_ids
    }
    event_ids = {str(event.get("source_id")) for event in events if event.get("source_id")}
    missing = {
        "db": sorted(set(source_ids) - db_ids),
        "jobs": sorted(set(source_ids) - job_ids),
        "conversion": sorted(set(source_ids) - conversion_ids),
        "foundation": sorted(set(source_ids) - foundation_ids),
        "vault": sorted(source_id for source_id, hits in vault_hits.items() if not hits),
        "events": sorted(set(source_ids) - event_ids),
    }
    return {
        "source_ids": source_ids,
        "matched_all": not any(missing.values()),
        "missing": missing,
        "vault_hits": vault_hits,
    }


async def _run_impl() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    selected_pdfs, excluded_names = _select_pdfs(args)
    run_id = args.run_id or make_run_id("20pdf_product_maturity")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    vault_path = args.vault_path.expanduser().resolve()
    graph_db_path = runtime_dir / "graph.db"
    llm_trace_path = runtime_dir / "llm-calls.jsonl"
    source_contexts = {
        str(path): {
            "role": "20pdf_product_maturity_internal_source",
            "selection_reason": "deterministic new-PDF selection; prior 10-PDF and failed 20-PDF sets excluded",
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in selected_pdfs
    }
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="20pdf_product_maturity",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=selected_pdfs,
        source_selector=f"deterministic_random_seed_{args.seed}_new_20pdf_excluding_prior_runs",
        source_seed=str(args.seed),
        source_contexts=source_contexts,
        mocked=bool(args.dry_run),
        real_files=True,
        real_graph_db=not args.dry_run,
        real_vault=True,
        real_llm=not args.dry_run,
        real_chat=not args.dry_run,
        real_workflow=not args.dry_run,
        claimed_components=["files", "vault"] if args.dry_run else ["files", "graph_db", "vault", "llm", "chat", "workflow"],
        command=sys.argv,
    )
    evidence.capture_before()
    preflight = _preflight(vault_path, runtime_dir, manage_backend=bool(args.manage_backend and not args.dry_run), repo_root=repo_root)
    evidence.write_json("preflight.json", preflight)
    evidence.write_json(
        "selected-pdfs.json",
        {
            "seed": args.seed,
            "excluded_pdf_names": sorted(excluded_names),
            "pdfs": source_contexts,
            "selected_pdf_count": len(selected_pdfs),
        },
    )
    if args.dry_run:
        manifest = evidence.finalize(
            exit_code=0,
            result={"dry_run": True, "selected_pdf_count": len(selected_pdfs), "preflight": preflight},
            failures=[],
            repo_root=repo_root,
        )
        print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
        return 0

    source_store = SourceStore(runtime_dir / "source_store.sqlite", runtime_dir / "objects", runtime_dir / "canonical_markdown")
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    chat_store = ChatStore(runtime_dir / "chat.sqlite")
    research_store = ResearchStore(runtime_dir / "research.sqlite")
    business_plan_store = BusinessPlanStore(runtime_dir / "business_plans.sqlite")
    graph_db = GraphDB(graph_db_path)
    failures: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    result_payload: dict[str, Any] = {}
    start_time = time.monotonic()
    timeout_hit = False

    await source_store.initialize()
    await workflow_store.initialize()
    await chat_store.initialize()
    await research_store.initialize()
    await business_plan_store.initialize()
    await graph_db.initialize()
    try:
        SETTINGS.llm_trace_log_path = llm_trace_path
        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        writer = DikiwiObsidianWriter(vault_path=vault_path, folder_prefix="", zettelkasten_only=True)
        mind = DikiwiMind(
            llm_client=llm_resolver("dikiwi"),
            llm_client_resolver=llm_resolver,
            graph_db=graph_db,
            enabled=SETTINGS.minds.dikiwi_enabled,
            dikiwi_obsidian_writer=writer,
        )
        import aily.sessions.dikiwi_mind as dikiwi_mind_module

        dikiwi_mind_module.emit_ui_event = lambda event_type, **payload: _emit(events, event_type, **payload)

        admissions = await _admit_sources(selected_pdfs=selected_pdfs, run_id=run_id, source_store=source_store)
        source_ids = [str(item["source"]["source_id"]) for item in admissions]
        evidence.write_json("source-admission.json", {"admissions": admissions, "source_ids": source_ids})
        if len(admissions) != len(selected_pdfs):
            failures.append({"check": "source_admitted_count", "expected": len(selected_pdfs), "actual": len(admissions)})

        conversions = await asyncio.wait_for(
            _convert_sources(
                admissions=admissions,
                source_store=source_store,
                vault_path=vault_path,
                run_id=run_id,
                workers=args.conversion_workers,
            ),
            timeout=max(60.0, args.timeout_seconds - (time.monotonic() - start_time)),
        )
        evidence.write_json("source-conversion.json", {"conversions": conversions})
        chaos_paths = [Path(str(item.get("chaos_markdown_vault_artifact", {}).get("path"))) for item in conversions if item.get("chaos_markdown_vault_artifact")]
        if len([path for path in chaos_paths if path.is_file()]) != len(selected_pdfs):
            failures.append({"check": "chaos_markdown_count", "expected": len(selected_pdfs), "actual": len([path for path in chaos_paths if path.is_file()])})

        remaining = args.timeout_seconds - (time.monotonic() - start_time)
        if remaining <= 0:
            raise asyncio.TimeoutError("20-PDF maturity timeout hit before DIKIWI foundation processing")
        foundations = await asyncio.wait_for(
            _process_foundations(
                source_store=source_store,
                workflow_store=workflow_store,
                mind=mind,
                conversions=conversions,
                run_id=run_id,
                workers=args.foundation_workers,
                timeout_seconds=args.per_source_timeout_seconds,
                events=events,
            ),
            timeout=remaining,
        )
        completed_source_ids = [str(item.get("source_id")) for item in foundations if item.get("status") == "completed"]
        failed_sources = [item for item in foundations if item.get("status") != "completed"]
        evidence.write_json("foundation-results.json", {"source_ids": source_ids, "completed_source_ids": completed_source_ids, "foundations": foundations})
        if failed_sources:
            failures.append({"check": "source_foundation_failures", "failed_sources": failed_sources})

        business_payload: dict[str, Any] = {}
        dossier_record: dict[str, Any] = {}
        if completed_source_ids:
            business_payload = await _run_business_graph(
                runtime_dir=runtime_dir,
                vault_path=vault_path,
                primary_source_id=completed_source_ids[0],
                all_source_ids=completed_source_ids,
                primary_pdf_path=selected_pdfs[0],
                second_opinion_pdf_path=selected_pdfs[-1],
                source_store=source_store,
                research_store=research_store,
                business_plan_store=business_plan_store,
                graph_db=graph_db,
                chat_store=chat_store,
                workflow_store=workflow_store,
                mind=mind,
                events=events,
                research_model=args.research_model,
                max_results=args.max_results,
            )
            research_jobs_for_dossier = await research_store.list_research_jobs(limit=50)
            business_plan = business_payload["captured_business_plan_dependency"].get("business_plan") or {}
            topic = str(business_plan.get("title") or "20-PDF Product Maturity Business Plan")
            dossier = DossierService().build_and_write(
                DossierBuildRequest(
                    topic=topic,
                    vault_path=vault_path,
                    query_terms=[run_id, *completed_source_ids[:8]],
                    seed_claims=[
                        str((business_plan.get("payload") or {}).get("executive_summary") or ""),
                        str((business_plan.get("payload") or {}).get("recommendation") or ""),
                    ],
                    tavily_research_jobs=research_jobs_for_dossier,
                    max_vault_evidence=60,
                    max_tavily_evidence=20,
                )
            )
            dossier_record = dossier.to_dict()
        else:
            failures.append({"check": "no_completed_sources_for_value_path"})

        source_records = await source_store.list_sources(limit=100)
        source_jobs = await source_store.list_source_jobs(limit=100)
        workflow_runs = await workflow_store.list_runs(limit=200)
        research_jobs = await research_store.list_research_jobs(limit=50)
        team_evaluations = await business_plan_store.list_team_evaluations(limit=50)
        business_plans = await business_plan_store.list_business_plans(limit=20)
        business_plan_record = business_payload.get("captured_business_plan_dependency", {}).get("business_plan") or (business_plans[0] if business_plans else {})
        traffic_monitor = build_traffic_monitor([llm_trace_path], run_id=run_id, scenario="20pdf_product_maturity")
        chaos_quality = score_chaos_vault(vault_path, paths=chaos_paths)
        generated_paths = _notes_for_run(
            vault_path,
            run_id=run_id,
            source_ids=source_ids,
            directories=("00-Chaos", *FOUNDATION_STAGE_DIRS, "04-Insight", "05-Wisdom", "06-Impact", "08-Evaluations", "09-Business-Plans", "10-Dossiers"),
        )
        obsidian_quality = score_vault_output(
            vault_path,
            generated_paths=generated_paths,
            thresholds=QualityThresholds(
                overall_score=80.0,
                dimension_floor=70.0,
                source_clarity=70.0,
                content_substance=70.0,
                report_substance=70.0,
                note_pass_rate=0.80,
                high_value_note_floor=75.0,
                max_unresolved_link_count=10,
            ),
        )
        source_reconciliation = _source_reconciliation(
            source_ids=source_ids,
            source_records=source_records,
            source_jobs=source_jobs,
            conversions=conversions,
            foundation_results=foundations,
            vault_path=vault_path,
            run_id=run_id,
            events=events,
        )
        elapsed = round(time.monotonic() - start_time, 3)
        result_payload = {
            "selected_pdf_count": len(selected_pdfs),
            "source_admitted_count": len(admissions),
            "source_completed_count": len(completed_source_ids),
            "source_failed_count": len(source_ids) - len(completed_source_ids),
            "chaos_markdown_count": len([path for path in chaos_paths if path.is_file()]),
            "data_note_count": _note_count(vault_path, run_id=run_id, source_ids=source_ids, directory="01-Data"),
            "information_note_count": _note_count(vault_path, run_id=run_id, source_ids=source_ids, directory="02-Information"),
            "knowledge_note_count": _note_count(vault_path, run_id=run_id, source_ids=source_ids, directory="03-Knowledge"),
            "research_job_count": len(research_jobs),
            "team_evaluation_count": len(team_evaluations),
            "business_plan_count": len(business_plans),
            "dossier_count": 1 if dossier_record.get("output_path") else 0,
            "elapsed_seconds": elapsed,
            "timeout_hit": timeout_hit,
            "provider_usage": traffic_monitor.get("provider_totals", {}),
            "failed_sources": failed_sources,
            "source_ids": source_ids,
            "completed_source_ids": completed_source_ids,
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "workflow_store_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "research_store_db_path": str(runtime_dir / "research.sqlite"),
            "business_plan_store_db_path": str(runtime_dir / "business_plans.sqlite"),
            "graph_db_path": str(graph_db_path),
            "source_reconciliation": source_reconciliation,
            "vault_counts": vault_counts(vault_path),
        }
        if result_payload["source_admitted_count"] < len(selected_pdfs):
            failures.append({"check": "source_admitted_count_gate", "result": result_payload})
        if result_payload["chaos_markdown_count"] < len(selected_pdfs):
            failures.append({"check": "chaos_markdown_count_gate", "result": result_payload})
        running_jobs = [job for job in source_jobs.get("jobs", []) if job.get("status") == "running"]
        if running_jobs:
            failures.append({"check": "no_running_source_jobs", "running_jobs": running_jobs})
        if not research_jobs or not any(job.get("status") == "completed" for job in research_jobs):
            failures.append({"check": "tavily_research_completed", "research_jobs": research_jobs})
        if len(team_evaluations) < 3:
            failures.append({"check": "team_evaluations_completed", "team_evaluations": team_evaluations})
        if not business_plan_record:
            failures.append({"check": "business_plan_generated"})
        if not dossier_record.get("output_path"):
            failures.append({"check": "dossier_generated"})
        if not chaos_quality.get("passed"):
            failures.append({"check": "chaos_quality", "quality": chaos_quality})
        if not obsidian_quality.get("passed"):
            failures.append({"check": "obsidian_quality", "quality": obsidian_quality})
        if not source_reconciliation.get("matched_all"):
            failures.append({"check": "source_reconciliation", "reconciliation": source_reconciliation})

        evidence.write_json("source-processing-summary.json", {"sources": source_records, "jobs": source_jobs, "summary": result_payload})
        evidence.write_json("workflow-runs.json", [_run_payload(run) for run in workflow_runs])
        evidence.write_json("llm-traffic.json", traffic_monitor)
        evidence.write_json("research-jobs.json", {"records": research_jobs, "secret_scan": [_sanitize_packet(job.get("packet") or {}) for job in research_jobs]})
        evidence.write_json("team-evaluations.json", {"records": team_evaluations, "store": _sqlite_summary(runtime_dir / "business_plans.sqlite")})
        evidence.write_json("business-plan-record.json", business_plan_record)
        evidence.write_json("dossier-record.json", dossier_record)
        evidence.write_json("chaos-quality.json", chaos_quality)
        evidence.write_json("obsidian-quality.json", obsidian_quality)
    except asyncio.TimeoutError as exc:
        timeout_hit = True
        failures.append({"check": "runner_timeout", "error": str(exc), "timeout_seconds": args.timeout_seconds})
        with suppress(Exception):
            await source_store.cancel_running_source_jobs()
        result_payload = {"timeout_hit": True, "elapsed_seconds": round(time.monotonic() - start_time, 3)}
    except Exception as exc:
        failures.append({"check": "runner_exception", "error": str(exc)})
        result_payload = {"error": str(exc), "elapsed_seconds": round(time.monotonic() - start_time, 3)}
    finally:
        await graph_db.close()
        await business_plan_store.close()
        await research_store.close()
        await chat_store.close()
        await workflow_store.close()
        await source_store.close()

    manifest = evidence.finalize(
        exit_code=0 if not failures else 1,
        result=result_payload,
        failures=failures,
        ui_events=events,
        llm_log_file=str(llm_trace_path),
        stderr_text="" if not failures else json.dumps(failures, ensure_ascii=False),
        repo_root=repo_root,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_impl()))
