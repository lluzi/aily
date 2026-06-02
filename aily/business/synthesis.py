from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aily.business.store import BusinessPlanStore
from aily.writer.vault_layout import ensure_v1_vault_layout


TEAMS = ("technical_innovation", "engineering_assessment", "commercial_feasibility")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned[:80] or "business-plan"


def _slugify_note_title(title: str, max_length: int = 150) -> str:
    cleaned = "".join(c for c in str(title) if c.isalnum() or c in " -_").strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "Untitled"
    return cleaned.replace(" ", "_")[:max_length].rstrip("_")


def _frontmatter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _context_labels(items: list[dict[str, Any]], limit: int = 5) -> list[str]:
    labels: list[str] = []
    for item in items:
        label = str(item.get("label") or item.get("title") or item.get("node_id") or item.get("relative_path") or "").strip()
        if label and label not in labels:
            labels.append(label[:180])
        if len(labels) >= limit:
            break
    return labels


def _context_links(items: list[dict[str, Any]], limit: int = 8, vault_path: Path | None = None) -> list[str]:
    links: list[str] = []
    for item in items:
        label = str(item.get("label") or item.get("title") or item.get("node_id") or "").strip()
        if not label:
            continue
        rel = str(item.get("relative_path") or item.get("obsidian_path") or "").strip()
        if not rel and vault_path is not None:
            rel = _find_vault_note_by_label(vault_path, label)
        if rel.endswith(".md"):
            rel = rel[:-3]
        if rel and "/" in rel:
            link = f"[[{rel}|{label[:120]}]]"
        else:
            # Business reports should not invent wikilinks unless they know the
            # vault-relative target. Plain labels are preferable to unresolved
            # graph links in high-value reports.
            link = label[:180]
        if link not in links:
            links.append(link)
        if len(links) >= limit:
            break
    return links


def _find_vault_note_by_label(vault_path: Path, label: str) -> str:
    """Find an existing vault-relative note path for a selected graph label."""
    slug = _slugify_note_title(label)
    if not slug:
        return ""
    for root_name in ("02-Information", "03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"):
        root = vault_path / root_name
        if not root.exists():
            continue
        direct = sorted(root.rglob(f"{slug}.md"))
        if direct:
            return str(direct[0].relative_to(vault_path))
    normalized = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
    for root_name in ("02-Information", "03-Knowledge"):
        root = vault_path / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            stem = re.sub(r"[^a-z0-9]+", " ", path.stem.lower()).strip()
            if stem == normalized:
                return str(path.relative_to(vault_path))
    return ""


