"""Markdown rendering for evidence-bound dossiers."""

from __future__ import annotations

from collections import defaultdict
import json
import re
from pathlib import Path

from aily.dossier.models import DossierClaim, DossierDraft, DossierEvidence


def render_dossier_markdown(draft: DossierDraft) -> str:
    claim_map = {claim.claim_id: claim for claim in draft.claims}
    evidence_map = {item.evidence_id: item for item in draft.evidence}
    supported_claims = [claim for claim in draft.claims if claim.status == "supported"]
    hypotheses = [claim for claim in draft.claims if claim.status == "hypothesis"]
    source_evidence = _evidence_by_role(draft.evidence, {"vault_source", "vault_data", "vault_information"})
    connector_evidence = _evidence_by_role(
        draft.evidence,
        {"vault_knowledge", "vault_insight", "vault_wisdom", "vault_impact"},
    )
    judgment_evidence = _evidence_by_role(draft.evidence, {"vault_evaluation", "vault_business_plan"})
    external_evidence = _evidence_by_role(draft.evidence, {"tavily_external"})

    lines: list[str] = [
        "---",
        "artifact_type: dossier",
        "origin_creator: application",
        "origin_generation_method: evidence-bound dossier renderer",
        "origin_modified_by_lead_agent: false",
        f"dossier_id: {json.dumps(draft.dossier_id)}",
        f"topic: {json.dumps(draft.topic)}",
        f"created_at: {json.dumps(draft.created_at)}",
        "---",
        "",
        f"# {draft.title}",
        "",
        "## Reader's Brief",
        "",
        _brief_intro(draft, supported_claims, hypotheses),
        "",
        "This dossier is written for learning first and audit second. The numbered evidence IDs are kept in brackets so every claim can be traced, but the main body avoids exposing verifier internals as prose.",
        "",
        "## What The Vault Is Saying",
        "",
        _source_narrative(draft.topic, source_evidence[:8]),
        "",
        "## How The Ideas Connect",
        "",
        _connector_narrative(draft.topic, connector_evidence[:8], evidence_map),
        "",
        "## Expert Reading",
        "",
        _expert_reading(draft.topic, source_evidence, connector_evidence, external_evidence),
        "",
        "## Source Knowledge Lineage",
        "",
        _source_lineage(source_evidence, connector_evidence),
        "",
        "## Panel And Business Judgment",
        "",
        _judgment_narrative(judgment_evidence),
        "",
        "## Learning Path",
        "",
        draft.evidence_policy,
        "",
        "Read this as a guided map: first understand the source facts, then follow the connector notes, then decide which business or engineering claim is actually justified.",
        "",
    ]

    learning_claims = _rank_claims_for_reading(supported_claims, evidence_map)[:8]
    for index, claim in enumerate(learning_claims, 1):
        lines.extend(_learning_claim_block(index, claim, evidence_map))

    if hypotheses:
        lines.extend(
            [
                "## Still Unproven",
                "",
                "These points may be useful as research prompts, but the dossier does not treat them as facts yet.",
                "",
            ]
        )
        for claim in hypotheses:
            lines.append(f"- {_clean_inline(claim.text)}")
        lines.append("")

    if external_evidence:
        lines.extend(["## External Research Context", ""])
        for evidence in external_evidence[:8]:
            lines.extend(_external_context_block(evidence))

    lines.extend(
        [
            "## Verification Summary",
            "",
            _verification_summary(draft),
            "",
            "## Audit Appendix",
            "",
            "The appendix is deliberately separated from the reading flow. Use it to verify source lineage, not as the main learning path.",
            "",
        ]
    )

    if draft.sections:
        lines.extend(["### Section Map", ""])
        for section in draft.sections:
            readable_claims = [
                _clean_inline(claim_map[claim_id].text)
                for claim_id in section.claim_ids[:5]
                if claim_id in claim_map
            ]
            if not readable_claims:
                continue
            lines.append(f"- **{section.title}**: {section.purpose}")
            lines.append(f"  Evidence-backed prompts: {'; '.join(readable_claims)}.")
        lines.append("")

    grouped_evidence: dict[str, list[DossierEvidence]] = defaultdict(list)
    for evidence in draft.evidence:
        grouped_evidence[evidence.source_type].append(evidence)

    for source_type, evidence_items in sorted(grouped_evidence.items()):
        lines.extend([f"### {_source_type_label(source_type)}", ""])
        for evidence in evidence_items:
            lines.extend(_evidence_block(evidence))

    return "\n".join(lines).rstrip() + "\n"


