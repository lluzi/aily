"""Reactor - The Innovation Laval Nozzle.

Wide inputs from multiple innovation methodologies running in parallel,
focused through a synthesis nozzle into high-quality proposals.

Name origin: Innovation + Laval (convergent-divergent nozzle principle)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from aily.dikiwi.agents.llm_tools import chat_json
from aily.sessions.base import BaseMindScheduler
from aily.sessions.models import Proposal, ProposalStage, ProposalType

logger = logging.getLogger(__name__)

RESIDUAL_PROPOSAL_REACTOR_STATUSES = ("pending_innovation", "ready_for_screening")


class InnovationMethod(Enum):
    """Available innovation methodologies."""
    TRIZ = "triz"
    SIT = "sit"
    SIX_HATS = "six_hats"
    BIOMIMICRY = "biomimicry"
    MORPHOLOGICAL = "morphological"
    BLUE_OCEAN = "blue_ocean"
    SCAMPER = "scamper"
    FIRST_PRINCIPLES = "first_principles"


DEFAULT_REACTOR_METHODS = {
    InnovationMethod.TRIZ,
    InnovationMethod.SIT,
    InnovationMethod.SIX_HATS,
    InnovationMethod.SCAMPER,
    InnovationMethod.BLUE_OCEAN,
    InnovationMethod.FIRST_PRINCIPLES,
}


@dataclass
class MethodResult:
    """Result from a single innovation method."""
    method: InnovationMethod
    proposals: list[Proposal]
    confidence: float
    processing_time_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NozzleConfig:
    """Configuration for the innovation nozzle."""
    min_confidence: float = 0.6
    min_novelty_score: float = 0.5
    min_feasibility_score: float = 0.4
    max_proposals_per_session: int = 10
    diversity_threshold: float = 0.7
    enabled_methods: set = field(default_factory=lambda: set(DEFAULT_REACTOR_METHODS))


class ReactorScheduler(BaseMindScheduler):
    """Innovation Laval Nozzle - wide inputs, focused output.

    Architecture:
        [Wide Input] -> [Parallel Methods] -> [Synthesis Nozzle] -> [Focused Proposals]

        TRIZ, SIT, Six Hats, Biomimicry, Morphological, Blue Ocean, SCAMPER...
    """

    def __init__(
        self,
        llm_client: Any,
        graph_db: Any,
        obsidian_writer: Any | None = None,
        feishu_pusher: Any | None = None,
        schedule_hour: int = 8,
        schedule_minute: int = 0,
        circuit_breaker_threshold: int = 3,
        enabled: bool = True,
        nozzle_config: NozzleConfig | None = None,
        method_timeout_seconds: float = 180.0,
    ) -> None:
        super().__init__(
            llm_client=llm_client,
            mind_name="reactor",
            schedule_hour=schedule_hour,
            schedule_minute=schedule_minute,
            circuit_breaker_threshold=circuit_breaker_threshold,
            enabled=enabled,
        )
        self.graph_db = graph_db
        self.obsidian_writer = obsidian_writer
        self.feishu_pusher = feishu_pusher
        self.nozzle_config = nozzle_config or NozzleConfig()
        self.method_timeout_seconds = max(1.0, float(method_timeout_seconds))
        self._analyzers: dict[InnovationMethod, Any] = {}
        self._current_session_proposals: list[Proposal] = []

    async def _run_method_bounded(
        self,
        method: InnovationMethod,
        context: dict[str, Any],
        budget: Any | None = None,
    ) -> MethodResult | None:
        try:
            return await asyncio.wait_for(
                self._run_method(method, context, budget=budget),
                timeout=self.method_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[Reactor] Method %s timed out after %.1fs",
                method.value,
                self.method_timeout_seconds,
            )
            await self.circuit_breaker.record_failure()
            return MethodResult(method=method, proposals=[], confidence=0.0)

    def _get_analyzer(self, method: InnovationMethod) -> Any:
        """Get or create analyzer for a method."""
        if method in self._analyzers:
            return self._analyzers[method]

        # Lazy import to avoid circular dependencies
        try:
            if method == InnovationMethod.TRIZ:
                from aily.thinking.frameworks.triz import TrizAnalyzer
                analyzer = TrizAnalyzer(self.llm_client)
            elif method == InnovationMethod.SIT:
                from aily.thinking.frameworks.sit import SITAnalyzer
                analyzer = SITAnalyzer(self.llm_client)
            elif method == InnovationMethod.SIX_HATS:
                from aily.thinking.frameworks.six_hats import SixHatsAnalyzer
                analyzer = SixHatsAnalyzer(self.llm_client)
            elif method == InnovationMethod.BIOMIMICRY:
                from aily.thinking.frameworks.biomimicry import BiomimicryAnalyzer
                analyzer = BiomimicryAnalyzer(self.llm_client)
            elif method == InnovationMethod.MORPHOLOGICAL:
                from aily.thinking.frameworks.morphological import MorphologicalAnalyzer
                analyzer = MorphologicalAnalyzer(self.llm_client)
            elif method == InnovationMethod.BLUE_OCEAN:
                from aily.thinking.frameworks.blue_ocean import BlueOceanAnalyzer
                analyzer = BlueOceanAnalyzer(self.llm_client)
            elif method == InnovationMethod.SCAMPER:
                from aily.thinking.frameworks.scamper import ScamperAnalyzer
                analyzer = ScamperAnalyzer(self.llm_client)
            elif method == InnovationMethod.FIRST_PRINCIPLES:
                from aily.thinking.frameworks.first_principles import FirstPrinciplesAnalyzer
                analyzer = FirstPrinciplesAnalyzer(self.llm_client)
            else:
                return None

            self._analyzers[method] = analyzer
            return analyzer

        except Exception as e:
            logger.warning(f"Failed to load analyzer for {method.value}: {e}")
            return None

    async def evaluate_context(
        self,
        context: dict[str, Any],
        budget: Any | None = None,
        persist: bool = False,
        output: bool = False,
    ) -> list[Proposal]:
        """Run enabled innovation frameworks on a context and return proposals.

        Lightweight entrypoint for per-pipeline invocation (e.g. from DikiwiMind).
        Skips scheduler boilerplate and output delivery.
        """
        if not await self.circuit_breaker.can_execute():
            logger.warning("[Reactor] evaluate_context skipped: circuit breaker is open")
            return []

        enabled = self.nozzle_config.enabled_methods
        tasks = []
        for method in enabled:
            task = self._run_method_bounded(method, context, budget=budget)
            tasks.append(task)

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            await self.circuit_breaker.record_failure()
            logger.error(f"[Reactor] evaluate_context failed: {exc}")
            return []

        valid_results = [
            r for r in results
            if isinstance(r, MethodResult) and r.proposals
        ]

        # Check if any method actually failed (returned empty due to error)
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            await self.circuit_breaker.record_failure()
        else:
            await self.circuit_breaker.record_success()

        logger.info(f"[Reactor] evaluate_context: {len(valid_results)}/{len(enabled)} methods produced proposals")
        proposals = await self._synthesis_nozzle(valid_results)
        if not proposals:
            proposals = await self._generate_context_fallback_proposals(context, budget=budget)
        self._current_session_proposals = proposals
        if persist:
            await self._persist_framework_proposals(proposals)
        if output:
            await self._output_proposals(proposals, datetime.now(timezone.utc))
        return proposals

    async def _run_session(self) -> dict[str, Any]:
        """Execute Reactor session."""
        session_start = datetime.now(timezone.utc)
        logger.info("Reactor session starting - wide input phase")

        context = await self._gather_context()

        # Evaluate Residual proposals first (business-first curation)
        residual_proposals = await self._evaluate_residual_proposals()

        # Run all methods in parallel
        tasks = []
        for method in self.nozzle_config.enabled_methods:
            task = self._run_method_bounded(method, context)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results = [
            r for r in results
            if isinstance(r, MethodResult) and r.proposals
        ]

        logger.info(f"Reactor: {len(valid_results)}/{len(self.nozzle_config.enabled_methods)} methods produced proposals")

        # Add Residual proposals that passed innovation screening
        if residual_proposals:
            valid_results.append(MethodResult(
                method=InnovationMethod.FIRST_PRINCIPLES,
                proposals=residual_proposals,
                confidence=sum(p.confidence for p in residual_proposals) / len(residual_proposals),
            ))

        # Synthesis nozzle
        proposals = await self._synthesis_nozzle(valid_results)
        if not proposals:
            proposals = await self._generate_context_fallback_proposals(context)
        self._current_session_proposals = proposals
        await self._persist_framework_proposals(proposals)

        # Output
        await self._output_proposals(proposals, session_start)

        return {
            "methods_run": len(valid_results),
            "proposals_generated": len(proposals),
            "proposals_delivered": len(proposals),
        }

    async def _run_method(
        self,
        method: InnovationMethod,
        context: dict,
        budget: Any | None = None,
    ) -> MethodResult | None:
        """Run a single innovation method."""
        start_time = datetime.now(timezone.utc)
        analyzer = self._get_analyzer(method)

        if not analyzer:
            logger.warning(f"No analyzer available for {method.value}")
            return MethodResult(
                method=method,
                proposals=[],
                confidence=0.0,
            )

        # Pass budget through context so analyzers can use it if they support it
        if budget is not None:
            context = {**context, "_llm_budget": budget}

        try:
            if method == InnovationMethod.TRIZ:
                return await self._run_triz_method(context)
            result = await analyzer.analyze(context)
            processing_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

            # Convert to MethodResult format
            return MethodResult(
                method=method,
                proposals=result.proposals,
                confidence=result.confidence,
                processing_time_ms=processing_time,
                metadata=result.metadata,
            )
        except Exception as e:
            logger.error(f"Method {method.value} failed: {e}")
            await self.circuit_breaker.record_failure()
            return MethodResult(
                method=method,
                proposals=[],
                confidence=0.0,
            )

    async def _synthesis_nozzle(self, results: list[MethodResult]) -> list[Proposal]:
        """Combine framework outputs without deciding the final business winner."""
        all_proposals: list[Proposal] = []
        for result in results:
            all_proposals.extend(result.proposals)

        if not all_proposals:
            return []

        deduped: dict[str, Proposal] = {}
        for proposal in all_proposals:
            title = " ".join((proposal.title or "").lower().split())
            key = title or proposal.id
            existing = deduped.get(key)
            if existing is None or proposal.confidence > existing.confidence:
                deduped[key] = proposal

        proposals = list(deduped.values())

        # Filter by nozzle config
        filtered: list[Proposal] = []
        for p in proposals:
            if p.confidence < self.nozzle_config.min_confidence:
                continue
            # Only filter by novelty when the value is meaningfully set
            raw_novelty = p.metadata.get("novelty")
            if raw_novelty is None:
                raw_novelty = p.innovation_score
            if raw_novelty and raw_novelty < self.nozzle_config.min_novelty_score:
                continue
            filtered.append(p)

        filtered.sort(key=lambda x: x.confidence, reverse=True)

        # Apply max proposals per session limit
        max_proposals = self.nozzle_config.max_proposals_per_session
        return filtered[:max_proposals]

    async def _generate_context_fallback_proposals(
        self,
        context: dict[str, Any],
        budget: Any | None = None,
    ) -> list[Proposal]:
        """Generate bounded, evidence-grounded proposals if all Reactor methods fail or filter out."""
        recent_insights = context.get("recent_insights", []) or []
        if not recent_insights:
            return []

        evidence_lines = []
        source_ids: list[str] = []
        for item in recent_insights[:16]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            node_type = str(item.get("type", "")).strip()
            node_id = str(item.get("id", "")).strip()
            if not label:
                continue
            evidence_lines.append(f"- {node_type}: {label}")
            if node_id:
                source_ids.append(node_id)

        if not evidence_lines:
            return []

        prompt = f"""All configured Reactor methods were executed but produced no proposals that survived the nozzle.