def _clean_claim(value: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -")
    text = re.sub(r"#+\s*", "", text)
    text = text.replace(" www ", " ").replace(" com ", " ")
    if len(text.split()) < 6:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "."


def _research_claims(research_jobs: list[dict[str, Any]], limit: int = 5) -> list[str]:
    claims: list[str] = []
    for job in research_jobs:
        packet = job.get("packet", {})
        for claim in packet.get("claims", []) if isinstance(packet, dict) else []:
            text = str(claim.get("claim") or "").strip()
            text = _clean_claim(text)
            if text:
                claims.append(text)
            if len(claims) >= limit:
                return claims
    return claims


def _second_opinion_claims(second_opinion_packets: list[dict[str, Any]], limit: int = 5) -> list[str]:
    claims: list[str] = []
    for packet_record in second_opinion_packets:
        packet = packet_record.get("packet", {})
        for claim in packet.get("major_claims", []) if isinstance(packet, dict) else []:
            text = str(claim).strip()
            text = _clean_claim(text)
            if text:
                claims.append(text)
            if len(claims) >= limit:
                return claims
    return claims


class BusinessPlanSynthesizer:
    def __init__(self, *, store: BusinessPlanStore, vault_path: Path) -> None:
        self.store = store
        self.vault_path = vault_path.expanduser().resolve()

    async def run_team_evaluations(
        self,
        *,
        workflow_run_id: str,
        workflow_plan_id: str,
        motive: str,
        knowledge_context: list[dict[str, Any]],
        iwi_result: dict[str, Any] | None = None,
        research_jobs: list[dict[str, Any]] | None = None,
        second_opinion_packets: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        evaluations: list[dict[str, Any]] = []
        for team in TEAMS:
            payload = self._team_payload(
                team=team,
                workflow_run_id=workflow_run_id,
                workflow_plan_id=workflow_plan_id,
                motive=motive,
                knowledge_context=knowledge_context,
                iwi_result=iwi_result or {},
                research_jobs=research_jobs or [],
                second_opinion_packets=second_opinion_packets or [],
            )
            record = await self.store.create_team_evaluation(
                workflow_run_id=workflow_run_id,
                workflow_plan_id=workflow_plan_id,
                team=team,
                payload=payload,
            )
            path = self._write_evaluation_markdown(record)
            record = await self.store.update_team_evaluation_obsidian_path(record["evaluation_id"], str(path.relative_to(self.vault_path)))
            evaluations.append(record)
        return evaluations

    async def synthesize_business_plan(
        self,
        *,
        workflow_run_id: str,
        workflow_plan_id: str,
        motive: str,
        evaluations: list[dict[str, Any]],
        knowledge_context: list[dict[str, Any]],
        research_jobs: list[dict[str, Any]] | None = None,
        second_opinion_packets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = self._business_plan_payload(
            workflow_run_id=workflow_run_id,
            workflow_plan_id=workflow_plan_id,
            motive=motive,
            evaluations=evaluations,
            knowledge_context=knowledge_context,
            research_jobs=research_jobs or [],
            second_opinion_packets=second_opinion_packets or [],
        )
        markdown = self._business_plan_markdown(payload)
        title = str(payload["title"])
        record = await self.store.create_business_plan(
            workflow_run_id=workflow_run_id,
            workflow_plan_id=workflow_plan_id,
            title=title,
            payload=payload,
            markdown=markdown,
        )
        path = self._write_business_plan_markdown(record)
        return await self.store.update_business_plan_obsidian_path(
            record["business_plan_id"],
            str(path.relative_to(self.vault_path)),
        )

    def _team_payload(
        self,
        *,
        team: str,
        workflow_run_id: str,
        workflow_plan_id: str,
        motive: str,
        knowledge_context: list[dict[str, Any]],
        iwi_result: dict[str, Any],
        research_jobs: list[dict[str, Any]],
        second_opinion_packets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        labels = _context_labels(knowledge_context)
        links = _context_links(knowledge_context, vault_path=self.vault_path)
        research_claims = _research_claims(research_jobs)
        second_claims = _second_opinion_claims(second_opinion_packets)
        base = {
            "workflow_run_id": workflow_run_id,
            "workflow_plan_id": workflow_plan_id,
            "team": team,
            "motive": motive,
            "internal_evidence": labels,
            "internal_evidence_links": links,
            "external_research_claims": research_claims,
            "second_opinion_claims": second_claims,
            "iwi_final_stage": iwi_result.get("final_stage", ""),
            "source_lineage": _source_lineage(knowledge_context),
            "truth_policy": {
                "internal_aily_knowledge_distinct": True,
                "external_research_distinct": True,
                "second_opinion_non_authoritative": True,
            },
            "created_at": _utc_now(),
        }
        if team == "technical_innovation":
            return {
                **base,
                "novelty_assessment": f"Investigate novelty around {labels[0] if labels else motive[:80]}.",
                "prior_art_map": research_claims,
                "differentiators": labels[:3],
                "invention_opportunities": ["Validate a defensible technical wedge before scaling."],
                "technical_moat_hypothesis": "Moat depends on proprietary workflow integration and evidence lineage.",
                "risk_of_obviousness": "Medium until prior art is reviewed source-by-source.",
            }
        if team == "engineering_assessment":
            return {
                **base,
                "feasible_architecture": "Use staged ingestion, graph-backed context selection, and auditable workflow records.",
                "mvp_scope": ["Ingest source", "Generate Knowledge", "Run confirmed I/W/I", "Produce evidence-backed plan"],
                "dependency_map": ["LLM provider", "SQLite stores", "Obsidian vault", "Tavily research"],
                "implementation_milestones": ["Prototype", "Evidence gate", "User review", "Pilot"],
                "cost_and_runtime_risks": ["LLM latency", "Research quota", "Vault write consistency"],
                "build_buy_recommendations": "Build orchestration and lineage; buy commodity search and document conversion.",
            }
        return {
            **base,
            "target_customer": "Teams with high-value knowledge workflows and unclear commercialization paths.",
            "buyer_user_distinction": "Buyer owns budget; user needs daily research and synthesis leverage.",
            "competitor_landscape": research_claims,
            "market_signals": labels[:3],
            "pricing_hypothesis": "Start with expert-workflow subscription or project-based pilot pricing.",
            "go_to_market_wedge": "Begin with evidence-heavy planning use cases where traceability matters.",
            "commercial_risks": ["Weak urgency", "Unclear buyer", "Existing manual consultant workflow"],
        }

    def _business_plan_payload(
        self,
        *,
        workflow_run_id: str,
        workflow_plan_id: str,
        motive: str,
        evaluations: list[dict[str, Any]],
        knowledge_context: list[dict[str, Any]],
        research_jobs: list[dict[str, Any]],
        second_opinion_packets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source_ids = _source_lineage(knowledge_context)
        evaluation_ids = [str(item.get("evaluation_id")) for item in evaluations]
        research_ids = [str(item.get("research_id")) for item in research_jobs]
        second_opinion_ids = [str(item.get("second_opinion_id")) for item in second_opinion_packets]
        labels = _context_labels(knowledge_context, limit=8)
        links = _context_links(knowledge_context, limit=8, vault_path=self.vault_path)
        research_claims = _research_claims(research_jobs, limit=5)
        second_claims = _second_opinion_claims(second_opinion_packets, limit=5)
        opportunity = labels[0] if labels else motive[:100]
        technical_wedge = ", ".join(labels[:3]) or "the selected Aily knowledge context"
        return {
            "title": f"Business Plan - {opportunity}",
            "workflow_run_id": workflow_run_id,
            "workflow_plan_id": workflow_plan_id,
            "evaluation_ids": evaluation_ids,
            "research_ids": research_ids,
            "second_opinion_ids": second_opinion_ids,
            "source_ids": source_ids,
            "knowledge_labels": labels,
            "knowledge_links": links,
            "research_claims": research_claims,
            "second_opinion_claims": second_claims,
            "motive": motive,
            "executive_summary": (
                f"Aily identified a focused opportunity around {opportunity}. The internal knowledge graph points to "
                f"{technical_wedge} as the strongest initial wedge, while external research and second-opinion material "
                "remain supporting inputs rather than authority. The plan should proceed only as a constrained validation "
                "pilot that proves buyer urgency, technical repeatability, and measurable workflow value."
            ),
            "problem_definition": (
                f"The target users appear to face a workflow problem where {opportunity.lower()} is valuable but hard "
                "to operationalize repeatedly. The evidence suggests a gap between isolated technical capability, "
                "methodology confidence, and a repeatable process that teams can trust across design iterations."
            ),
            "proposed_solution": (
                f"Build a narrow pilot around {opportunity}: ingest the relevant evidence, preserve source lineage, "
                "construct readable connector notes between the supporting concepts, and test whether the workflow can "
                "produce a decision-grade artifact faster or with less rework than the current process. The output is "
                "accepted only when the source-to-connector-to-plan chain remains readable to a human reviewer."
            ),
            "unresolved_risks": [
                "External claims require independent verification.",
                "Customer willingness to pay remains unproven.",
                "Technical moat needs source-by-source prior-art review.",
            ],
            "kill_criteria": [
                "No reachable buyer with urgent pain.",
                "Prior art removes the technical wedge.",
                "Pilot cannot produce measurable value within the planned scope.",
            ],
            "recommendation": (
                "Proceed only if the first pilot can retire the buyer, prior-art, and proof-of-value risks with a small "
                "number of real user interviews and one measurable technical demonstration."
            ),
            "created_at": _utc_now(),
            "evidence_policy": {
                "internal_aily_knowledge_distinct": True,
                "external_research_distinct": True,
                "second_opinion_non_authoritative": True,
            },
        }

    def _write_evaluation_markdown(self, record: dict[str, Any]) -> Path:
        ensure_v1_vault_layout(self.vault_path)
        payload = record["payload"]
        path = self.vault_path / "08-Evaluations" / f"{record['team']}-{record['evaluation_id']}.md"
        frontmatter = _frontmatter(
            {
                "artifact_type": "team_evaluation",
                "evaluation_id": record["evaluation_id"],
                "workflow_run_id": record["workflow_run_id"],
                "workflow_plan_id": record["workflow_plan_id"],
                "team": record["team"],
                "source_ids": payload.get("source_lineage", []),
                "origin_creator": "application",
                "origin_modified_by_lead_agent": "false",
                "created_at": payload.get("created_at", _utc_now()),
            }
        )
        path.write_text(
            f"{frontmatter}\n\n# {record['team'].replace('_', ' ').title()}\n\n"
            f"## Evaluation Question\n{_team_question(record['team'])}\n\n"
            f"## Source Knowledge Lineage\n{_source_lineage_section(payload)}\n\n"
            f"## External Research Claims\n{_bullets(payload.get('external_research_claims', []))}\n\n"
            f"## Second Opinion Claims\n{_bullets(payload.get('second_opinion_claims', []))}\n\n"
            f"## Findings\n{_evaluation_findings(payload)}\n\n"
            f"## Decision Implications\n{_evaluation_implications(payload)}\n\n"
            f"## Risks And Checks\n{_bullets(_evaluation_risks(payload))}\n\n"
            f"## Recommendation\n{_team_recommendation(record['team'], payload)}\n",
            encoding="utf-8",
        )
        return path

    def _write_business_plan_markdown(self, record: dict[str, Any]) -> Path:
        ensure_v1_vault_layout(self.vault_path)
        payload = record["payload"]
        path = self.vault_path / "09-Business-Plans" / f"{_slug(record['title'])}-{record['business_plan_id']}.md"
        path.write_text(record["markdown"], encoding="utf-8")
        return path

    def _business_plan_markdown(self, payload: dict[str, Any]) -> str:
        frontmatter = _frontmatter(
            {
                "artifact_type": "business_plan",
                "workflow_run_id": payload["workflow_run_id"],
                "workflow_plan_id": payload["workflow_plan_id"],
                "evaluation_ids": payload["evaluation_ids"],
                "research_ids": payload["research_ids"],
                "second_opinion_ids": payload["second_opinion_ids"],
                "source_ids": payload["source_ids"],
                "origin_creator": "application",
                "origin_modified_by_lead_agent": "false",
                "created_at": payload["created_at"],
            }
        )
        sections = [
            ("Executive Summary", payload["executive_summary"]),
            ("Source Knowledge Lineage", _source_lineage_section(payload)),
            ("Problem Definition", payload["problem_definition"]),
            ("Customer And Buyer", _customer_buyer_section(payload)),
            ("Proposed Solution", payload["proposed_solution"]),
            ("Technical Innovation", _technical_innovation_section(payload)),
            ("Engineering Plan", _engineering_plan_section(payload)),
            ("Evidence Review", _evidence_review_section(payload)),
            ("Commercial Feasibility", _commercial_section(payload)),
            ("Second Opinion Comparison", _second_opinion_section(payload)),
            ("Market And Competitive Landscape", _market_section(payload)),
            ("MVP Scope", _mvp_section(payload)),
            ("Validation Plan", _validation_section(payload)),
            ("Risks And Kill Criteria", _bullets([*payload["unresolved_risks"], *payload["kill_criteria"]])),
            ("Milestones", _bullets(["Discovery interviews and prior-art review", "Thin workflow prototype", "Pilot with one real team", "Evidence review and go/no-go decision"])),
            ("Investment / Resource Estimate", _investment_section(payload)),
            ("Recommendation", payload["recommendation"]),
        ]
        body = "\n\n".join(f"## {title}\n\n{content}" for title, content in sections)
        return f"{frontmatter}\n\n# {payload['title']}\n\n{body}\n"


def _source_lineage(knowledge_context: list[dict[str, Any]]) -> list[str]:
    source_ids: list[str] = []
    for item in knowledge_context:
        source_id = str(item.get("source_id") or "").strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        for raw in item.get("source_ids", []) if isinstance(item.get("source_ids"), list) else []:
            value = str(raw).strip()
            if value and value not in source_ids:
                source_ids.append(value)
        for raw_path in item.get("source_paths", []) if isinstance(item.get("source_paths"), list) else []:
            value = str(raw_path)
            if value.startswith("source_id:"):
                source_id = value.removeprefix("source_id:")
                if source_id and source_id not in source_ids:
                    source_ids.append(source_id)
    return source_ids


def _source_lineage_section(payload: dict[str, Any]) -> str:
    labels = payload.get("knowledge_labels", []) or payload.get("internal_evidence", [])
    links = payload.get("knowledge_links", []) or payload.get("internal_evidence_links", [])
    source_ids = payload.get("source_ids", [])
    lines = []
    if links:
        lines.append("Internal Aily knowledge used as the primary evidence base:")
        lines.extend(f"- {link}" for link in links)
        lines.append("")
        lines.append(
            "Why these links matter: each linked note is a source-backed concept selected from the internal graph. "
            "The report depends on these links because they let a reviewer traverse from the business claim back to "
            "the underlying Information and Knowledge notes instead of trusting the report text alone."
        )
    elif labels:
        lines.append("Internal Aily knowledge used as the primary evidence base:")
        lines.extend(f"- {label}" for label in labels)
    if source_ids:
        lines.append("")
        lines.append(f"Audit source hashes are preserved in frontmatter and evidence records for {len(source_ids)} source package(s).")
    return "\n".join(lines).strip() or "- Source lineage was not available."


def _customer_buyer_section(payload: dict[str, Any]) -> str:
    opportunity = (payload.get("knowledge_labels") or [payload.get("motive", "the workflow")])[0]
    return (
        f"Initial users are engineers, architects, or technical leads who need to make decisions around {opportunity}. "
        "The likely economic buyer is the owner of verification, signoff, or platform productivity budgets. "
        "The buyer hypothesis should be tested by asking whether the current process creates delay, avoidable reruns, "
        "or insufficient confidence in decision artifacts."
    )


def _technical_innovation_section(payload: dict[str, Any]) -> str:
    labels = payload.get("knowledge_labels", [])
    wedge = ", ".join(labels[:3]) or "the selected internal knowledge context"
    return (
        f"The technical wedge is the combination of {wedge}. The innovation is not merely producing another report; "
        "it is preserving the chain from source evidence through connector notes into a decision artifact. The main "
        "technical question is whether the connectors remain valid when the source context changes."
    )


def _engineering_plan_section(payload: dict[str, Any]) -> str:
    return (
        "Build the pilot as a narrow, auditable workflow: source intake, canonical Markdown, Data/Information/Knowledge "
        "notes, typed connector notes, triggered Insight/Wisdom/Impact, and report generation. Each step should emit "
        "quality scores so poor notes are rejected before they become report inputs. Because the plan is grounded in "
        "linked vault notes, every milestone must preserve a reviewer path from source artifact to connector meaning "
        "to final recommendation."
    )


def _evidence_review_section(payload: dict[str, Any]) -> str:
    labels = payload.get("knowledge_labels", [])
    source_count = len(payload.get("source_ids", []))
    evaluation_count = len(payload.get("evaluation_ids", []))
    research_count = len(payload.get("research_ids", []))
    first = labels[0] if labels else "the selected graph context"
    second = labels[1] if len(labels) > 1 else "the nearest supporting connector"
    third = labels[2] if len(labels) > 2 else "the strongest adjacent evidence note"
    return (
        f"The internal evidence base spans {source_count} source package(s), {evaluation_count} team evaluation(s), "
        f"and {research_count} external research packet(s). The strongest internal chain starts at {first}, then "
        f"compares it with {second} and {third}. This matters because the opportunity should survive a reviewer "
        "walking the graph manually: source-equivalent Markdown, atomic Data notes, classified Information notes, "
        "typed Knowledge connectors, and the final plan all need to tell the same story. Treat external research as "
        "context for questions, while the linked vault notes remain the primary evidence for technical claims."
    )


def _commercial_section(payload: dict[str, Any]) -> str:
    claims = payload.get("research_claims", [])
    signal = claims[0] if claims else "Commercial demand is not proven by the current evidence."
    return (
        f"The commercial case is still a validation hypothesis. External research signal: {signal} The first saleable "
        "wedge should be a workflow where traceability and speed are both valuable enough to justify adoption."
    )


def _second_opinion_section(payload: dict[str, Any]) -> str:
    claims = payload.get("second_opinion_claims", [])
    if not claims:
        return "No usable second-opinion claims were extracted. Treat this as a gap, not as supporting evidence."
    return (
        "Second-opinion material is treated as non-authoritative comparison evidence. The useful claims are:\n"
        + _bullets(claims)
    )


def _market_section(payload: dict[str, Any]) -> str:
    claims = payload.get("research_claims", [])
    if not claims:
        return "The market landscape remains under-evidenced. Run targeted research against buyer workflows, alternatives, and budget owners before scaling."
    return "External research should be used as weak market context, not proof. Current claims:\n" + _bullets(claims)


def _mvp_section(payload: dict[str, Any]) -> str:
    opportunity = (payload.get("knowledge_labels") or ["the selected opportunity"])[0]
    return (
        f"The MVP should prove one repeatable workflow around {opportunity}. It should include a small source set, "
        "human-readable connector notes, one decision-grade report, and a before/after comparison against the current manual process."
    )


def _validation_section(payload: dict[str, Any]) -> str:
    return (
        "Validation requires three concrete artifacts: buyer interview notes showing urgent pain, a technical demo that "
        "recreates the source-to-connector-to-report chain, and a quality score proving the generated vault is readable "
        "without manual editing."
    )


def _investment_section(payload: dict[str, Any]) -> str:
    return (
        "Keep investment limited to one workflow slice until the pilot proves value. Required effort is a source-processing "
        "engineer, a graph/connector implementation pass, and a domain reviewer who can judge whether the generated report "
        "contains decision-grade substance."
    )


def _team_question(team: str) -> str:
    questions = {
        "technical_innovation": "Is there a defensible technical wedge that is more than a restatement of the source material?",
        "engineering_assessment": "Can this be implemented as a reliable, observable workflow with stable evidence lineage?",
        "commercial_feasibility": "Is there a buyer, urgent workflow pain, and proof artifact strong enough to justify a pilot?",
    }
    return questions.get(team, "What does this team need to validate before the plan can be trusted?")


def _evaluation_findings(payload: dict[str, Any]) -> str:
    team = str(payload.get("team") or "")
    labels = payload.get("internal_evidence", [])
    primary = labels[0] if labels else "the selected workflow"
    support = ", ".join(labels[1:4]) if len(labels) > 1 else "the supporting knowledge notes"
    if team == "technical_innovation":
        items = [
            f"Primary technical wedge: {primary}. The opportunity depends on whether this can be converted from a presentation-level method into a repeatable workflow primitive.",
            f"Supporting concepts: {support}. These create a stronger claim when they are connected as a workflow chain rather than read as isolated facts.",
            f"Differentiators: {', '.join(payload.get('differentiators', [])[:3]) or 'not established'}",
            payload.get("technical_moat_hypothesis", ""),
            f"Obviousness risk: {payload.get('risk_of_obviousness', 'not assessed')}",
        ]
    elif team == "engineering_assessment":
        items = [
            f"Build target: {primary}. The pilot should prove that the source intake, graph selection, connector notes, and final report can be reproduced without manual note repair.",
            payload.get("feasible_architecture", ""),
            "MVP scope: " + ", ".join(payload.get("mvp_scope", [])),
            "Runtime risks: " + ", ".join(payload.get("cost_and_runtime_risks", [])),
            "Operational quality check: reject the run if high-level notes cannot explain why their links exist.",
        ]
    else:
        items = [
            f"Commercial wedge: {primary}. The near-term buyer story is strongest if the workflow reduces verification delay, rerun cost, or uncertainty in power-related decisions.",
            payload.get("target_customer", ""),
            payload.get("buyer_user_distinction", ""),
            payload.get("go_to_market_wedge", ""),
            "Evidence strength remains provisional until buyer interviews confirm this is urgent enough to fund.",
        ]
    return _bullets([item for item in items if item])


def _evaluation_implications(payload: dict[str, Any]) -> str:
    team = str(payload.get("team") or "")
    labels = payload.get("internal_evidence", [])
    primary = labels[0] if labels else "the selected workflow"
    if team == "technical_innovation":
        items = [
            f"Treat {primary} as a hypothesis about a reusable mechanism, not as a proven invention.",
            "Require prior-art review to compare the connector chain against existing emulation and low-power verification methods.",
            "Use the generated connector notes to identify which part of the method is actually novel: speed, translation fidelity, replay automation, or evidence lineage.",
        ]
    elif team == "engineering_assessment":
        items = [
            f"Implement only the thinnest slice that demonstrates {primary} end to end.",
            "Instrument every stage with durable run records, vault output paths, and quality scores so failed notes cannot silently enter the report.",
            "Use a domain reviewer to judge whether the generated plan would change an engineering decision.",
        ]
    else:
        items = [
            f"Frame the initial pilot around the pain created when {primary} is slow, unreliable, or difficult to audit.",
            "Do not infer market demand from generic feasibility research; use it only to shape interview questions.",
            "Advance only if a named buyer confirms a budgeted workflow problem and accepts the quality gate as credible evidence.",
        ]
    return _bullets(items)


def _evaluation_risks(payload: dict[str, Any]) -> list[str]:
    team = str(payload.get("team") or "")
    if team == "commercial_feasibility":
        return list(payload.get("commercial_risks", [])) or ["Buyer urgency is not yet proven."]
    if team == "engineering_assessment":
        return list(payload.get("cost_and_runtime_risks", [])) or ["Workflow reliability must be proven in a real run."]
    return [payload.get("risk_of_obviousness", "Prior art must be reviewed source-by-source.")]


def _team_recommendation(team: str, payload: dict[str, Any]) -> str:
    if team == "technical_innovation":
        return "Proceed only after prior-art review confirms the technical wedge is not obvious."
    if team == "engineering_assessment":
        return "Proceed with a thin pilot that proves stable source lineage, connector generation, and quality scoring."
    return "Proceed only if buyer interviews confirm urgent pain and willingness to evaluate a pilot."


def _bullets(items: list[Any]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- Not recorded."


def _pretty(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
