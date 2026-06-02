"""Entrepreneur Mind - Agentic GStack evaluation through execution.

Runs daily at 9am.
Acts like Garry Tan - actually pulls up code, runs tests, checks metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from aily.sessions.base import BaseMindScheduler
from aily.sessions.models import Proposal, ProposalType, ProposalStatus, ProposalStage
from aily.sessions.gstack_agent import (
    GStackAgent,
    GStackPanelResult,
    GStackSession,
    GSTACK_PERSONAS,
)

logger = logging.getLogger(__name__)

PROPOSAL_CONTEXT_KEYS = (
    "title",
    "description",
    "summary",
    "hypothesis",
    "problem",
    "solution",
    "target_user",
    "economic_buyer",
    "current_workaround",
    "why_existing_tools_fail",
    "adoption_wedge",
    "pilot_design_partner",
    "integration_boundary",
    "integration_surface",
    "workflow_insertion",
    "workflow_trigger",
    "proof_artifact",
    "proof_of_value",
    "success_metric",
    "recommended_next_validation",
    "risks",
    "why_now",
    "killer_risk",
    "focus_areas",
)


class EntrepreneurScheduler(BaseMindScheduler):
    """Entrepreneur Mind that actually takes actions - evaluates by doing."""

    def __init__(
        self,
        llm_client: Any,
        graph_db: Any,
        gstack_llm_client: Any | None = None,
        guru_llm_client: Any | None = None,
        innovation_scheduler: Any | None = None,
        obsidian_writer: Any | None = None,
        feishu_pusher: Any | None = None,
        schedule_hour: int = 9,
        schedule_minute: int = 0,
        circuit_breaker_threshold: int = 3,
        enabled: bool = True,
        proposal_min_confidence: float = 0.7,
        proposal_max_per_session: int = 10,
        innovation_timeout_minutes: int = 30,
        tool_executor: Any | None = None,
        use_gstack_panel: bool = True,
    ) -> None:
        super().__init__(
            llm_client=llm_client,
            mind_name="entrepreneur",
            schedule_hour=schedule_hour,
            schedule_minute=schedule_minute,
            circuit_breaker_threshold=circuit_breaker_threshold,
            enabled=enabled,
        )
        self.graph_db = graph_db
        self.innovation_scheduler = innovation_scheduler
        self.obsidian_writer = obsidian_writer
        self.feishu_pusher = feishu_pusher
        self.proposal_min_confidence = proposal_min_confidence
        self.proposal_max_per_session = proposal_max_per_session
        self.innovation_timeout_minutes = innovation_timeout_minutes
        self.use_gstack_panel = use_gstack_panel

        # Initialize GStack Agent - actually takes actions
        self.gstack_agent = GStackAgent(
            llm_client=gstack_llm_client or llm_client,
            guru_llm_client=guru_llm_client,
            tool_executor=tool_executor,
            obsidian_writer=obsidian_writer,
            graph_db=graph_db,
            request_timeout_seconds=min(
                120.0,
                max(30.0, float(innovation_timeout_minutes) * 15.0),
            ),
            guru_timeout_seconds=min(
                180.0,
                max(45.0, float(innovation_timeout_minutes) * 20.0),
            ),
        )

    async def _run_session(self) -> dict[str, Any]:
        """Execute GStack agentic evaluation - actually takes actions."""
        logger.info("[Entrepreneur] Starting GStack evaluation with real actions")

        # Step 1: Gather pending business proposals from Residual/Reactor
        pending_proposals = await self._query_pending_business_proposals()
        innovation_proposals = self._get_innovation_proposals()

        logger.info(
            "[Entrepreneur] Evaluating %d pending business proposals",
            len(pending_proposals),
        )

        approved_proposals: list[Proposal] = []
        total_actions = 0

        # Step 2: Evaluate each pending proposal with GStack
        for proposal_node in pending_proposals[: self.proposal_max_per_session]:
            hypothesis = self._build_hypothesis_from_node(proposal_node)
            context = self._build_gstack_context(
                proposal_node=proposal_node,
                hypothesis=hypothesis,
                innovation_proposals=innovation_proposals,
            )
            evaluation_result = await self._evaluate_business_case(hypothesis, context)
            total_actions += self._count_evaluation_actions(evaluation_result)

            # Step 3: Process verdict and update GraphDB
            if isinstance(evaluation_result, GStackPanelResult):
                entrepreneur_proposal = await self._process_gstack_panel_verdict(
                    proposal_node, evaluation_result
                )
            else:
                entrepreneur_proposal = await self._process_gstack_verdict(
                    proposal_node, evaluation_result
                )

            guru_appendix_markdown = await self._build_guru_appendix_markdown(
                proposal_node=proposal_node,
                panel_or_session=evaluation_result,
                innovation_proposals=innovation_proposals,
            )
            await self._write_proposal_note(
                proposal_node,
                evaluation_result,
                approved=entrepreneur_proposal is not None,
                guru_appendix_markdown=guru_appendix_markdown,
            )

            if entrepreneur_proposal:
                approved_proposals.append(entrepreneur_proposal)

        if approved_proposals:
            await self._deliver_proposals(approved_proposals)

        # Step 4: Write session summary report to Obsidian
        if self.obsidian_writer:
            try:
                summary_lines = [
                    f"# Entrepreneur Session {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                    "",
                    f"Evaluated: {len(pending_proposals)} proposals",
                    f"Approved: {len(approved_proposals)} proposals",
                    "",
                    "## Approved",
                ]
                for p in approved_proposals:
                    summary_lines.append(f"- **{p.title}** (confidence: {p.confidence:.0%})")
                summary_lines.append("")
                summary_lines.append("## Rejected")
                for node in pending_proposals:
                    props = node.get("properties", {})
                    if props.get("status") in ("rejected_business", "rejected_innovation"):
                        summary_lines.append(f"- {node['label']} — {props.get('rejection_reason', '')}")

                await self.obsidian_writer.write_note(
                    title=f"Entrepreneur Session {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                    markdown="\n".join(summary_lines),
                    source_url="aily://entrepreneur_session",
                )
            except Exception as e:
                logger.warning("[Entrepreneur] Failed to write session report: %s", e)

        return {
            "proposals_generated": len(approved_proposals),
            "actions_taken": total_actions,
            "evaluated": len(pending_proposals),
        }

    async def _evaluate_business_case(
        self,
        hypothesis: dict[str, Any],
        context: dict[str, Any],
    ) -> GStackPanelResult | GStackSession:
        """Run GStack with bounded time and degrade gracefully on slow evaluations."""
        timeout_seconds = max(60.0, float(self.innovation_timeout_minutes) * 60.0)

        try:
            if self.use_gstack_panel:
                return await asyncio.wait_for(
                    self.gstack_agent.evaluate_panel(
                        hypothesis=hypothesis["hypothesis"],
                        problem=hypothesis["problem"],
                        solution=hypothesis["solution"],
                        target_user=hypothesis["target_user"],
                        context=context,
                    ),
                    timeout=timeout_seconds,
                )
            return await asyncio.wait_for(
                self.gstack_agent.evaluate(
                    hypothesis=hypothesis["hypothesis"],
                    problem=hypothesis["problem"],
                    solution=hypothesis["solution"],
                    target_user=hypothesis["target_user"],
                    context=context,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[Entrepreneur] GStack evaluation timed out after %.1fs for %s",
                timeout_seconds,
                hypothesis.get("title", "untitled"),
            )

        fallback_timeout = min(90.0, max(20.0, timeout_seconds / 3.0))
        try:
            session = await asyncio.wait_for(
                self.gstack_agent.evaluate(
                    hypothesis=hypothesis["hypothesis"],
                    problem=hypothesis["problem"],
                    solution=hypothesis["solution"],
                    target_user=hypothesis["target_user"],
                    context=context,
                    persona="general",
                ),
                timeout=fallback_timeout,
            )
            if self.use_gstack_panel:
                return GStackPanelResult(
                    sessions=[session],
                    final_verdict=session.verdict or "needs_more_validation",
                    final_confidence=session.confidence,
                    synthesis_reasoning=(
                        "Panel timed out; fell back to a single general-partner review."
                    ),
                    split_verdict=False,
                )
            return session
        except asyncio.TimeoutError:
            logger.warning(
                "[Entrepreneur] Fallback GStack evaluation timed out after %.1fs for %s",
                fallback_timeout,
                hypothesis.get("title", "untitled"),
            )
        except Exception as exc:
            logger.warning("[Entrepreneur] Fallback GStack evaluation failed: %s", exc)

        timeout_session = self._build_timeout_session(hypothesis)
        if self.use_gstack_panel:
            return GStackPanelResult(
                sessions=[timeout_session],
                final_verdict="needs_more_validation",
                final_confidence=timeout_session.confidence,
                synthesis_reasoning=(
                    "Evaluation timed out before enough evidence was collected. "
                    "Kept the proposal in validation instead of hanging the pipeline."
                ),
                split_verdict=False,
            )
        return timeout_session

    @staticmethod
    def _count_evaluation_actions(evaluation_result: GStackPanelResult | GStackSession) -> int:
        """Count persisted GStack actions regardless of evaluation mode."""
        if isinstance(evaluation_result, GStackPanelResult):
            return sum(len(session.actions) for session in evaluation_result.sessions)
        return len(evaluation_result.actions)

    @staticmethod
    def _build_timeout_session(hypothesis: dict[str, Any]) -> GStackSession:
        """Create a synthetic session so stalled evaluations still leave a usable record."""
        session = GStackSession(
            session_id=f"gstack_timeout_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            hypothesis=hypothesis.get("hypothesis", ""),
            problem=hypothesis.get("problem", ""),
            solution=hypothesis.get("solution", ""),
            target_user=hypothesis.get("target_user", ""),
            persona="general",
            verdict="needs_more_validation",
            confidence=0.2,
        )
        session.add_action(
            action="evaluation_timeout",
            status="error",
            output="GStack evaluation timed out before a full review completed.",
        )
        session.key_findings.append(
            "Evaluation was time-bounded to keep the business pipeline responsive."
        )
        session.blockers.append(
            "Need a shorter validation pass or smaller prompt context before a full committee review."
        )
        session.complete("needs_more_validation", 0.2)
        return session

    def _get_innovation_proposals(self) -> list[dict]:
        """Get proposals from Innovation Mind."""
        if not self.innovation_scheduler:
            return []
        try:
            proposals = getattr(self.innovation_scheduler, '_current_session_proposals', [])
            formatted: list[dict] = []
            for proposal in proposals:
                item = {
                    "title": proposal.title,
                    "description": proposal.summary or proposal.content,
                    "confidence": proposal.confidence,
                }
                metadata = getattr(proposal, "metadata", {}) or {}
                for key in PROPOSAL_CONTEXT_KEYS:
                    value = metadata.get(key)
                    if value not in ("", None, [], {}):
                        item[key] = value
                formatted.append(item)
            return formatted
        except Exception:
            return []

    async def _deliver_proposals(self, proposals: list[Proposal]) -> None:
        """Deliver proposals."""
        for proposal in proposals:
            if self.obsidian_writer:
                try:
                    await self.obsidian_writer.write_note(
                        title=f"Business: {proposal.title}",
                        markdown=f"# {proposal.title}\n\n{proposal.content}",
                        source_url="aily://entrepreneur",
                    )
                    proposal.status = ProposalStatus.DELIVERED
                except Exception as e:
                    logger.warning("[Entrepreneur] Obsidian write failed: %s", e)

    async def _query_pending_business_proposals(self) -> list[dict]:
        """Query proposals waiting for business evaluation."""
        if not self.graph_db:
            return []
        try:
            nodes_by_id: dict[str, dict] = {}
            for node_type in ("residual_proposal", "reactor_proposal"):
                for node in await self.graph_db.get_nodes_by_property(
                    node_type, "status", "pending_business"
                ):
                    nodes_by_id[node["id"]] = node
            return list(nodes_by_id.values())
        except Exception as e:
            logger.exception("[Entrepreneur] Failed to query pending business proposals: %s", e)
            return []

    def _build_hypothesis_from_node(self, node: dict) -> dict:
        """Build a GStack hypothesis from a Residual proposal node."""
        props = node.get("properties", {}) or {}
        label = node.get("label", "")
        parsed_title = label.split(":")[0] if ":" in label else label
        parsed_description = label[len(parsed_title) + 1 :].strip() if ":" in label else label
        title = props.get("title") or parsed_title or "Business Opportunity"
        description = (
            props.get("description")
            or props.get("summary")
            or props.get("problem")
            or parsed_description
        )
        problem = props.get("problem") or description or "Problem not clearly defined"
        solution = props.get("solution") or description or "Solution not clearly defined"
        target_user = props.get("target_user") or "Users who face this problem"
        hypothesis = props.get("hypothesis") or f"Building {title} will solve a real problem for {target_user}"
        return {
            "title": title,
            "description": description,
            "summary": props.get("summary", ""),
            "hypothesis": hypothesis,
            "problem": problem,
            "solution": solution,
            "target_user": target_user,
            "economic_buyer": props.get("economic_buyer", ""),
            "current_workaround": props.get("current_workaround", ""),
            "why_existing_tools_fail": props.get("why_existing_tools_fail", ""),
            "adoption_wedge": props.get("adoption_wedge", ""),
            "pilot_design_partner": props.get("pilot_design_partner", ""),
            "integration_boundary": props.get("integration_boundary", ""),
            "integration_surface": props.get("integration_surface", ""),
            "workflow_insertion": props.get("workflow_insertion", ""),
            "workflow_trigger": props.get("workflow_trigger", ""),
            "proof_artifact": props.get("proof_artifact", ""),
            "proof_of_value": props.get("proof_of_value", ""),
            "success_metric": props.get("success_metric", ""),
            "recommended_next_validation": props.get("recommended_next_validation", ""),
            "risks": props.get("risks", []),
            "why_now": props.get("why_now", ""),
            "killer_risk": props.get("killer_risk", ""),
            "focus_areas": props.get("focus_areas", []),
        }

    def _build_gstack_context(
        self,
        proposal_node: dict,
        hypothesis: dict[str, Any],
        innovation_proposals: list[dict],
    ) -> dict[str, Any]:
        """Build the full proposal context for GStack actions."""
        props = proposal_node.get("properties", {}) or {}
        context = {
            "knowledge": [],
            "innovation_proposals": innovation_proposals,
            "session_type": "entrepreneur",
            "proposal_node_id": proposal_node["id"],
        }
        for key, value in props.items():
            if value not in ("", None, [], {}):
                context[key] = value
        for key, value in hypothesis.items():
            if value not in ("", None, [], {}):
                context[key] = value
        return context

    @staticmethod
    def _append_proposal_context(lines: list[str], hypothesis: dict[str, Any]) -> None:
        """Append a structured proposal brief to a markdown note."""
        fields = (
            ("Hypothesis", hypothesis.get("hypothesis", "")),
            ("Problem", hypothesis.get("problem", "")),
            ("Solution", hypothesis.get("solution", "")),
            ("Target User", hypothesis.get("target_user", "")),
            ("Economic Buyer", hypothesis.get("economic_buyer", "")),
            ("Current Workaround", hypothesis.get("current_workaround", "")),
            ("Adoption Wedge", hypothesis.get("adoption_wedge", "")),
            ("Workflow Insertion", hypothesis.get("workflow_insertion", "")),
            ("Integration Boundary", hypothesis.get("integration_boundary", "")),
            ("Proof Artifact", hypothesis.get("proof_artifact", "")),
            ("Recommended Next Validation", hypothesis.get("recommended_next_validation", "")),
        )
        lines.extend(["## Proposal Brief", ""])
        for label, value in fields:
            if value not in ("", None, [], {}):
                lines.append(f"- **{label}:** {value}")
        risks = hypothesis.get("risks", [])
        if risks:
            lines.append("- **Key Risks:** " + "; ".join(str(risk) for risk in risks))
        lines.append("")

    @staticmethod
    def _evaluation_result_meta(panel_or_session: Any) -> dict[str, Any]:
        """Extract verdict metadata from a GStack result."""
        if isinstance(panel_or_session, GStackPanelResult):
            return {
                "verdict": panel_or_session.final_verdict,
                "confidence": panel_or_session.final_confidence,
                "reasoning": panel_or_session.synthesis_reasoning,
            }
        return {
            "verdict": getattr(panel_or_session, "verdict", "needs_more_validation"),
            "confidence": getattr(panel_or_session, "confidence", 0.0),
            "reasoning": "",
        }

    @staticmethod
    def _markdown_value(value: Any) -> str:
        """Render arbitrary LLM JSON values as safe markdown text."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)) or value is None:
            return str(value)
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _markdown_list_item(value: Any) -> str:
        """Render arbitrary LLM JSON values as one bullet-safe line."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)) or value is None:
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _render_guru_appendix(title: str, guru_plan: dict[str, Any], evaluation_meta: dict[str, Any]) -> str:
        """Render the Guru appendix as markdown."""
        lines = [
            f"# Guru Appendix: {title}",
            "",
            f"**GStack Verdict:** {evaluation_meta.get('verdict', 'needs_more_validation')}",
            f"**Confidence:** {evaluation_meta.get('confidence', 0.0):.0%}",
        ]
        if evaluation_meta.get("reasoning"):
            lines.extend(["", "## GStack Framing", "", str(evaluation_meta["reasoning"])])

        executive_take = guru_plan.get("executive_take", "")
        if executive_take:
            lines.extend(["", "## Executive Take", "", EntrepreneurScheduler._markdown_value(executive_take)])

        decision_posture = guru_plan.get("decision_posture", "")
        if decision_posture:
            lines.extend(["", f"**Decision Posture:** {EntrepreneurScheduler._markdown_list_item(decision_posture)}"])

        fact_base = guru_plan.get("fact_base", [])
        if fact_base:
            lines.extend(["", "## Fact Base", ""])
            for item in fact_base:
                if isinstance(item, dict):
                    item_type = EntrepreneurScheduler._markdown_list_item(item.get("type", "fact"))
                    statement = EntrepreneurScheduler._markdown_list_item(item.get("statement", ""))
                    implication = EntrepreneurScheduler._markdown_list_item(item.get("implication", ""))
                    lines.append(f"- **{item_type}:** {statement} -> {implication}")
                else:
                    lines.append(f"- {EntrepreneurScheduler._markdown_list_item(item)}")

        business_plan = guru_plan.get("business_plan", {})
        if business_plan:
            lines.extend(["", "## Hypothesis-Driven Business Plan", ""])
            for heading, key in (
                ("Core Thesis", "core_thesis"),
                ("Market Logic", "market_logic"),
                ("Customer And Buyer Map", "customer_and_buyer_map"),
                ("Wedge And Positioning", "wedge_and_positioning"),
                ("Commercial Motion", "commercial_motion"),
            ):
                value = business_plan.get(key, "")
                if value:
                    lines.extend([f"### {heading}", "", EntrepreneurScheduler._markdown_value(value), ""])
            for heading, key in (
                ("Validation Program", "validation_program"),
                ("Decision Gates", "decision_gates"),
                ("Salvage Or Acceleration", "salvage_or_acceleration"),
            ):
                entries = business_plan.get(key, [])
                if entries:
                    lines.extend([f"### {heading}", ""])
                    for entry in entries:
                        lines.append(f"- {EntrepreneurScheduler._markdown_list_item(entry)}")
                    lines.append("")

        development_plan = guru_plan.get("development_plan", {})
        if development_plan:
            lines.extend(["## Simulation-Driven Development Plan", ""])
            for heading, key in (
                ("Technical Thesis", "technical_thesis"),
                ("System Boundary", "system_boundary"),
                ("Architecture Outline", "architecture_outline"),
            ):
                value = development_plan.get(key, "")
                if value:
                    lines.extend([f"### {heading}", "", EntrepreneurScheduler._markdown_value(value), ""])
            for heading, key in (
                ("Simulation Program", "simulation_program"),
                ("Constraints", "constraints"),
                ("Feedback Loops", "feedback_loops"),
                ("Milestones", "milestones"),
                ("Team And Dependencies", "team_and_dependencies"),
                ("Kill Criteria", "kill_criteria"),
            ):
                entries = development_plan.get(key, [])
                if entries:
                    lines.extend([f"### {heading}", ""])
                    for entry in entries:
                        lines.append(f"- {EntrepreneurScheduler._markdown_list_item(entry)}")
                    lines.append("")

        briefing_notes = guru_plan.get("briefing_notes", {})
        if briefing_notes:
            lines.extend(["## Briefing Notes", ""])
            if briefing_notes.get("ceo"):
                lines.extend(["### CEO", "", EntrepreneurScheduler._markdown_value(briefing_notes["ceo"]), ""])
            if briefing_notes.get("cto"):
                lines.extend(["### CTO", "", EntrepreneurScheduler._markdown_value(briefing_notes["cto"]), ""])

        return "\n".join(lines).rstrip() + "\n"

    async def _build_guru_appendix_markdown(
        self,
        proposal_node: dict,
        panel_or_session: Any,
        innovation_proposals: list[dict],
    ) -> str | None:
        """Build the Guru planning appendix markdown for embedding in the plan note."""
        try:
            hypothesis = self._build_hypothesis_from_node(proposal_node)
            context = self._build_gstack_context(
                proposal_node=proposal_node,
                hypothesis=hypothesis,
                innovation_proposals=innovation_proposals,
            )
            guru_plan = await self.gstack_agent.generate_guru_plan(context, panel_or_session)
            evaluation_meta = self._evaluation_result_meta(panel_or_session)
            title = hypothesis["title"]
            return self._render_guru_appendix(title, guru_plan, evaluation_meta)
        except Exception as e:
            logger.warning("[Entrepreneur] Failed to build guru appendix: %s", e)
            return None

    async def _process_gstack_panel_verdict(
        self, proposal_node: dict, panel: GStackPanelResult
    ) -> Proposal | None:
        """Update proposal status based on GStack panel verdict and return Proposal if approved."""
        if not self.graph_db:
            return None

        node_id = proposal_node["id"]
        verdict = panel.final_verdict
        confidence = panel.final_confidence
        props = proposal_node.get("properties", {})

        # Persist panel sessions to GraphDB for audit trail
        try:
            for session in panel.sessions:
                session_id = f"gstack_{session.persona}_{node_id[-8:]}"
                await self.graph_db.insert_node(
                    node_id=session_id,
                    node_type="gstack_panel_session",
                    label=f"{session.persona}: {session.verdict} ({session.confidence:.0%})",
                    source="entrepreneur",
                )
                await self.graph_db.insert_edge(
                    edge_id=f"edge_{session.persona}_{node_id[-8:]}",
                    source_node_id=session_id,
                    target_node_id=node_id,
                    relation_type="evaluates",
                    weight=session.confidence,
                    source="entrepreneur",
                )
        except Exception as exc:
            logger.warning("[Entrepreneur] Failed to persist panel sessions: %s", exc)

        if verdict == "build_it" and confidence >= self.proposal_min_confidence:
            await self.graph_db.set_node_property(node_id, "status", "incubating")
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._create_incubation_task(proposal_node, panel)
            proposal_brief = self._build_hypothesis_from_node(proposal_node)
            proposal_metadata = {
                key: value
                for key, value in proposal_brief.items()
                if key != "title" and value not in ("", None, [], {})
            }
            return Proposal(
                id=f"biz_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{node_id[-4:]}",
                mind_name="entrepreneur",
                title=proposal_brief["title"],
                content=panel.synthesis_reasoning or panel.sessions[0].hypothesis,
                summary=panel.synthesis_reasoning or panel.sessions[0].hypothesis,
                confidence=confidence,
                proposal_type=ProposalType.BUSINESS,
                status=ProposalStatus.PENDING,
                priority="high" if confidence >= 0.85 else "medium",
                business_score=confidence,
                stage=ProposalStage.INCUBATING,
                source_knowledge_ids=[node_id],
                framework_used="GStack Panel",
                metadata={
                    **proposal_metadata,
                    "problem": panel.sessions[0].problem,
                    "solution": panel.sessions[0].solution,
                    "target_user": panel.sessions[0].target_user,
                    "verdict": verdict,
                    "panel_verdicts": panel.verdict_counts,
                    "split_verdict": panel.split_verdict,
                    "risks": panel.sessions[0].blockers,
                    "opportunities": panel.sessions[0].opportunities,
                    "actions_taken": sum(len(s.actions) for s in panel.sessions),
                    "key_findings": panel.sessions[0].key_findings,
                },
            )

        if verdict == "kill_it":
            reason = panel.synthesis_reasoning or "killed_by_gstack_panel"
            await self.graph_db.set_node_property(node_id, "status", "rejected_business")
            await self.graph_db.set_node_property(node_id, "rejection_reason", reason)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._write_rejection_feedback(proposal_node, reason)
            return None

        # needs_more_validation or pivot
        attempts = int(props.get("validation_attempts", 0)) + 1
        if attempts >= 2:
            reason = panel.synthesis_reasoning or "max_validation_attempts_reached"
            await self.graph_db.set_node_property(node_id, "status", "rejected_business")
            await self.graph_db.set_node_property(node_id, "rejection_reason", reason)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._write_rejection_feedback(proposal_node, reason)
        else:
            await self.graph_db.set_node_property(node_id, "validation_attempts", attempts)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)

        return None

    async def _process_gstack_verdict(
        self, proposal_node: dict, session: "GStackSession"
    ) -> Proposal | None:
        """Update proposal status based on GStack verdict and return Proposal if approved."""
        if not self.graph_db:
            return None

        node_id = proposal_node["id"]
        verdict = session.verdict
        confidence = session.confidence
        props = proposal_node.get("properties", {})

        if verdict == "build_it" and confidence >= self.proposal_min_confidence:
            await self.graph_db.set_node_property(node_id, "status", "incubating")
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._create_incubation_task(proposal_node, session)
            proposal_brief = self._build_hypothesis_from_node(proposal_node)
            proposal_metadata = {
                key: value
                for key, value in proposal_brief.items()
                if key != "title" and value not in ("", None, [], {})
            }
            return Proposal(
                id=f"biz_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{node_id[-4:]}",
                mind_name="entrepreneur",
                title=proposal_brief["title"],
                content=session.hypothesis,
                summary=session.hypothesis,
                confidence=confidence,
                proposal_type=ProposalType.BUSINESS,
                status=ProposalStatus.PENDING,
                priority="high" if confidence >= 0.85 else "medium",
                business_score=confidence,
                stage=ProposalStage.INCUBATING,
                source_knowledge_ids=[node_id],
                framework_used="GStack",
                metadata={
                    **proposal_metadata,
                    "problem": session.problem,
                    "solution": session.solution,
                    "target_user": session.target_user,
                    "verdict": session.verdict,
                    "risks": session.blockers,
                    "opportunities": session.opportunities,
                    "actions_taken": len(session.actions),
                    "key_findings": session.key_findings,
                },
            )

        if verdict == "kill_it":
            reason = session.blockers[-1] if session.blockers else "killed_by_gstack"
            await self.graph_db.set_node_property(node_id, "status", "rejected_business")
            await self.graph_db.set_node_property(node_id, "rejection_reason", reason)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._write_rejection_feedback(proposal_node, reason)
            return None

        # needs_more_validation or pivot
        attempts = int(props.get("validation_attempts", 0)) + 1
        if attempts >= 2:
            reason = "max_validation_attempts_reached"
            await self.graph_db.set_node_property(node_id, "status", "rejected_business")
            await self.graph_db.set_node_property(node_id, "rejection_reason", reason)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)
            await self._write_rejection_feedback(proposal_node, reason)
        else:
            await self.graph_db.set_node_property(node_id, "validation_attempts", attempts)
            await self.graph_db.set_node_property(node_id, "business_score", confidence)

        return None

    async def _write_rejection_feedback(self, proposal_node: dict, reason: str) -> None:
        """Append rejection reason to the Residual Feedback Index for future learning."""
        if not self.graph_db:
            return
        if proposal_node.get("type") != "residual_proposal":
            return
        try:
            label = proposal_node.get("label", "")
            await self.graph_db.add_residual_feedback(label, reason)
            logger.info("[Entrepreneur] Wrote rejection feedback for %s", proposal_node.get("id"))
        except Exception as e:
            logger.warning("[Entrepreneur] Failed to write rejection feedback: %s", e)

    async def _create_incubation_task(self, proposal_node: dict, panel_or_session: Any) -> None:
        """Create an incubation task note for approved proposals."""
        if not self.obsidian_writer:
            return
        try:
            hypothesis = self._build_hypothesis_from_node(proposal_node)
            title = hypothesis["title"]
            lines = [
                f"# Incubation Task: {title}",
                "",
            ]
            self._append_proposal_context(lines, hypothesis)

            # Handle both panel and single session
            if isinstance(panel_or_session, GStackPanelResult):
                lines.append(f"**Verdict:** {panel_or_session.final_verdict}")
                lines.append(f"**Confidence:** {panel_or_session.final_confidence:.0%}")
                lines.append("")
                lines.append("## Panel Verdicts")
                for session in panel_or_session.sessions:
                    lines.append(f"- **{session.persona}**: {session.verdict} ({session.confidence:.0%})")
                lines.append("")
                lines.append("## Synthesis Reasoning")
                lines.append(panel_or_session.synthesis_reasoning or "No reasoning provided.")
                lines.append("")
                key_findings = panel_or_session.sessions[0].key_findings if panel_or_session.sessions else []
                blockers = []
                for s in panel_or_session.sessions:
                    blockers.extend(s.blockers)
            else:
                session = panel_or_session
                lines.append(f"**Verdict:** {session.verdict}")
                lines.append(f"**Confidence:** {session.confidence:.0%}")
                lines.append("")
                key_findings = session.key_findings
                blockers = session.blockers

            lines.append("## Next Steps")
            for finding in key_findings:
                lines.append(f"- [ ] {finding}")
            lines.append("")
            lines.append("## Blockers to Resolve")
            for blocker in blockers:
                lines.append(f"- [ ] {blocker}")

            await self.obsidian_writer.write_note(
                title=f"Incubation: {title}",
                markdown="\n".join(lines),
                source_url="aily://entrepreneur_incubation",
            )
        except Exception as e:
            logger.warning("[Entrepreneur] Failed to create incubation task: %s", e)

    async def _write_proposal_note(
        self,
        proposal_node: dict,
        panel_or_session: Any,
        approved: bool,
        guru_appendix_markdown: str | None = None,
    ) -> None:
        """Write a detailed proposal note with full GStack reasoning to Obsidian."""
        if not self.obsidian_writer:
            return
        try:
            hypothesis = self._build_hypothesis_from_node(proposal_node)
            title = hypothesis["title"]
            prefix = "approved" if approved else "denied"
            note_title = f"{prefix}-{title}"

            lines = [f"# {title}", ""]
            self._append_proposal_context(lines, hypothesis)

            if isinstance(panel_or_session, GStackPanelResult):
                panel = panel_or_session
                lines.extend([
                    f"**Final Verdict:** {panel.final_verdict}",
                    f"**Final Confidence:** {panel.final_confidence:.0%}",
                    f"**Split Verdict:** {'Yes' if panel.split_verdict else 'No'}",
                    "",
                    "## Synthesis Reasoning",
                    "",
                    panel.synthesis_reasoning or "No synthesis reasoning provided.",
                    "",
                    "## Panel Deliberation",
                    "",
                ])
                for session in panel.sessions:
                    persona_label = GSTACK_PERSONAS.get(session.persona, {}).get("name", session.persona)
                    lines.extend([
                        f"### {persona_label}",
                        "",
                        f"- **Verdict:** {session.verdict}",
                        f"- **Confidence:** {session.confidence:.0%}",
                        "",
                        "#### Key Findings",
                    ])
                    for finding in session.key_findings:
                        lines.append(f"- {finding}")
                    lines.append("")
                    lines.append("#### Blockers")
                    for blocker in session.blockers:
                        lines.append(f"- {blocker}")
                    lines.append("")
                    lines.append("#### Opportunities")
                    for opp in session.opportunities:
                        lines.append(f"- {opp}")
                    lines.append("")
                    lines.append("#### Actions Taken")
                    for action in session.actions:
                        lines.append(f"- [{action.status}] {action.action}: {action.output}")
                    lines.append("")
            else:
                session = panel_or_session
                lines.extend([
                    f"**Verdict:** {session.verdict}",
                    f"**Confidence:** {session.confidence:.0%}",
                    "",
                    "## Reasoning & Findings",
                    "",
                    "#### Key Findings",
                ])
                for finding in session.key_findings:
                    lines.append(f"- {finding}")
                lines.append("")
                lines.append("#### Blockers")
                for blocker in session.blockers:
                    lines.append(f"- {blocker}")
                lines.append("")
                lines.append("#### Opportunities")
                for opp in session.opportunities:
                    lines.append(f"- {opp}")
                lines.append("")
                lines.append("#### Actions Taken")
                for action in session.actions:
                    lines.append(f"- [{action.status}] {action.action}: {action.output}")
                lines.append("")

            if guru_appendix_markdown:
                lines.extend(["", "---", "", guru_appendix_markdown.rstrip(), ""])

            await self.obsidian_writer.write_note(
                title=note_title,
                markdown="\n".join(lines),
                source_url="aily://entrepreneur",
            )
            logger.info("[Entrepreneur] Wrote %s proposal note: %s", prefix, note_title)
        except Exception as e:
            logger.warning("[Entrepreneur] Failed to write proposal note: %s", e)