Use the graph evidence below to create fallback innovation proposals. This is not a mock: each proposal must be grounded in the provided evidence and must be concrete enough for business evaluation.

Focus areas: {", ".join(context.get("focus_areas", []))}

Graph evidence:
{chr(10).join(evidence_lines)}

Return JSON:
{{
  "proposals": [
    {{
      "title": "specific non-truncated proposal title",
      "summary": "one concrete sentence",
      "problem": "specific workflow or market problem",
      "solution": "specific product or technical solution",
      "target_user": "specific user or buyer",
      "adoption_wedge": "first narrow adoption path",
      "proof_of_value": "prototype, benchmark, or validation artifact",
      "technical_path": "implementation path",
      "business_path": "commercial path",
      "risks": ["specific risk"],
      "confidence": 0.0,
      "innovation_score": 0.0
    }}
  ]
}}

Rules:
- Produce at most {self.nozzle_config.max_proposals_per_session} proposals.
- Do not generate generic consulting, dashboard, or platform language.
- Prefer narrow EDA/deep-tech wedges over broad weak ideas.
- Confidence must reflect evidence quality; do not inflate scores without concrete proof paths.
"""
        try:
            data = await chat_json(
                llm_client=self.llm_client,
                stage="reactor",
                stage_key="context_fallback",
                messages=[
                    {"role": "system", "content": "Return valid JSON only. Generate evidence-grounded innovation proposals."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.45,
                budget=budget,
            )
        except Exception as exc:
            logger.warning("[Reactor] Context fallback proposal generation failed: %s", exc)
            return []

        raw_proposals = data.get("proposals", []) if isinstance(data, dict) else []
        proposals: list[Proposal] = []
        for raw in raw_proposals:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "")).strip()
            summary = str(raw.get("summary", "")).strip()
            if not title or not summary:
                continue
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or self.nozzle_config.min_confidence)))
            innovation_score = max(0.0, min(1.0, float(raw.get("innovation_score") or confidence)))
            content = "\n\n".join(
                part
                for part in [
                    f"Problem: {raw.get('problem', '')}",
                    f"Solution: {raw.get('solution', '')}",
                    f"Target user: {raw.get('target_user', '')}",
                    f"Adoption wedge: {raw.get('adoption_wedge', '')}",
                    f"Proof of value: {raw.get('proof_of_value', '')}",
                    f"Technical path: {raw.get('technical_path', '')}",
                    f"Business path: {raw.get('business_path', '')}",
                    "Risks:\n" + "\n".join(f"- {risk}" for risk in raw.get("risks", []) if risk),
                ]
                if part and not part.endswith(": ")
            )
            proposals.append(
                Proposal(
                    mind_name="reactor",
                    proposal_type=ProposalType.INNOVATION,
                    title=title,
                    content=content,
                    summary=summary,
                    confidence=confidence,
                    priority="high" if confidence >= 0.8 else "medium",
                    innovation_score=innovation_score,
                    stage=ProposalStage.PENDING_BUSINESS,
                    source_knowledge_ids=source_ids[:10],
                    framework_used="Reactor-Context-Fallback",
                    metadata={
                        "framework": "Reactor-Context-Fallback",
                        "fallback_reason": "all_configured_methods_empty_or_filtered",
                        "source_evidence": evidence_lines[:10],
                        "proof_of_value": raw.get("proof_of_value", ""),
                        "adoption_wedge": raw.get("adoption_wedge", ""),
                    },
                )
            )

        proposals.sort(key=lambda item: item.confidence, reverse=True)
        return proposals[: self.nozzle_config.max_proposals_per_session]

    async def _run_triz_method(self, context: dict[str, Any]) -> MethodResult:
        """Adapt the legacy TRIZ analyzer into Reactor proposals."""
        from aily.thinking.frameworks.triz import TrizAnalyzer
        from aily.thinking.models import KnowledgePayload

        analyzer = self._analyzers.get(InnovationMethod.TRIZ)
        if analyzer is None:
            analyzer = TrizAnalyzer(self.llm_client)
            self._analyzers[InnovationMethod.TRIZ] = analyzer

        recent_insights = context.get("recent_insights", [])
        content_lines = [f"Focus Areas: {', '.join(context.get('focus_areas', ['general']))}"]
        for insight in recent_insights[:12]:
            if isinstance(insight, dict):
                label = insight.get("label", "")
                node_type = insight.get("type", "")
                content_lines.append(f"- {node_type}: {label}".strip())
            elif insight:
                content_lines.append(f"- {insight}")
        payload = KnowledgePayload(
            content="\n".join(content_lines),
            source_title="Reactor Context",
            metadata={"focus_areas": context.get("focus_areas", [])},
        )

        insight = await analyzer.analyze(payload)
        proposals = self._triz_insight_to_proposals(insight)
        return MethodResult(
            method=InnovationMethod.TRIZ,
            proposals=proposals,
            confidence=insight.confidence,
            processing_time_ms=insight.processing_time_ms or 0,
            metadata={"framework": "TRIZ"},
        )

    def _triz_insight_to_proposals(self, insight: Any) -> list[Proposal]:
        """Convert FrameworkInsight-style TRIZ output into Reactor proposals."""
        raw = getattr(insight, "raw_analysis", {}) or {}
        contradictions = raw.get("contradictions", []) or []
        recommendations = raw.get("principle_recommendations", []) or []
        evolution = raw.get("evolution_analysis")
        key_insights = list(getattr(insight, "insights", []) or [])

        proposals: list[Proposal] = []
        for index, recommendation in enumerate(recommendations[:3], start=1):
            contradiction = contradictions[min(index - 1, len(contradictions) - 1)] if contradictions else None
            contradiction_desc = getattr(contradiction, "description", "") if contradiction else ""
            principle_number = getattr(recommendation, "principle_number", "")
            principle_name = getattr(recommendation, "principle_name", "")
            application = getattr(recommendation, "application", "")
            confidence = float(getattr(recommendation, "confidence", getattr(insight, "confidence", 0.6)) or 0.6)
            title_basis = contradiction_desc or application or (key_insights[0] if key_insights else "Inventive Resolution")
            content_parts = [
                application or "Apply the inventive principle to resolve the contradiction.",
            ]
            if contradiction_desc:
                content_parts.extend(["", f"Contradiction: {contradiction_desc}"])
            if key_insights:
                content_parts.extend(["", "Key insights:"])
                content_parts.extend([f"- {item}" for item in key_insights[:5]])
            proposals.append(
                Proposal(
                    mind_name="reactor",
                    title=f"TRIZ Principle {principle_number}: {title_basis}",
                    content="\n".join(content_parts),
                    summary=application or title_basis,
                    confidence=confidence,
                    priority="high" if confidence >= 0.8 else "medium",
                    innovation_score=confidence,
                    stage=ProposalStage.PENDING_BUSINESS,
                    proposal_type=self._proposal_type_for_method(InnovationMethod.TRIZ),
                    framework_used="TRIZ",
                    metadata={
                        "framework": "TRIZ",
                        "principle_number": principle_number,
                        "principle_name": principle_name,
                        "contradiction": contradiction_desc,
                        "key_insights": key_insights[:5],
                    },
                )
            )

        if not proposals and key_insights:
            content = "\n".join(key_insights)
            confidence = float(getattr(insight, "confidence", 0.6) or 0.6)
            proposals.append(
                Proposal(
                    mind_name="reactor",
                    title=f"TRIZ S-Curve Shift: {key_insights[0]}",
                    content=content,
                    summary=key_insights[0],
                    confidence=confidence,
                    priority="high" if confidence >= 0.8 else "medium",
                    innovation_score=confidence,
                    stage=ProposalStage.PENDING_BUSINESS,
                    proposal_type=self._proposal_type_for_method(InnovationMethod.TRIZ),
                    framework_used="TRIZ",
                    metadata={"framework": "TRIZ"},
                )
            )

        if evolution is not None:
            prediction = getattr(evolution, "next_generation_prediction", "") or ""
            if prediction:
                confidence = float(getattr(insight, "confidence", 0.6) or 0.6)
                proposals.append(
                    Proposal(
                        mind_name="reactor",
                        title=f"TRIZ Next Generation: {prediction}",
                        content=prediction,
                        summary=prediction,
                        confidence=confidence,
                        priority="medium",
                        innovation_score=confidence,
                        stage=ProposalStage.PENDING_BUSINESS,
                        proposal_type=self._proposal_type_for_method(InnovationMethod.TRIZ),
                        framework_used="TRIZ",
                        metadata={
                            "framework": "TRIZ",
                            "s_curve_position": getattr(evolution, "s_curve_position", ""),
                            "evolution_trends": getattr(evolution, "evolution_trends", []),
                        },
                    )
                )

        return proposals

    async def _persist_framework_proposals(self, proposals: list[Proposal]) -> None:
        """Persist Reactor-generated proposals into GraphDB for entrepreneur review."""
        if not self.graph_db:
            return

        for proposal in proposals:
            if proposal.framework_used == "Reactor-Residual":
                continue
            try:
                await self.graph_db.insert_node(
                    node_id=proposal.id,
                    node_type="reactor_proposal",
                    label=f"{proposal.title}: {proposal.summary or proposal.content}",
                    source="reactor",
                )
                await self.graph_db.set_node_property(proposal.id, "status", "pending_business")
                await self.graph_db.set_node_property(proposal.id, "title", proposal.title)
                await self.graph_db.set_node_property(proposal.id, "description", proposal.content)
                await self.graph_db.set_node_property(proposal.id, "summary", proposal.summary)
                await self.graph_db.set_node_property(proposal.id, "confidence", proposal.confidence)
                await self.graph_db.set_node_property(proposal.id, "innovation_score", proposal.innovation_score or proposal.confidence)
                await self.graph_db.set_node_property(proposal.id, "framework_used", proposal.framework_used)
                await self.graph_db.set_node_property(proposal.id, "priority", proposal.priority)
                await self.graph_db.set_node_property(proposal.id, "proposal_type", proposal.proposal_type.name.lower())
                await self.graph_db.set_node_property(proposal.id, "stage", "pending_business")
                await self.graph_db.set_node_property(proposal.id, "source_knowledge_ids", proposal.source_knowledge_ids)
                for key, value in proposal.metadata.items():
                    await self.graph_db.set_node_property(proposal.id, key, value)
                for idx, source_id in enumerate(proposal.source_knowledge_ids):
                    await self.graph_db.insert_edge(
                        edge_id=f"reactor_{proposal.id}_{idx}",
                        source_node_id=proposal.id,
                        target_node_id=source_id,
                        relation_type="informed_by",
                        weight=proposal.confidence or 0.5,
                        source="reactor",
                    )
            except Exception as exc:
                logger.warning("[Reactor] Failed to persist proposal %s: %s", proposal.title, exc)

    async def _evaluate_residual_proposals(self, max_nodes: int | None = None) -> list[Proposal]:
        """Score and gate Residual proposals through innovation screening."""
        if not self.graph_db:
            return []

        try:
            nodes_by_id: dict[str, dict] = {}
            for status in RESIDUAL_PROPOSAL_REACTOR_STATUSES:
                for node in await self.graph_db.get_nodes_by_property(
                    "residual_proposal", "status", status
                ):
                    nodes_by_id[node["id"]] = node
            nodes = list(nodes_by_id.values())
        except Exception as e:
            logger.warning(f"[Reactor] Failed to query residual proposals: {e}")
            return []

        pending = nodes[:max_nodes] if max_nodes is not None else nodes

        if not pending:
            logger.info("[Reactor] No pending Residual proposals to evaluate")
            return []

        logger.info(f"[Reactor] Evaluating {len(pending)} Residual proposals")
        approved: list[Proposal] = []

        for node in pending:
            score = await self._score_proposal(node, budget=None)
            node_id = node["id"]
            await self.graph_db.set_node_property(node_id, "innovation_score_details", score)
            if score.get("pass", False):
                await self.graph_db.set_node_property(node_id, "status", "pending_business")
                await self.graph_db.set_node_property(
                    node_id, "innovation_score", score.get("confidence", 0.0)
                )
                approved.append(self._node_to_proposal(node, score))
            else:
                await self.graph_db.set_node_property(node_id, "status", "rejected_innovation")
                await self.graph_db.set_node_property(
                    node_id, "rejection_reason", score.get("reason", "failed_innovation_screening")
                )

        logger.info(f"[Reactor] Approved {len(approved)}/{len(pending)} Residual proposals")
        return approved

    async def _score_proposal(
        self,
        node: dict,
        budget: Any | None = None,
    ) -> dict:
        """Use LLM to score a Residual proposal for novelty and feasibility."""
        label = node.get("label", "")
        props = node.get("properties", {}) or {}
        prompt = f"""Evaluate this proposal for innovation potential using the structured business and technical brief, not only the title.

