"""Verification for evidence-bound dossier drafts."""

from __future__ import annotations

from typing import Any

from aily.dossier.models import DossierDraft, DossierVerification


def verify_dossier(draft: DossierDraft) -> DossierVerification:
    evidence_ids = {evidence.evidence_id for evidence in draft.evidence}
    claim_ids = {claim.claim_id for claim in draft.claims}
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    supported_claims = [claim for claim in draft.claims if claim.status == "supported"]
    covered_claims = 0
    for claim in draft.claims:
        missing_evidence = [evidence_id for evidence_id in claim.evidence_ids if evidence_id not in evidence_ids]
        if missing_evidence:
            failures.append(
                {
                    "check": "claim_evidence_resolves",
                    "claim_id": claim.claim_id,
                    "missing_evidence_ids": missing_evidence,
                }
            )
        if claim.status == "supported":
            if not claim.evidence_ids:
                failures.append({"check": "supported_claim_has_evidence", "claim_id": claim.claim_id})
            elif not missing_evidence:
                covered_claims += 1
        if claim.status == "hypothesis" and claim.evidence_ids:
            warnings.append(
                {
                    "check": "hypothesis_has_evidence",
                    "claim_id": claim.claim_id,
                    "message": "Hypothesis has evidence references but is not marked supported.",
                }
            )

    for section in draft.sections:
        missing_claims = [claim_id for claim_id in section.claim_ids if claim_id not in claim_ids]
        if missing_claims:
            failures.append(
                {
                    "check": "section_claims_resolve",
                    "section": section.title,
                    "missing_claim_ids": missing_claims,
                }
            )
        if not section.claim_ids:
            warnings.append({"check": "section_has_claims", "section": section.title})

    coverage = covered_claims / max(len(supported_claims), 1)
    if supported_claims and coverage < 1.0:
        failures.append(
            {
                "check": "supported_claim_coverage",
                "actual": round(coverage, 3),
                "minimum": 1.0,
            }
        )
    if not draft.evidence:
        failures.append({"check": "evidence_present", "message": "Dossier has no evidence records."})
    if not draft.claims:
        failures.append({"check": "claims_present", "message": "Dossier has no claim ledger."})

    return DossierVerification(
        passed=not failures,
        claim_coverage=round(coverage, 3),
        failures=failures,
        warnings=warnings,
    )
