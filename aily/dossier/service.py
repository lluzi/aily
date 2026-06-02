"""Dossier build orchestration."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from aily.dossier.evidence import VaultEvidenceCollector, tavily_jobs_to_evidence
from aily.dossier.models import (
    DossierBuildRequest,
    DossierBuildResult,
    DossierClaim,
    DossierDraft,
    DossierEvidence,
    DossierSection,
    utc_now,
)
from aily.dossier.verifier import verify_dossier
from aily.dossier.writer import dossier_output_path, render_dossier_markdown
from aily.writer.vault_layout import ensure_v1_vault_layout


EVIDENCE_POLICY = (
    "This dossier is evidence-bound. Substantive claims may only use Vault evidence "
    "or Tavily research evidence. Claims without sufficient evidence are labeled as "
    "hypotheses and must not be presented as facts."
)


class DossierService:
    """Build deterministic evidence-bound dossier drafts."""

    def __init__(
        self,
        *,
        vault_collector: VaultEvidenceCollector | None = None,
    ) -> None:
        self.vault_collector = vault_collector or VaultEvidenceCollector()

    def build(self, request: DossierBuildRequest) -> DossierBuildResult:
        evidence = self._collect_evidence(request)
        claims = self._build_claims(
            topic=request.topic,
            evidence=evidence,
            seed_claims=request.seed_claims,
            include_hypotheses=request.include_hypotheses,
        )
        draft = DossierDraft(
            dossier_id=_dossier_id(request.topic, evidence, claims),
            topic=request.topic,
            title=f"Deep Learning Dossier: {request.topic}",
            created_at=utc_now(),
            evidence_policy=EVIDENCE_POLICY,
            evidence=evidence,
            claims=claims,
            sections=_build_sections(claims),
            metadata={
                "query_terms": request.query_terms,
                "vault_path": str(request.vault_path.expanduser().resolve()),
                "vault_evidence_count": sum(1 for item in evidence if item.evidence_id.startswith("V")),
                "tavily_evidence_count": sum(1 for item in evidence if item.evidence_id.startswith("T")),
            },
        )
        verification = verify_dossier(draft)
        draft = DossierDraft(
            dossier_id=draft.dossier_id,
            topic=draft.topic,
            title=draft.title,
            created_at=draft.created_at,
            evidence_policy=draft.evidence_policy,
            evidence=draft.evidence,
            claims=draft.claims,
            sections=draft.sections,
            verification=verification,
            metadata=draft.metadata,
        )
        return DossierBuildResult(draft=draft, markdown=render_dossier_markdown(draft))

    def build_and_write(self, request: DossierBuildRequest, output_path: Path | None = None) -> DossierBuildResult:
        result = self.build(request)
        target = output_path
        if target is None:
            ensure_v1_vault_layout(request.vault_path)
            target = dossier_output_path(request.vault_path, result.draft.title, result.draft.dossier_id)
        target.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        target.expanduser().resolve().write_text(result.markdown, encoding="utf-8")
        return DossierBuildResult(draft=result.draft, markdown=result.markdown, output_path=target.expanduser().resolve())

    def _collect_evidence(self, request: DossierBuildRequest) -> list[DossierEvidence]:
        vault_evidence = self.vault_collector.collect(
            vault_path=request.vault_path,
            query_terms=request.query_terms or [request.topic],
            limit=request.max_vault_evidence,
        )
        tavily_evidence = tavily_jobs_to_evidence(
            request.tavily_research_jobs,
            start_index=1,
            limit=request.max_tavily_evidence,
        )
        return [*vault_evidence, *tavily_evidence]

    def _build_claims(
        self,
        *,
        topic: str,
        evidence: list[DossierEvidence],
        seed_claims: list[str],
        include_hypotheses: bool,
    ) -> list[DossierClaim]:
        claims: list[DossierClaim] = []
        for index, evidence_record in enumerate(evidence, 1):
            claims.append(
                DossierClaim(
                    claim_id=f"C{index:03d}",
                    text=evidence_record.claim,
                    claim_type=_claim_type_from_evidence(evidence_record),
                    status="supported",
                    evidence_ids=[evidence_record.evidence_id],
                    confidence=evidence_record.confidence,
                    rationale=f"Normalized directly from {evidence_record.evidence_id}.",
                )
            )

        next_id = len(claims) + 1
        for seed in seed_claims:
            text = _clean_seed_claim(seed)
            if not text:
                continue
            matches = _match_evidence(text, evidence)
            if matches:
                claims.append(
                    DossierClaim(
                        claim_id=f"C{next_id:03d}",
                        text=text,
                        claim_type="product_judgment",
                        status="supported",
                        evidence_ids=matches,
                        confidence="supported_by_matching_evidence",
                        rationale="Seed claim matched against the evidence pack by lexical overlap.",
                    )
                )
                next_id += 1
            elif include_hypotheses:
                claims.append(
                    DossierClaim(
                        claim_id=f"C{next_id:03d}",
                        text=text,
                        claim_type="hypothesis",
                        status="hypothesis",
                        evidence_ids=[],
                        confidence="unsupported",
                        rationale=f"No Vault or Tavily evidence was matched for this claim about {topic}.",
                    )
                )
                next_id += 1
        return claims


def _build_sections(claims: list[DossierClaim]) -> list[DossierSection]:
    supported = [claim.claim_id for claim in claims if claim.status == "supported"]
    hypotheses = [claim.claim_id for claim in claims if claim.status == "hypothesis"]
    source_claims = [
        claim.claim_id
        for claim in claims
        if claim.status == "supported" and claim.claim_type in {"source_fact", "vault_extraction", "external_fact"}
    ]
    inference_claims = [
        claim.claim_id
        for claim in claims
        if claim.status == "supported" and claim.claim_type in {"graph_inference", "product_judgment"}
    ]
    sections = [
        DossierSection(
            title="Grounded Context",
            purpose="Facts and extractions that can be traced to Vault source layers or Tavily search records.",
            claim_ids=source_claims or supported[:8],
        ),
        DossierSection(
            title="Reasoning Layer",
            purpose="Higher-level graph inferences and product judgments. These are useful, but they must remain distinguishable from source facts.",
            claim_ids=inference_claims[:12],
        ),
        DossierSection(
            title="Learning Prompts",
            purpose="Use these claims as retrieval prompts. Explain each claim in your own words and trace it back to the evidence IDs before applying it.",
            claim_ids=supported[:5],
        ),
    ]
    if hypotheses:
        sections.append(
            DossierSection(
                title="Hypotheses Not Yet Proven",
                purpose="These ideas may be useful, but they must not be treated as facts until supported by Vault or Tavily evidence.",
                claim_ids=hypotheses,
            )
        )
    return sections


def _claim_type_from_evidence(evidence: DossierEvidence) -> str:
    if evidence.source_type == "vault_source":
        return "source_fact"
    if evidence.source_type in {"vault_data", "vault_information"}:
        return "vault_extraction"
    if evidence.source_type in {"vault_knowledge", "vault_insight", "vault_wisdom", "vault_impact"}:
        return "graph_inference"
    if evidence.source_type in {"vault_evaluation", "vault_business_plan"}:
        return "product_judgment"
    if evidence.source_type == "tavily_external":
        return "external_fact"
    return "hypothesis"


def _match_evidence(claim: str, evidence: list[DossierEvidence], *, limit: int = 4) -> list[str]:
    claim_tokens = _tokens(claim)
    scored: list[tuple[int, str]] = []
    for item in evidence:
        item_tokens = _tokens(f"{item.claim} {item.excerpt} {item.source_title}")
        overlap = len(claim_tokens & item_tokens)
        if overlap >= 3:
            scored.append((overlap, item.evidence_id))
    scored.sort(reverse=True)
    return [evidence_id for _, evidence_id in scored[:limit]]


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}", value.lower())
        if token not in {"the", "and", "that", "this", "with", "from", "into", "must", "should"}
    }


def _clean_seed_claim(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -")
    if len(text.split()) < 5:
        return ""
    return text[:700]


def _dossier_id(topic: str, evidence: list[DossierEvidence], claims: list[DossierClaim]) -> str:
    digest = hashlib.sha256()
    digest.update(topic.encode("utf-8"))
    for item in evidence[:20]:
        digest.update(item.evidence_id.encode("utf-8"))
        digest.update(item.claim.encode("utf-8"))
    for claim in claims[:20]:
        digest.update(claim.text.encode("utf-8"))
    return f"dos_{digest.hexdigest()[:12]}"