Proposal Label: {label}
Title: {props.get("title", "")}
Description: {props.get("description", "")}
Hypothesis: {props.get("hypothesis", "")}
Problem: {props.get("problem", "")}
Solution: {props.get("solution", "")}
Target User: {props.get("target_user", "")}
Economic Buyer: {props.get("economic_buyer", "")}
Current Workaround: {props.get("current_workaround", "")}
Adoption Wedge: {props.get("adoption_wedge", "")}
Workflow Insertion: {props.get("workflow_insertion", "")}
Integration Boundary: {props.get("integration_boundary", "")}
Proof Artifact: {props.get("proof_artifact", "")}
Success Metric: {props.get("success_metric", "")}
Recommended Next Validation: {props.get("recommended_next_validation", "")}
Risks: {props.get("risks", [])}

Scoring dimensions:
1. novelty (0.0-1.0) — is the idea materially differentiated?
2. feasibility (0.0-1.0) — can it be built and piloted with realistic effort?
3. confidence (0.0-1.0) — overall confidence after considering the whole brief
4. source_grounding (0.0-1.0) — does the proposal feel anchored in real workflow evidence?
5. buyer_clarity (0.0-1.0) — are the user, champion, and economic buyer sufficiently concrete?
6. workflow_insertion_clarity (0.0-1.0) — is it clear where this fits into the workflow?
7. validation_readiness (0.0-1.0) — is there a credible next proof artifact or validation step?

