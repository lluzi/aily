"""Evidence collection for dossier generation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aily.dossier.models import DossierEvidence, EvidenceType, utc_now


VAULT_SOURCE_TYPES: dict[str, EvidenceType] = {
    "00-Chaos": "vault_source",
    "01-Data": "vault_data",
    "02-Information": "vault_information",
    "03-Knowledge": "vault_knowledge",
    "04-Insight": "vault_insight",
    "05-Wisdom": "vault_wisdom",
    "06-Impact": "vault_impact",
    "08-Evaluations": "vault_evaluation",
    "09-Business-Plans": "vault_business_plan",
}

HIDDEN_VAULT_PREFIXES = (
    "00-Chaos/_assets/",
    "00-Chaos/canonical-markdown/",
    "99-MOC/",
    "99-System/",
)


class VaultEvidenceCollector:
    """Collect evidence cards from human-facing Obsidian vault notes."""

    def collect(
        self,
        *,
        vault_path: Path,
        query_terms: list[str],
        limit: int = 40,
    ) -> list[DossierEvidence]:
        vault = vault_path.expanduser().resolve()
        terms = [_normalize(term) for term in query_terms if _normalize(term)]
        candidates: list[tuple[float, Path, str, str]] = []
        for path in sorted(vault.rglob("*.md")):
            if not _is_visible_vault_note(path, vault):
                continue
            rel = path.relative_to(vault).as_posix()
            top_dir = rel.split("/", 1)[0]
            if top_dir not in VAULT_SOURCE_TYPES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            frontmatter, body = _split_frontmatter(text)
            score = _relevance_score(rel, body, terms)
            if terms and score <= 0:
                continue
            candidates.append((score, path, frontmatter, body))

        if terms:
            candidates.sort(key=lambda item: (item[0], item[1].as_posix()), reverse=True)
        else:
            candidates.sort(key=lambda item: item[1].as_posix())

        evidence: list[DossierEvidence] = []
        for index, (_, path, frontmatter, body) in enumerate(candidates[: max(1, limit)], 1):
            rel = path.relative_to(vault).as_posix()
            top_dir = rel.split("/", 1)[0]
            title = _first_h1(body) or path.stem.replace("_", " ")
            excerpt = _extract_snippet(body, terms)
            evidence.append(
                DossierEvidence(
                    evidence_id=f"V{index:03d}",
                    source_type=VAULT_SOURCE_TYPES[top_dir],
                    source_path=rel,
                    source_title=title,
                    claim=_clean_claim(title),
                    excerpt=excerpt,
                    confidence=_vault_confidence(top_dir),
                    allowed_use=_vault_allowed_use(top_dir),
                    metadata={
                        "frontmatter_keys": _frontmatter_keys(frontmatter),
                        "top_dir": top_dir,
                    },
                )
            )
        return evidence


def tavily_jobs_to_evidence(
    research_jobs: list[dict[str, Any]],
    *,
    start_index: int = 1,
    limit: int = 20,
) -> list[DossierEvidence]:
    """Normalize stored Tavily research packets into dossier evidence cards."""
    evidence: list[DossierEvidence] = []
    for job in research_jobs:
        packet = job.get("packet") or {}
        if not isinstance(packet, dict) or packet.get("provider") != "tavily":
            continue
        query = str(packet.get("query") or job.get("query") or "")
        timestamp = str(packet.get("freshness") or job.get("completed_at") or job.get("updated_at") or "")
        sources = list(packet.get("sources") or [])
        claims = list(packet.get("claims") or [])
        for claim_index, claim in enumerate(claims):
            if len(evidence) >= limit:
                return evidence
            source = sources[min(claim_index, len(sources) - 1)] if sources else {}
            text = str(claim.get("claim") if isinstance(claim, dict) else claim).strip()
            if not text:
                continue
            evidence.append(
                DossierEvidence(
                    evidence_id=f"T{start_index + len(evidence):03d}",
                    source_type="tavily_external",
                    claim=_clean_claim(text),
                    excerpt=text[:900],
                    confidence="external_unverified_until_reconciled",
                    allowed_use="external_context_requires_reconciliation",
                    source_url=str((claim if isinstance(claim, dict) else {}).get("source_url") or source.get("url") or ""),
                    source_title=str((claim if isinstance(claim, dict) else {}).get("source_title") or source.get("title") or "Tavily result"),
                    source_query=query,
                    source_timestamp=timestamp or utc_now(),
                    metadata={
                        "research_id": str(packet.get("research_id") or job.get("research_id") or ""),
                        "score": source.get("score"),
                    },
                )
            )
    return evidence


def _is_visible_vault_note(path: Path, vault: Path) -> bool:
    try:
        rel = path.expanduser().resolve().relative_to(vault).as_posix()
    except ValueError:
        return False
    if rel == "00-Chaos/00 Zettelkasten Index.md":
        return False
    return not rel.startswith(HIDDEN_VAULT_PREFIXES)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    return text[4:end].strip(), text[end + 4 :].lstrip()


def _frontmatter_keys(frontmatter: str) -> list[str]:
    keys: list[str] = []
    for line in frontmatter.splitlines():
        if ":" in line and not line.startswith(" "):
            keys.append(line.split(":", 1)[0].strip())
    return keys


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace("_", " ").strip().lower())


def _relevance_score(rel: str, body: str, terms: list[str]) -> float:
    haystack = _normalize(f"{rel}\n{body}")
    if not terms:
        return 1.0
    score = 0.0
    for term in terms:
        if term in haystack:
            score += 5.0 + min(10, haystack.count(term))
        else:
            words = [word for word in term.split() if len(word) >= 4]
            score += sum(1.0 for word in words if word in haystack)
    return score


def _extract_snippet(body: str, terms: list[str], limit: int = 900) -> str:
    paragraphs = _readable_paragraphs(body)
    if not paragraphs:
        return ""
    normalized_terms = [term for term in terms if term]
    selected = ""
    for paragraph in paragraphs:
        haystack = _normalize(paragraph)
        if any(term in haystack for term in normalized_terms):
            selected = paragraph
            break
    if not selected:
        selected = paragraphs[0]
    selected = re.sub(r"\s+", " ", selected).strip()
    if len(selected) <= limit:
        return selected
    return selected[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "."


def _readable_paragraphs(body: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    in_code = False
    in_details = False

    def flush() -> None:
        if not current:
            return
        paragraph = _clean_paragraph(" ".join(current))
        current.clear()
        if _is_useful_paragraph(paragraph):
            paragraphs.append(paragraph)

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            flush()
            continue
        if line.startswith("<details"):
            in_details = True
            flush()
            continue
        if line.startswith("</details"):
            in_details = False
            flush()
            continue
        if in_code or in_details:
            continue
        if not line:
            flush()
            continue
        if line.startswith("#"):
            flush()
            continue
        current.append(line)
    flush()
    return paragraphs


def _clean_paragraph(value: str) -> str:
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"\s+", " ", text.replace("|", " ")).strip()
    return text


def _is_useful_paragraph(paragraph: str) -> bool:
    if len(paragraph.split()) < 10:
        return False
    lower = paragraph.lower()
    generic_starts = (
        "this note should be read",
        "this information note turns",
        "source:",
        "canonical source:",
        "source artifact:",
        "domain:",
        "type:",
        "confidence:",
        "from data:",
    )
    return not lower.startswith(generic_starts)


def _clean_claim(value: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" #-")
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "."


def _vault_confidence(top_dir: str) -> str:
    if top_dir == "00-Chaos":
        return "source_record"
    if top_dir in {"01-Data", "02-Information"}:
        return "vault_extraction"
    if top_dir in {"03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"}:
        return "graph_inference_requires_source_trace"
    return "product_judgment_requires_reconciliation"


def _vault_allowed_use(top_dir: str) -> str:
    uses = {
        "00-Chaos": "source_background",
        "01-Data": "factual_extraction",
        "02-Information": "technical_background",
        "03-Knowledge": "relationship_context",
        "04-Insight": "interpretive_context",
        "05-Wisdom": "principle_or_pattern",
        "06-Impact": "product_hypothesis_or_action",
        "08-Evaluations": "expert_panel_judgment",
        "09-Business-Plans": "business_plan_context",
    }
    return uses.get(top_dir, "context")