def dossier_output_path(vault_path: Path, title: str, dossier_id: str) -> Path:
    return vault_path.expanduser().resolve() / "10-Dossiers" / f"{_slug(title)}-{dossier_id}.md"


def _brief_intro(draft: DossierDraft, supported_claims: list[DossierClaim], hypotheses: list[DossierClaim]) -> str:
    vault_count = int(draft.metadata.get("vault_evidence_count") or 0)
    tavily_count = int(draft.metadata.get("tavily_evidence_count") or 0)
    return (
        f"This dossier studies **{_clean_inline(draft.topic)}** from {vault_count} Vault evidence records "
        f"and {tavily_count} Tavily research records. It contains {len(supported_claims)} supported claims"
        f"{f' and {len(hypotheses)} unresolved hypotheses' if hypotheses else ''}. "
        "The purpose is not to list snippets; it is to help a human reader understand what the evidence supports, "
        "how the concepts depend on one another, and where judgment is still required."
    )


def _source_narrative(topic: str, evidence_items: list[DossierEvidence]) -> str:
    if not evidence_items:
        return f"The vault did not provide enough source-level evidence to explain {_clean_inline(topic)} as a grounded topic."
    anchors = "\n".join(f"- {_evidence_sentence(item)}" for item in evidence_items[:5])
    return (
        f"The source layer frames **{_clean_inline(topic)}** as a concrete engineering workflow rather than a loose theme. "
        "The strongest source anchors are:\n\n"
        f"{anchors}\n\n"
        "Together, these records establish the vocabulary a reader needs before accepting any higher-level business or product interpretation."
    )


def _connector_narrative(
    topic: str,
    evidence_items: list[DossierEvidence],
    evidence_map: dict[str, DossierEvidence],
) -> str:
    if not evidence_items:
        return (
            "The dossier did not find enough connector-level notes to explain how the source facts form a knowledge graph. "
            "That should be treated as a quality warning for this topic."
        )
    connectors = "\n".join(f"- {_evidence_sentence(item)}" for item in evidence_items[:5])
    link_count = sum(1 for item in evidence_map.values() if "[[" in item.excerpt)
    link_note = (
        f"The evidence pack also includes {link_count} records with explicit wiki-link context. "
        if link_count
        else ""
    )
    return (
        f"The connector layer turns {_clean_inline(topic)} from isolated facts into a reasoning path. "
        f"{link_note}Key connector notes point to:\n\n{connectors}\n\n"
        "A useful reader should ask how each connector changes the decision: does it explain causality, dependency, validation, tradeoff, or action?"
    )


def _expert_reading(
    topic: str,
    source_evidence: list[DossierEvidence],
    connector_evidence: list[DossierEvidence],
    external_evidence: list[DossierEvidence],
) -> str:
    source_titles = _joined_titles(source_evidence[:3])
    connector_titles = _joined_titles(connector_evidence[:3])
    external_clause = (
        f"External research is present, but it should be used as market or methodological context, not as authority over the vault. "
        if external_evidence
        else "No external research evidence is available in this dossier, so outside-market conclusions should remain tentative. "
    )
    return (
        f"An expert would read **{_clean_inline(topic)}** by separating three questions. "
        f"First, what is directly visible in the source material? Here the main anchors are {source_titles or 'not yet strong enough'}. "
        f"Second, what relationship does the knowledge graph add? The relevant connectors include {connector_titles or 'no strong connector set'}. "
        f"Third, what action follows only after validation? {external_clause}"
        "The practical lesson is to preserve the chain from source fact to connector meaning to recommendation; without that chain, the dossier becomes another unsupported memo."
    )


def _judgment_narrative(evidence_items: list[DossierEvidence]) -> str:
    if not evidence_items:
        return "No panel or business-plan judgment artifact was available for this dossier."
    bullets = "\n".join(f"- {_evidence_sentence(item)}" for item in evidence_items[:5])
    return (
        "The panel and business-plan layer should be read after the source and connector layers. "
        "These notes are useful because they turn the technical graph into decisions, but they are not stronger than the evidence beneath them.\n\n"
        f"{bullets}"
    )


def _source_lineage(source_evidence: list[DossierEvidence], connector_evidence: list[DossierEvidence]) -> str:
    lineage = [item for item in [*source_evidence, *connector_evidence] if _vault_wikilink(item)]
    if not lineage:
        return "Source Trace: no readable internal Vault links were available for this dossier."
    lines = [
        "Source Trace: the following Vault notes are the primary path from source observation to graph-level reasoning.",
        "",
    ]
    for item in lineage[:10]:
        lines.append(f"- {_vault_wikilink(item)} matters because {_lineage_reason(item)}")
    return "\n".join(lines)