Evaluation rules:
- Reward concrete buyer/workflow/proof structure.
- Penalize vague platform language, missing proof paths, or missing workflow insertion points.
- For EDA and deep-tech ideas, narrow wedges are acceptable if workflow value and proof artifacts are credible.

A proposal PASSES if:
- novelty >= {self.nozzle_config.min_novelty_score}
- feasibility >= {self.nozzle_config.min_feasibility_score}
- confidence >= {self.nozzle_config.min_confidence}

Return JSON:
{{
    "novelty": 0.0-1.0,
    "feasibility": 0.0-1.0,
    "confidence": 0.0-1.0,
    "source_grounding": 0.0-1.0,
    "buyer_clarity": 0.0-1.0,
    "workflow_insertion_clarity": 0.0-1.0,
    "validation_readiness": 0.0-1.0,
    "pass": true|false,
    "reason": "Why it passed or failed",
    "strengths": ["specific strength"],
    "gaps": ["specific gap"]
}}"""
        try:
            result = await chat_json(
                llm_client=self.llm_client,
                stage="reactor_score",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                budget=budget,
            )
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning(f"[Reactor] Proposal scoring failed (attempt 1): {e}")
            try:
                await asyncio.sleep(1.0)
                result = await chat_json(
                    llm_client=self.llm_client,
                    stage="reactor_score",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    budget=budget,
                )
                if isinstance(result, dict):
                    return result
            except Exception as retry_e:
                logger.warning(f"[Reactor] Proposal scoring failed on retry: {retry_e}")
        return {
            "novelty": 0.0,
            "feasibility": 0.0,
            "confidence": 0.0,
            "pass": False,
            "reason": "scoring_error",
        }

    def _node_to_proposal(self, node: dict, score: dict) -> Proposal:
        """Convert a Residual proposal node to a Proposal object."""
        props = node.get("properties", {}) or {}
        label = node.get("label", "")
        parsed_title = label.split(":")[0] if ":" in label else label
        parsed_description = label[len(parsed_title) + 1 :].strip() if ":" in label else label
        title = props.get("title") or parsed_title
        description = (
            props.get("description")
            or props.get("problem")
            or props.get("summary")
            or parsed_description
        )
        metadata = {
            key: value
            for key, value in props.items()
            if value not in ("", None, [], {})
        }
        return Proposal(
            id=f"reactor_{node['id']}",
            mind_name="reactor",
            title=title,
            content=description,
            summary=description[:200],
            confidence=score.get("confidence", 0.0),
            priority="high" if score.get("confidence", 0) >= 0.8 else "medium",
            innovation_score=score.get("confidence", 0.0),
            stage=ProposalStage.PENDING_BUSINESS,
            source_knowledge_ids=[node["id"]],
            proposal_type=self._proposal_type_for_method(InnovationMethod.FIRST_PRINCIPLES),
            framework_used="Reactor-Residual",
            metadata=metadata,
        )

    def _proposal_type_for_method(self, method: InnovationMethod) -> Any:
        """Map innovation method to proposal type."""
        from aily.sessions.models import ProposalType
        mapping = {
            InnovationMethod.TRIZ: ProposalType.INNOVATION,
            InnovationMethod.SIT: ProposalType.INNOVATION,
            InnovationMethod.SIX_HATS: ProposalType.SYNTHESIS,
            InnovationMethod.BIOMIMICRY: ProposalType.INNOVATION,
            InnovationMethod.MORPHOLOGICAL: ProposalType.INNOVATION,
            InnovationMethod.BLUE_OCEAN: ProposalType.BUSINESS,
            InnovationMethod.SCAMPER: ProposalType.INNOVATION,
            InnovationMethod.FIRST_PRINCIPLES: ProposalType.INNOVATION,
        }
        return mapping.get(method, ProposalType.INNOVATION)

    async def _gather_context(self) -> dict[str, Any]:
        """Gather context from GraphDB for innovation analysis."""
        context = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "focus_areas": ["eda", "ai", "semiconductor", "software"],  # fallback
            "recent_insights": [],
        }

        # Try to get recent insights and dynamic focus areas from GraphDB
        try:
            if self.graph_db:
                # Get recent nodes as insights
                nodes = await self.graph_db.get_recent_nodes(limit=20)
                context["recent_insights"] = [
                    {"id": n.get("id", ""), "label": n.get("label", ""), "type": n.get("type", "")}
                    for n in nodes
                ]
                # Build dynamic focus areas from recent node types (deduplicated, preserving order)
                recent_types: list[str] = []
                for n in nodes:
                    node_type = n.get("type", "")
                    if node_type and node_type not in recent_types:
                        recent_types.append(node_type)
                if recent_types:
                    context["focus_areas"] = recent_types
        except Exception as e:
            logger.debug(f"Could not gather GraphDB context: {e}")

        return context

    async def _output_proposals(self, proposals: list[Proposal], start: datetime) -> None:
        """Output proposals to Obsidian and Feishu."""
        if not proposals:
            logger.info("No proposals generated in this session")
            return

        logger.info(f"Generated {len(proposals)} proposals")

        # Write to Obsidian
        if self.obsidian_writer:
            try:
                date_str = start.strftime("%Y-%m-%d")
                for i, proposal in enumerate(proposals):
                    await self._write_proposal_to_obsidian(proposal, date_str, i)
            except Exception as e:
                logger.warning(f"Failed to write proposals to Obsidian: {e}")

        # Send to Feishu
        if self.feishu_pusher and proposals:
            try:
                top_proposals = proposals[:3]
                message = self._format_proposals_message(top_proposals, start)
                # Feishu pusher implementation here
            except Exception as e:
                logger.warning(f"Failed to send Feishu notification: {e}")

    async def _write_proposal_to_obsidian(self, proposal: Proposal, date_str: str, index: int) -> None:
        """Write a single proposal to Obsidian."""
        await self.obsidian_writer.write_note(
            title=f"Innovation: {proposal.title}",
            markdown=proposal.to_markdown(),
            source_url=f"aily://reactor/{date_str}/{index}",
        )

    def _format_proposals_message(self, proposals: list[Proposal], start: datetime) -> str:
        """Format proposals for Feishu message."""
        lines = [f"🚀 **Reactor Innovation Report** ({start.strftime('%Y-%m-%d %H:%M')})", ""]
        for i, p in enumerate(proposals, 1):
            lines.append(f"**{i}. {p.title}** (confidence: {p.confidence:.0%})")
            preview = p.summary or p.content
            lines.append(f"{preview[:200]}...")
            lines.append("")
        return "\n".join(lines)

    def get_current_proposals(self) -> list[Proposal]:
        """Return proposals from the most recent session."""
        return self._current_session_proposals

    def add_method(self, method: InnovationMethod) -> None:
        """Enable a new innovation method."""
        self.nozzle_config.enabled_methods.add(method)
        logger.info(f"Added {method.value} to Reactor")

    def remove_method(self, method: InnovationMethod) -> None:
        """Disable an innovation method."""
        self.nozzle_config.enabled_methods.discard(method)
        logger.info(f"Removed {method.value} from Reactor")

    def list_methods(self) -> dict[str, bool]:
        """List all methods and their status."""
        return {
            method.value: method in self.nozzle_config.enabled_methods
            for method in InnovationMethod
        }
