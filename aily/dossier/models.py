"""Data models for evidence-bound dossiers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


ClaimStatus = Literal["supported", "hypothesis", "rejected"]
ClaimType = Literal[
    "source_fact",
    "vault_extraction",
    "graph_inference",
    "product_judgment",
    "external_fact",
    "hypothesis",
]
EvidenceType = Literal[
    "vault_source",
    "vault_data",
    "vault_information",
    "vault_knowledge",
    "vault_insight",
    "vault_wisdom",
    "vault_impact",
    "vault_evaluation",
    "vault_business_plan",
    "tavily_external",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DossierEvidence:
    evidence_id: str
    source_type: EvidenceType
    claim: str
    excerpt: str
    confidence: str
    allowed_use: str
    source_path: str = ""
    source_url: str = ""
    source_title: str = ""
    source_query: str = ""
    source_timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DossierClaim:
    claim_id: str
    text: str
    claim_type: ClaimType
    status: ClaimStatus
    evidence_ids: list[str] = field(default_factory=list)
    confidence: str = "unknown"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DossierSection:
    title: str
    purpose: str
    claim_ids: list[str]
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DossierVerification:
    passed: bool
    claim_coverage: float
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DossierDraft:
    dossier_id: str
    topic: str
    title: str
    created_at: str
    evidence_policy: str
    evidence: list[DossierEvidence]
    claims: list[DossierClaim]
    sections: list[DossierSection]
    verification: DossierVerification | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verification"] = self.verification.to_dict() if self.verification else None
        return payload


@dataclass(frozen=True)
class DossierBuildRequest:
    topic: str
    vault_path: Path
    query_terms: list[str] = field(default_factory=list)
    seed_claims: list[str] = field(default_factory=list)
    tavily_research_jobs: list[dict[str, Any]] = field(default_factory=list)
    max_vault_evidence: int = 40
    max_tavily_evidence: int = 20
    include_hypotheses: bool = True


@dataclass(frozen=True)
class DossierBuildResult:
    draft: DossierDraft
    markdown: str
    output_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft": self.draft.to_dict(),
            "output_path": str(self.output_path) if self.output_path else "",
            "markdown": self.markdown,
        }