def _learning_claim_block(
    index: int,
    claim: DossierClaim,
    evidence_map: dict[str, DossierEvidence],
) -> list[str]:
    evidence_items = [evidence_map[evidence_id] for evidence_id in claim.evidence_ids if evidence_id in evidence_map]
    evidence_labels = ", ".join(_evidence_ref(item) for item in evidence_items) or "no evidence"
    explanation = _learning_explanation(claim, evidence_items)
    importance = _learning_importance(claim, evidence_items)
    lines = [
        f"### {index}. {_clean_heading(claim.text)}",
        "",
        f"**What it means.** {explanation}",
        "",
        f"**Why it matters.** {importance}",
        "",
        f"**Evidence trail.** {evidence_labels}.",
        "",
    ]
    if claim.rationale:
        lines.extend([f"**Audit note.** {_clean_inline(claim.rationale)}", ""])
    return lines


def _external_context_block(evidence: DossierEvidence) -> list[str]:
    source = evidence.source_url or evidence.source_title or "external source"
    return [
        f"### {_clean_heading(evidence.claim)}",
        "",
        f"- What it contributes: {_clean_inline(evidence.excerpt)}",
        f"- Source: {source}",
        f"- Use limitation: `{evidence.allowed_use}`",
        "",
    ]


def _verification_summary(draft: DossierDraft) -> str:
    if draft.verification is None:
        return "Verification was not attached to this dossier."
    failure_count = len(draft.verification.failures)
    warning_count = len(draft.verification.warnings)
    status = "passed" if draft.verification.passed else "failed"
    return (
        f"Verification **{status}** with supported-claim coverage `{draft.verification.claim_coverage}`. "
        f"Failure count: `{failure_count}`. Warning count: `{warning_count}`."
    )


def _evidence_by_role(evidence: list[DossierEvidence], source_types: set[str]) -> list[DossierEvidence]:
    return [item for item in evidence if item.source_type in source_types]


def _rank_claims_for_reading(
    claims: list[DossierClaim],
    evidence_map: dict[str, DossierEvidence],
) -> list[DossierClaim]:
    def score(claim: DossierClaim) -> tuple[int, int, str]:
        evidence_items = [evidence_map[evidence_id] for evidence_id in claim.evidence_ids if evidence_id in evidence_map]
        source_weight = max((_source_type_weight(item.source_type) for item in evidence_items), default=0)
        if any(item.source_type in {"vault_business_plan", "vault_evaluation"} for item in evidence_items):
            source_weight -= 4
        if claim.text.lower() in {"technical innovation", "engineering assessment", "commercial feasibility"}:
            source_weight -= 8
        if claim.text.lower().startswith("business plan - "):
            source_weight -= 8
        if any(len(item.excerpt.split()) >= 25 for item in evidence_items):
            source_weight += 3
        text_len = min(len(claim.text), 160)
        return (source_weight, text_len, claim.text)

    return sorted(claims, key=score, reverse=True)


def _source_type_weight(source_type: str) -> int:
    weights = {
        "vault_business_plan": 9,
        "vault_evaluation": 8,
        "vault_impact": 7,
        "vault_wisdom": 6,
        "vault_insight": 5,
        "vault_knowledge": 4,
        "vault_information": 3,
        "vault_data": 2,
        "vault_source": 1,
        "tavily_external": 1,
    }
    return weights.get(source_type, 0)


def _evidence_block(evidence: DossierEvidence) -> list[str]:
    source = _display_source(evidence)
    lines = [
        f"#### {evidence.evidence_id}: {_clean_heading(evidence.source_title or evidence.claim)}",
        "",
        f"- Source type: `{evidence.source_type}`",
        f"- Source: `{source}`",
        f"- Confidence: `{evidence.confidence}`",
        f"- Allowed use: `{evidence.allowed_use}`",
    ]
    if evidence.source_query:
        lines.append(f"- Tavily query: `{evidence.source_query}`")
    if evidence.source_timestamp:
        lines.append(f"- Source timestamp: `{evidence.source_timestamp}`")
    lines.extend(["", f"Claim: {_clean_inline(evidence.claim)}", "", f"Excerpt: {_clean_inline(evidence.excerpt)}", ""])
    return lines


def _evidence_sentence(evidence: DossierEvidence) -> str:
    title = _clean_inline(evidence.source_title or evidence.claim)
    excerpt = _first_sentence(evidence.excerpt)
    if excerpt and excerpt.lower() != title.lower():
        return f"{title} ({_evidence_ref(evidence)}) gives this signal: {excerpt}."
    return f"{title} ({_evidence_ref(evidence)})."


