"""LLM traffic-monitor summaries for evidence runs."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(r"tvly-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"api[_-]?key['\"]?\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(r"authorization['\"]?\s*[:=]", re.IGNORECASE),
)

EXPECTED_PROVIDERS_BY_WORKLOAD = {
    "dikiwi.data": "kimi",
    "dikiwi.information": "kimi",
    "dikiwi.knowledge": "deepseek",
    "dikiwi.insight": "deepseek",
    "dikiwi.wisdom": "deepseek",
    "dikiwi.impact": "deepseek",
    "reactor": "deepseek",
    "entrepreneur": "deepseek",
    "guru": "deepseek",
    "gstack": "deepseek",
    "copilot.chat": "deepseek",
    "copilot.dossier": "deepseek",
}


@dataclass(frozen=True)
class TrafficMonitorThresholds:
    require_deepseek: bool = True
    require_kimi: bool = True
    require_success_tokens: bool = True
    require_provider_response_id: bool = True


def build_traffic_monitor(
    trace_paths: list[Path],
    *,
    run_id: str = "",
    scenario: str = "",
    thresholds: TrafficMonitorThresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or TrafficMonitorThresholds()
    records_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parse_failures: list[dict[str, Any]] = []
    secret_hits: list[dict[str, Any]] = []

    for trace_path in sorted(path.expanduser().resolve() for path in trace_paths):
        logical_run = _logical_run_id(trace_path)
        for line_number, line in enumerate(trace_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    secret_hits.append({"trace_path": str(trace_path), "line_number": line_number, "pattern": pattern.pattern})
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                parse_failures.append({"trace_path": str(trace_path), "line_number": line_number, "error": str(exc)})
                continue
            if "_origin" in record:
                continue
            if "provider" not in record and "workload" not in record:
                continue
            record["_trace_path"] = str(trace_path)
            record["_line_number"] = line_number
            record["_logical_run_id"] = logical_run
            records_by_run[logical_run].append(record)

    records = [record for run_records in records_by_run.values() for record in run_records]
    provider_totals = _provider_totals(records)
    workload_routes = _workload_routes(records)
    coverage = _coverage(records_by_run)
    failures = _traffic_failures(
        records=records,
        parse_failures=parse_failures,
        secret_hits=secret_hits,
        coverage=coverage,
        thresholds=thresholds,
    )
    return {
        "_origin": {
            "creator": "aily.verify.llm_traffic",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "generation_method": "aggregate sanitized llm-calls.jsonl provider receipts",
            "evidence_class": "quality_gate",
            "modified_by_lead_agent": False,
        },
        "run_id": run_id,
        "scenario": scenario,
        "trace_paths": [str(path.expanduser().resolve()) for path in trace_paths],
        "expected_routes": EXPECTED_PROVIDERS_BY_WORKLOAD,
        "record_count": len(records),
        "success_count": sum(1 for record in records if record.get("success") is True),
        "failure_count": sum(1 for record in records if record.get("success") is not True),
        "provider_totals": provider_totals,
        "observed_routes": workload_routes,
        "coverage": coverage,
        "failed_records": [_sanitize_failure(record) for record in records if record.get("success") is not True],
        "parse_failures": parse_failures,
        "redaction_report": {
            "secret_hit_count": len(secret_hits),
            "secret_hits": secret_hits[:20],
            "raw_prompt_or_completion_fields_present": _raw_payload_field_hits(records),
            "authorization_header_present": any("authorization" in json.dumps(record, ensure_ascii=False).lower() for record in records),
        },
        "passed": not failures,
        "failures": failures,
    }


def _logical_run_id(trace_path: Path) -> str:
    parts = trace_path.parts
    if "runs" in parts:
        index = len(parts) - 1 - list(reversed(parts)).index("runs")
        if index + 1 < len(parts):
            return parts[index + 1]
    if trace_path.parent.name == "runtime":
        return trace_path.parent.parent.name
    return trace_path.parent.name


def _provider_totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("provider") or "unknown")].append(record)
    result: dict[str, Any] = {}
    for provider, items in sorted(grouped.items()):
        durations = [float(item.get("duration_ms") or 0.0) for item in items]
        status_codes = Counter(str(item.get("status_code") or "") for item in items)
        usage = [_usage(item) for item in items]
        result[provider] = {
            "calls": len(items),
            "successes": sum(1 for item in items if item.get("success") is True),
            "failures": sum(1 for item in items if item.get("success") is not True),
            "duration_ms_total": round(sum(durations), 2),
            "duration_ms_p50": round(median(durations), 2) if durations else 0.0,
            "duration_ms_p95": round(sorted(durations)[max(0, int(len(durations) * 0.95) - 1)], 2) if durations else 0.0,
            "status_code_counts": dict(sorted(status_codes.items())),
            "token_totals": {
                "prompt_tokens": sum(item["prompt_tokens"] for item in usage),
                "completion_tokens": sum(item["completion_tokens"] for item in usage),
                "total_tokens": sum(item["total_tokens"] for item in usage),
            },
            "models": sorted({str(item.get("model") or "") for item in items if item.get("model")}),
            "base_urls": sorted({str(item.get("base_url") or "") for item in items if item.get("base_url")}),
            "provider_response_ids": sorted({str(item.get("provider_response_id") or "") for item in items if item.get("provider_response_id")})[:50],
        }
    return result


def _workload_routes(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = str(record.get("workload") or record.get("stage") or "unknown").lower()
        grouped[key].append(record)
    for workload, items in sorted(grouped.items()):
        result[workload] = {
            "calls": len(items),
            "providers": dict(sorted(Counter(str(item.get("provider") or "unknown") for item in items).items())),
            "models": sorted({str(item.get("model") or "") for item in items if item.get("model")}),
            "successes": sum(1 for item in items if item.get("success") is True),
            "failures": sum(1 for item in items if item.get("success") is not True),
        }
    return result


def _coverage(records_by_run: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    runs: dict[str, Any] = {}
    missing_kimi: list[str] = []
    missing_deepseek: list[str] = []
    route_mismatches: list[dict[str, str]] = []
    for run_id, records in sorted(records_by_run.items()):
        successful = [record for record in records if record.get("success") is True]
        providers = sorted({str(record.get("provider") or "") for record in successful if record.get("provider")})
        stages = sorted({str(record.get("stage") or "") for record in successful if record.get("stage")})
        workloads = sorted({str(record.get("workload") or "") for record in successful if record.get("workload")})
        if "kimi" not in providers:
            missing_kimi.append(run_id)
        if "deepseek" not in providers:
            missing_deepseek.append(run_id)
        for record in successful:
            workload = str(record.get("workload") or "").lower()
            expected = EXPECTED_PROVIDERS_BY_WORKLOAD.get(workload)
            observed = str(record.get("provider") or "")
            if expected and expected != observed:
                route_mismatches.append({"run_id": run_id, "workload": workload, "expected": expected, "observed": observed})
        runs[run_id] = {
            "record_count": len(records),
            "success_count": len(successful),
            "providers": providers,
            "stages": stages,
            "workloads": workloads,
        }
    return {
        "runs": runs,
        "runs_missing_kimi": missing_kimi,
        "runs_missing_deepseek": missing_deepseek,
        "route_mismatches": route_mismatches,
    }


def _traffic_failures(
    *,
    records: list[dict[str, Any]],
    parse_failures: list[dict[str, Any]],
    secret_hits: list[dict[str, Any]],
    coverage: dict[str, Any],
    thresholds: TrafficMonitorThresholds,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if not records:
        failures.append({"check": "llm_records_present", "message": "No LLM trace records were found."})
    if parse_failures:
        failures.append({"check": "jsonl_parse", "count": len(parse_failures)})
    if secret_hits:
        failures.append({"check": "redaction", "count": len(secret_hits)})
    if coverage.get("route_mismatches"):
        failures.append({"check": "provider_route_mismatch", "mismatches": coverage["route_mismatches"][:20]})
    if thresholds.require_kimi and coverage.get("runs_missing_kimi"):
        failures.append({"check": "per_run_kimi_coverage", "runs": coverage["runs_missing_kimi"]})
    if thresholds.require_deepseek and coverage.get("runs_missing_deepseek"):
        failures.append({"check": "per_run_deepseek_coverage", "runs": coverage["runs_missing_deepseek"]})
    for record in records:
        if record.get("success") is not True:
            continue
        if record.get("status_code") != 200:
            failures.append({"check": "success_status_code", "line": record.get("_line_number"), "status_code": record.get("status_code")})
        if thresholds.require_provider_response_id and not str(record.get("provider_response_id") or ""):
            failures.append({"check": "provider_response_id_present", "line": record.get("_line_number"), "provider": record.get("provider")})
        if thresholds.require_success_tokens and _usage(record)["total_tokens"] <= 0:
            failures.append({"check": "success_token_usage", "line": record.get("_line_number"), "provider": record.get("provider")})
    return failures[:100]


def _usage(record: dict[str, Any]) -> dict[str, int]:
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _sanitize_failure(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": record.get("timestamp", ""),
        "provider": record.get("provider", ""),
        "base_url": record.get("base_url", ""),
        "model": record.get("model", ""),
        "workload": record.get("workload", ""),
        "stage": record.get("stage", ""),
        "status_code": record.get("status_code", ""),
        "error_type": record.get("error_type", ""),
        "error": str(record.get("error", ""))[:500],
        "duration_ms": record.get("duration_ms", 0),
        "local_request_id": record.get("local_request_id", ""),
        "line_number": record.get("_line_number", 0),
        "logical_run_id": record.get("_logical_run_id", ""),
    }


def _raw_payload_field_hits(records: list[dict[str, Any]]) -> list[str]:
    forbidden = {"prompt", "completion", "messages", "request", "response", "source_text", "content"}
    hits: set[str] = set()
    for record in records:
        for key in record:
            if str(key).lower() in forbidden:
                hits.add(str(key))
    return sorted(hits)