def _learning_explanation(claim: DossierClaim, evidence_items: list[DossierEvidence]) -> str:
    for item in evidence_items:
        excerpt = _first_sentence(item.excerpt, limit=360)
        if excerpt and excerpt.lower() != _clean_inline(item.source_title or item.claim).lower():
            return excerpt
    return _clean_inline(claim.text)


def _learning_importance(claim: DossierClaim, evidence_items: list[DossierEvidence]) -> str:
    source_types = {item.source_type for item in evidence_items}
    if source_types & {"vault_insight", "vault_wisdom", "vault_impact"}:
        return "It converts source facts into a mechanism or decision rule, which is where the dossier starts teaching rather than merely collecting notes."
    if source_types & {"vault_knowledge"}:
        return "It explains a relationship between concepts, so the reader can see why the graph edge exists instead of treating the link as decoration."
    if source_types & {"vault_information", "vault_data", "vault_source"}:
        return "It is part of the factual base. Later recommendations are only credible if this observation survives source review."
    if source_types & {"tavily_external"}:
        return "It provides outside context, but it should be reconciled with the vault before becoming a product claim."
    return "It gives the reader a traceable claim that can be audited against the evidence appendix."


def _evidence_ref(evidence: DossierEvidence) -> str:
    label = _clean_inline(evidence.source_title or evidence.claim)
    if evidence.source_url:
        return f"{evidence.evidence_id}: {label} ({evidence.source_url})"
    return f"{evidence.evidence_id}: {label}"


def _display_source(evidence: DossierEvidence) -> str:
    source = evidence.source_path or evidence.source_url or evidence.source_title or "unknown source"
    if evidence.source_path:
        source = re.sub(r"-(eval|bp)_[a-f0-9]{12,}", "", source)
        source = re.sub(r"dos_[a-f0-9]{12}", "dossier", source)
    return source


def _vault_wikilink(evidence: DossierEvidence) -> str:
    if not evidence.source_path:
        return ""
    if evidence.source_type in {"vault_evaluation", "vault_business_plan"}:
        return ""
    target = evidence.source_path.removesuffix(".md")
    title = _clean_inline(evidence.source_title or evidence.claim)
    return f"[[{target}|{title}]]"


def _lineage_reason(evidence: DossierEvidence) -> str:
    if evidence.source_type == "vault_source":
        return "it preserves the original source context before interpretation."
    if evidence.source_type in {"vault_data", "vault_information"}:
        return "it states the extracted technical fact that later reasoning depends on."
    if evidence.source_type == "vault_knowledge":
        return "it names a relationship in the graph rather than leaving an unexplained edge."
    if evidence.source_type in {"vault_insight", "vault_wisdom", "vault_impact"}:
        return "it converts the graph relationship into a mechanism, principle, or action."
    return "it contributes traceable context."


def _joined_titles(evidence_items: list[DossierEvidence]) -> str:
    titles = [_clean_inline(item.source_title or item.claim) for item in evidence_items if item.source_title or item.claim]
    return ", ".join(titles)


def _source_type_label(source_type: str) -> str:
    labels = {
        "vault_source": "Vault Source Notes",
        "vault_data": "Data Notes",
        "vault_information": "Information Notes",
        "vault_knowledge": "Knowledge Connectors",
        "vault_insight": "Insight Notes",
        "vault_wisdom": "Wisdom Notes",
        "vault_impact": "Impact Notes",
        "vault_evaluation": "Expert Evaluation Notes",
        "vault_business_plan": "Business Plan Notes",
        "tavily_external": "Tavily External Evidence",
    }
    return labels.get(source_type, source_type.replace("_", " ").title())


def _first_sentence(value: str, limit: int = 240) -> str:
    text = _clean_inline(value)
    if not text:
        return ""
    match = re.search(r"(.{40,}?[.!?])(?:\s|$)", text)
    sentence = match.group(1) if match else text
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "."


def _clean_heading(value: str, limit: int = 110) -> str:
    text = _clean_inline(value)
    text = re.sub(r"^[|\\/\s]+|[|\\/\s]+$", "", text)
    if len(text) <= limit:
        return text or "Untitled"
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "."


def _clean_inline(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text.replace("|", " ")).strip()
    repairs = {
        "generatio n": "generation",
        "correlatio n": "correlation",
        "informatio n": "information",
        "optimizatio n": "optimization",
        "validatio n": "validation",
        "simulatio n": "simulation",
    }
    lowered = text.lower()
    for wrong, right in repairs.items():
        if wrong in lowered:
            text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
    return text


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned[:90] or "dossier"
