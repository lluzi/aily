"""GStack Agent - Business evaluation through actual execution.

The GStack mind doesn't just talk - it takes actions:
- Runs /qa to test the product
- Runs /ship to check deployment readiness
- Searches for competitive intelligence
- Actually validates assumptions through tools

Verdict: build_it | pivot | kill_it | needs_more_validation
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


GSTACK_PERSONAS: dict[str, dict[str, str]] = {
    "general": {
        "name": "General Partner",
        "prompt": (
            "You are chairing a high-standard investment and product review. "
            "Be direct and specific. Prefer concrete judgment over swagger. "
            "In deep-tech or enterprise markets, narrow wedges can be valid if the pain, buyer, and proof path are clear."
        ),
    },
    "ceo": {
        "name": "CEO / Founder",
        "prompt": (
            "You are a CEO evaluating a business opportunity. "
            "Challenge scope and ambition. Is this the right thing to build? "
            "Is the problem real, acute, and budget-worthy? Demand strong evidence. "
            "For enterprise and deep-tech ideas, care about workflow ownership, champion quality, and adoption path as much as market size. "
            "Be direct. No hedging."
        ),
    },
    "engineer": {
        "name": "Senior Engineer",
        "prompt": (
            "You are a senior engineer evaluating technical feasibility and execution risk. "
            "Look for architecture landmines, underestimated complexity, integration burden, validation burden, and team gaps. "
            "For EDA or semiconductor workflows, weigh signoff trust, insertion cost, and benchmarkability heavily. "
            "Be direct. No hedging."
        ),
    },
    "designer": {
        "name": "Product Designer",
        "prompt": (
            "You are a product designer evaluating workflow fit, usability, and differentiation. "
            "Ask whether the user experience is credible for the target workflow and whether the product earns adoption through clarity and trust. "
            "For enterprise and deep-tech ideas, focus on operator workflow, explainability, and friction to first value. "
            "Be direct. No hedging."
        ),
    },
    "market": {
        "name": "Market Analyst",
        "prompt": (
            "You are a competitive intelligence analyst. Who already does this? "
            "Why would anyone switch? What incumbent workflow or vendor is the real substitute? "
            "In deep-tech and EDA, a narrow market can still matter if annual value per customer is high and adoption can start with a credible wedge. "
            "Be direct. No hedging."
        ),
    },
    "guru": {
        "name": "Guru",
        "prompt": (
            "You are a guru-level business strategist and systems architect writing executive briefing appendices for a CEO and CTO. "
            "Produce hypothesis-driven, fact-based logical insight for the business plan and simulation-driven, constraint-based, feedback-evolving reasoning for the development plan. "
            "Be concrete, structured, and rigorous. Separate facts, inferences, and unknowns. "
            "Every idea deserves a serious salvage, validation, or execution path even when the current verdict is negative."
        ),
    },
}


@dataclass
class ActionResult:
    """Result of a GStack action."""

    action: str
    status: str  # success, failure, partial
    output: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GStackSession:
    """GStack evaluation session with actions taken."""

    session_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    # Business hypothesis being evaluated
    hypothesis: str = ""
    target_user: str = ""
    problem: str = ""
    solution: str = ""

    # Specialist persona that produced this evaluation
    persona: str = "general"

    # Actions taken
    actions: list[ActionResult] = field(default_factory=list)

    # Findings
    key_findings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)

    # Verdict
    verdict: str = ""  # build_it, pivot, kill_it, needs_more_validation
    confidence: float = 0.0

    def add_action(self, action: str, status: str, output: str, data: dict | None = None) -> None:
        self.actions.append(ActionResult(
            action=action,
            status=status,
            output=output,
            data=data or {},
        ))

    def complete(self, verdict: str, confidence: float) -> None:
        self.verdict = verdict
        self.confidence = confidence
        self.completed_at = datetime.now(timezone.utc)


@dataclass
class GStackPanelResult:
    """Result of a multi-persona GStack panel evaluation."""

    sessions: list[GStackSession]
    final_verdict: str = "needs_more_validation"
    final_confidence: float = 0.0
    synthesis_reasoning: str = ""
    split_verdict: bool = False

    @property
    def verdict_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for session in self.sessions:
            counts[session.verdict] = counts.get(session.verdict, 0) + 1
        return counts

    @property
    def average_confidence(self) -> float:
        if not self.sessions:
            return 0.0
        return sum(s.confidence for s in self.sessions) / len(self.sessions)


class GStackAgent:
    """GStack agent that evaluates through execution.

    Like talking to Garry Tan, but he actually pulls up the code,
    runs the tests, checks the metrics, and tells you the truth.
    """

    def __init__(
        self,
        llm_client: Any,
        guru_llm_client: Any | None = None,
        tool_executor: Callable | None = None,
        obsidian_writer: Any | None = None,
        graph_db: Any | None = None,
        request_timeout_seconds: float = 90.0,
        guru_timeout_seconds: float = 120.0,
    ) -> None:
        self.llm_client = llm_client
        self.guru_llm_client = guru_llm_client or llm_client
        self.tool_executor = tool_executor
        self.obsidian_writer = obsidian_writer
        self.graph_db = graph_db
        self.request_timeout_seconds = max(5.0, float(request_timeout_seconds))
        self.guru_timeout_seconds = max(self.request_timeout_seconds, float(guru_timeout_seconds))

        # Available GStack actions
        self.actions = {
            "analyze_codebase": self._analyze_codebase,
            "check_test_coverage": self._check_test_coverage,
            "search_market": self._search_market,
            "validate_problem": self._validate_problem,
            "assess_tech_risk": self._assess_tech_risk,
            "check_deployment_readiness": self._check_deployment_readiness,
        }

    async def _chat_json(
        self,
        *,
        llm_client: Any | None = None,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Bound LLM calls so GStack sessions degrade instead of hanging forever."""
        timeout = self.request_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        return await asyncio.wait_for(
            (llm_client or self.llm_client).chat_json(
                messages=messages,
                temperature=temperature,
            ),
            timeout=max(5.0, timeout),
        )

    @staticmethod
    def _is_deeptech_context(context: dict[str, Any] | None = None, text: str = "") -> bool:
        context = context or {}
        haystack_parts = [text]
        for key in ("hypothesis", "problem", "solution", "target_user"):
            value = context.get(key)
            if isinstance(value, str):
                haystack_parts.append(value)
        focus = context.get("focus_areas", [])
        if isinstance(focus, list):
            haystack_parts.extend(str(item) for item in focus)
        haystack = " ".join(haystack_parts).lower()
        keywords = (
            "eda",
            "semiconductor",
            "signoff",
            "timing",
            "verification",
            "physical design",
            "characterization",
            "rtl",
            "eco",
            "silicon",
            "enterprise workflow",
            "toolchain",
        )
        return any(word in haystack for word in keywords)

    def _persona_system_prompt(self, persona: str, context: dict[str, Any] | None = None) -> str:
        base_prompt = GSTACK_PERSONAS.get(persona, GSTACK_PERSONAS["general"])["prompt"]
        if self._is_deeptech_context(context):
            return (
                f"{base_prompt} "
                "This is likely a deep-tech, enterprise, EDA, or semiconductor evaluation. "
                "Judge it by workflow pain, insertion cost, signoff trust, benchmark delta, buyer clarity, and pilotability. "
                "Do not reject a narrow wedge merely because it is not a broad consumer market."
            )
        return base_prompt

    async def evaluate(
        self,
        hypothesis: str,
        problem: str,
        solution: str,
        target_user: str,
        context: dict[str, Any],
        persona: str = "general",
    ) -> GStackSession:
        """Execute GStack evaluation through actions."""

        session = GStackSession(
            session_id=f"gstack_{uuid.uuid4().hex[:8]}",
            hypothesis=hypothesis,
            problem=problem,
            solution=solution,
            target_user=target_user,
            persona=persona,
        )

        logger.info("[GStack Agent] Starting evaluation: %s (persona: %s)", hypothesis[:60], persona)

        # Action 1: Analyze codebase (only if explicitly requested)
        if context.get("evaluate_codebase"):
            await self._run_action(session, "analyze_codebase", context)
            await self._run_action(session, "check_test_coverage", context)

        # Action 2: Search for market/competitive intel
        await self._run_action(session, "search_market", context)

        # Action 3: Validate problem with real users/data
        await self._run_action(session, "validate_problem", context)

        # Action 4: Assess technical risks
        await self._run_action(session, "assess_tech_risk", context)

        # Action 5: Check if ready to ship (only if explicitly requested)
        if context.get("evaluate_codebase"):
            await self._run_action(session, "check_deployment_readiness", context)

        # Generate verdict based on findings
        verdict, confidence = await self._generate_verdict(session, persona)
        session.complete(verdict, confidence)

        logger.info("[GStack Agent] Verdict: %s (%.0f%% confidence, persona: %s)", verdict, confidence * 100, persona)

        return session

    async def evaluate_panel(
        self,
        hypothesis: str,
        problem: str,
        solution: str,
        target_user: str,
        context: dict[str, Any],
        personas: list[str] | None = None,
    ) -> GStackPanelResult:
        """Run a multi-persona GStack panel and synthesize a final verdict."""

        personas = personas or ["ceo", "engineer", "designer", "market"]
        logger.info("[GStack Panel] Starting panel evaluation with %d personas", len(personas))

        # Run all persona evaluations concurrently
        tasks = [
            self.evaluate(
                hypothesis=hypothesis,
                problem=problem,
                solution=solution,
                target_user=target_user,
                context=context,
                persona=persona,
            )
            for persona in personas
        ]
        sessions = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any failures gracefully
        valid_sessions: list[GStackSession] = []
        for session in sessions:
            if isinstance(session, Exception):
                logger.warning("[GStack Panel] Persona evaluation failed: %s", session)
                continue
            valid_sessions.append(session)

        if not valid_sessions:
            logger.error("[GStack Panel] All persona evaluations failed")
            return GStackPanelResult(sessions=[], final_verdict="needs_more_validation", final_confidence=0.0)

        # Synthesize panel into a single verdict
        return await self.synthesize_panel(valid_sessions, context)

    async def synthesize_panel(
        self,
        sessions: list[GStackSession],
        context: dict[str, Any],
    ) -> GStackPanelResult:
        """Synthesize multiple GStack sessions into a single verdict."""

        # Build panel summary
        panel_lines: list[str] = []
        for session in sessions:
            persona_label = GSTACK_PERSONAS.get(session.persona, {}).get("name", session.persona)
            panel_lines.append(
                f"=== {persona_label} ===\n"
                f"Verdict: {session.verdict}\n"
                f"Confidence: {session.confidence:.0%}\n"
                f"Key Findings: {', '.join(session.key_findings[:5]) or 'None'}\n"
                f"Blockers: {', '.join(session.blockers[:5]) or 'None'}\n"
                f"Opportunities: {', '.join(session.opportunities[:5]) or 'None'}"
            )

        verdict_counts: dict[str, int] = {}
        for session in sessions:
            verdict_counts[session.verdict] = verdict_counts.get(session.verdict, 0) + 1

        verdict_summary = "\n".join([f"- {v}: {c}" for v, c in verdict_counts.items()])
        split_verdict = len(verdict_counts) > 1

        prompt = f"""You are the senior partner chairing a GStack investment committee.

A panel of four specialists evaluated the same business hypothesis. Here are their independent verdicts:

{verdict_summary}

Detailed findings:
{chr(10).join(panel_lines)}

Hypothesis: {sessions[0].hypothesis}
Problem: {sessions[0].problem}
Solution: {sessions[0].solution}
Target User: {sessions[0].target_user}

Your job is to render a SINGLE final verdict that the firm will stand behind.
Rules:
- If the panel is split, side with the most bearish credible view unless one side has overwhelming evidence.
- Technical blockers from the engineer should weigh heavily.
- If confidence is low across the board, default to needs_more_validation.
- In deep-tech or EDA cases, do not kill the idea purely for being narrow; focus on buyer clarity, workflow pain, insertion cost, and proof path.
- Be direct. No hedging.

Return JSON:
{{
    "final_verdict": "build_it|pivot|kill_it|needs_more_validation",
    "final_confidence": 0.0-1.0,
    "reasoning": "Why this verdict given the panel disagreement",
    "critical_factors": ["factor 1", "factor 2"]
}}"""

        try:
            result = await self._chat_json(
                messages=[{
                    "role": "system",
                    "content": self._persona_system_prompt("general", context),
                }, {
                    "role": "user",
                    "content": prompt,
                }],
                temperature=0.5,
            )

            final_verdict = result.get("final_verdict", "needs_more_validation")
            final_confidence = result.get("final_confidence", 0.5)
            reasoning = result.get("reasoning", "")
        except Exception as e:
            logger.error("[GStack Panel] Synthesis failed: %s", e)
            # Fallback to majority vote
            if verdict_counts:
                final_verdict = max(verdict_counts, key=verdict_counts.get)  # type: ignore[arg-type]
            else:
                final_verdict = "needs_more_validation"
            final_confidence = sum(s.confidence for s in sessions) / len(sessions) if sessions else 0.0
            reasoning = f"Synthesis failed; fallback to plurality verdict."

        return GStackPanelResult(
            sessions=sessions,
            final_verdict=final_verdict,
            final_confidence=final_confidence,
            synthesis_reasoning=reasoning,
            split_verdict=split_verdict,
        )

    @staticmethod
    def _evaluation_summary(
        panel_or_session: GStackPanelResult | GStackSession,
    ) -> dict[str, Any]:
        """Normalize GStack evaluation results for downstream planning."""
        if isinstance(panel_or_session, GStackPanelResult):
            sessions_summary = []
            for session in panel_or_session.sessions:
                sessions_summary.append(
                    {
                        "persona": session.persona,
                        "verdict": session.verdict,
                        "confidence": session.confidence,
                        "key_findings": session.key_findings[:8],
                        "blockers": session.blockers[:8],
                        "opportunities": session.opportunities[:8],
                        "actions": [
                            {
                                "action": action.action,
                                "status": action.status,
                                "output": action.output[:240],
                            }
                            for action in session.actions[:8]
                        ],
                    }
                )
            return {
                "verdict": panel_or_session.final_verdict,
                "confidence": panel_or_session.final_confidence,
                "reasoning": panel_or_session.synthesis_reasoning,
                "split_verdict": panel_or_session.split_verdict,
                "sessions": sessions_summary,
            }

        return {
            "verdict": panel_or_session.verdict,
            "confidence": panel_or_session.confidence,
            "reasoning": "",
            "split_verdict": False,
            "sessions": [
                {
                    "persona": panel_or_session.persona,
                    "verdict": panel_or_session.verdict,
                    "confidence": panel_or_session.confidence,
                    "key_findings": panel_or_session.key_findings[:8],
                    "blockers": panel_or_session.blockers[:8],
                    "opportunities": panel_or_session.opportunities[:8],
                    "actions": [
                        {
                            "action": action.action,
                            "status": action.status,
                            "output": action.output[:240],
                        }
                        for action in panel_or_session.actions[:8]
                    ],
                }
            ],
        }

    async def generate_guru_plan(
        self,
        context: dict[str, Any],
        panel_or_session: GStackPanelResult | GStackSession,
    ) -> dict[str, Any]:
        """Generate a deep business and development appendix for a reviewed idea."""

        evaluation = self._evaluation_summary(panel_or_session)
        prompt = f"""Produce a CEO/CTO-grade appendix for this reviewed proposal.

Audience:
- CEO briefing: strategy, commercial logic, validation sequence, kill criteria
- CTO briefing: architecture thesis, simulation plan, constraints, milestones, technical risk retirement

Proposal Brief:
Title: {context.get("title", "")}
Hypothesis: {context.get("hypothesis", "")}
Problem: {context.get("problem", "")}
Solution: {context.get("solution", "")}
Target User: {context.get("target_user", "")}
Economic Buyer: {context.get("economic_buyer", "")}
Current Workaround: {context.get("current_workaround", "")}
Why Existing Tools Fail: {context.get("why_existing_tools_fail", "")}
Adoption Wedge: {context.get("adoption_wedge", "")}
Workflow Insertion: {context.get("workflow_insertion", "")}
Workflow Trigger: {context.get("workflow_trigger", "")}
Integration Boundary: {context.get("integration_boundary", "")}
Integration Surface: {context.get("integration_surface", "")}
Proof Artifact: {context.get("proof_artifact", "")}
Proof of Value: {context.get("proof_of_value", "")}
Success Metric: {context.get("success_metric", "")}
Why Now: {context.get("why_now", "")}
Recommended Next Validation: {context.get("recommended_next_validation", "")}
Known Risks: {json.dumps(context.get("risks", []), ensure_ascii=False)}
Killer Risk: {context.get("killer_risk", "")}

GStack Review:
Verdict: {evaluation["verdict"]}
Confidence: {evaluation["confidence"]:.2f}
Reasoning: {evaluation["reasoning"]}
Panel Details:
{json.dumps(evaluation["sessions"], ensure_ascii=False, indent=2)}

Instructions:
- Produce hypothesis-driven, fact-based logical insight for the business plan.
- Produce simulation-driven, constraint-based, feedback-evolving planning for the development plan.
- Separate facts, inferences, and unknowns.
- If the verdict is negative or uncertain, do not give up. Provide the best salvage, reframing, validation, and conditional build path.
- If the verdict is positive, provide an execution-grade incubation path with concrete commercial and technical milestones.
- For deep-tech, enterprise, semiconductor, or EDA opportunities, anchor the plan in workflow trust, insertion cost, benchmark evidence, pilot design, and signoff constraints.
- Avoid generic startup slogans. Use crisp reasoning and explicit gates.

Return JSON:
{{
  "executive_take": "2-4 sentence top-level assessment",
  "decision_posture": "build_now|validate_then_build|reframe|archive_for_now",
  "fact_base": [
    {{
      "type": "fact|inference|unknown",
      "statement": "specific point",
      "implication": "why it matters"
    }}
  ],
  "business_plan": {{
    "core_thesis": "central business thesis",
    "market_logic": "why this market/workflow matters",
    "customer_and_buyer_map": "user, champion, buyer, blocker",
    "wedge_and_positioning": "narrow initial wedge and why it can win",
    "commercial_motion": "pilot to expansion motion",
    "validation_program": ["ordered validation step"],
    "decision_gates": ["explicit pass/fail gate"],
    "salvage_or_acceleration": ["how to rescue denied ideas or accelerate accepted ones"]
  }},
  "development_plan": {{
    "technical_thesis": "core technical bet",
    "system_boundary": "what is in/out of scope",
    "architecture_outline": "modules and interfaces",
    "simulation_program": ["simulation/benchmark step"],
    "constraints": ["constraint or non-negotiable"],
    "feedback_loops": ["how learning updates the plan"],
    "milestones": ["milestone with expected artifact"],
    "team_and_dependencies": ["people, tools, datasets, integrations"],
    "kill_criteria": ["technical or business stop condition"]
  }},
  "briefing_notes": {{
    "ceo": "what the CEO should focus on next",
    "cto": "what the CTO should focus on next"
  }}
}}"""

        try:
            return await self._chat_json(
                llm_client=self.guru_llm_client,
                messages=[
                    {
                        "role": "system",
                        "content": self._persona_system_prompt("guru", context),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.4,
                timeout_seconds=self.guru_timeout_seconds,
            )
        except Exception as e:
            logger.error("[GStack Guru] Appendix generation failed: %s", e)
            return {
                "executive_take": "[FAILED] Guru appendix generation failed — data below is placeholder, not analysis.",
                "decision_posture": "validate_then_build",
                "fact_base": [],
                "business_plan": {},
                "development_plan": {},
                "briefing_notes": {},
                "generation_error": str(e),
            }

    async def _run_action(
        self,
        session: GStackSession,
        action_name: str,
        context: dict,
    ) -> None:
        """Execute a GStack action and record result."""

        logger.info("[GStack Agent] Running: %s (persona: %s)", action_name, session.persona)

        try:
            action_fn = self.actions.get(action_name)
            if action_fn:
                result = await action_fn(context, session.persona)
                session.add_action(
                    action=action_name,
                    status=result.get("status", "unknown"),
                    output=result.get("output", ""),
                    data=result.get("data", {}),
                )

                # Extract findings
                if "findings" in result:
                    session.key_findings.extend(result["findings"])
                if "blockers" in result:
                    session.blockers.extend(result["blockers"])
                if "opportunities" in result:
                    session.opportunities.extend(result["opportunities"])

                # Persist action to GraphDB
                if self.graph_db and context.get("proposal_node_id"):
                    try:
                        action_id = f"gstack_{uuid.uuid4().hex[:8]}"
                        await self.graph_db.insert_node(
                            node_id=action_id,
                            node_type="gstack_action",
                            label=f"{action_name}: {result.get('status', 'unknown')}",
                            source="entrepreneur",
                        )
                        await self.graph_db.insert_edge(
                            edge_id=f"edge_{uuid.uuid4().hex[:8]}",
                            source_node_id=action_id,
                            target_node_id=context["proposal_node_id"],
                            relation_type="validates",
                            weight=1.0,
                            source="entrepreneur",
                        )
                    except Exception as exc:
                        logger.warning("[GStack Agent] Failed to persist action: %s", exc)
            else:
                session.add_action(
                    action=action_name,
                    status="error",
                    output=f"Unknown action: {action_name}",
                )
        except Exception as e:
            logger.exception("[GStack Agent] Action %s failed: %s", action_name, e)
            session.add_action(
                action=action_name,
                status="error",
                output=str(e),
            )

    async def _analyze_codebase(self, context: dict, persona: str = "general") -> dict:
        """Analyze the codebase for quality and complexity."""

        persona_label = GSTACK_PERSONAS.get(persona, {}).get("name", persona)

        prompt = f"""Analyze this codebase like a {persona_label} evaluating a startup.

Look for:
1. Code quality and maintainability
2. Technical debt indicators
3. Architecture decisions
4. Test coverage signs
5. Documentation quality

Be direct. What's the real technical health? If this is a deep-tech workflow product, evaluate whether the codebase looks credible enough for pilot adoption rather than consumer-scale growth.

Return JSON:
{{
    "status": "success|partial|failure",
    "output": "Concise assessment",
    "findings": ["finding 1", "finding 2"],
    "blockers": ["critical issue"],
    "data": {{"lines_of_code": 0, "test_ratio": 0.0}}
}}"""

        try:
            result = await self._chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return result
        except Exception as e:
            return {
                "status": "error",
                "output": f"Code analysis failed: {e}",
                "findings": [],
                "data": {},
            }

    async def _check_test_coverage(self, context: dict, persona: str = "general") -> dict:
        """Check test coverage and quality."""

        # If we have a tool executor, run actual tests
        if self.tool_executor:
            try:
                result = await self.tool_executor("run_tests")
                return {
                    "status": "success" if result.get("passed") else "failure",
                    "output": f"Tests: {result.get('passed', 0)}/{result.get('total', 0)} passed",
                    "findings": [f"Coverage: {result.get('coverage', 'unknown')}"],
                    "data": result,
                }
            except Exception as e:
                return {
                    "status": "error",
                    "output": f"Test execution failed: {e}",
                    "findings": ["Cannot verify test quality"],
                }

        # Fallback to LLM evaluation
        return {
            "status": "partial",
            "output": "Test coverage check skipped - no tool executor",
            "findings": ["Need to verify test coverage manually"],
        }

    async def _search_market(self, context: dict, persona: str = "general") -> dict:
        """Search for market intelligence and competitors."""

        hypothesis = context.get("hypothesis", "")
        target_user = context.get("target_user", "")
        persona_label = GSTACK_PERSONAS.get(persona, {}).get("name", persona)
        domain_hint = (
            "This appears to be a deep-tech, enterprise, or EDA-style opportunity. "
            "Look for incumbent tool vendors, in-house workflows, service firms, and internal scripts as substitutes. "
            "A narrow market can still matter if value per customer is high."
            if self._is_deeptech_context(context)
            else ""
        )

        prompt = f"""Search for market intelligence on this business from the perspective of a {persona_label}.

Hypothesis: {hypothesis}
Target User: {target_user}
{domain_hint}

Find:
1. Existing competitors (direct and indirect)
2. Market size indicators
3. Recent similar startups (funded, failed, exited)
4. User pain point validation signals
5. Regulatory or market risks
6. For enterprise or EDA ideas, identify the real incumbent workflow, buyer, and switching barriers

Return JSON:
{{
    "status": "success|partial",
    "output": "Market assessment summary",
    "findings": ["competitor A exists", "market is $X"],
    "opportunities": ["gap in market"],
    "blockers": ["crowded market"],
    "data": {{"competitors": [], "market_size": "unknown"}}
}}"""

        try:
            result = await self._chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return result
        except Exception as e:
            return {
                "status": "error",
                "output": f"Market search failed: {e}",
                "findings": [],
            }

    async def _validate_problem(self, context: dict, persona: str = "general") -> dict:
        """Validate that the problem is real and painful."""

        problem = context.get("problem", "")
        target_user = context.get("target_user", "")
        persona_label = GSTACK_PERSONAS.get(persona, {}).get("name", persona)
        domain_hint = (
            "If this is a deep-tech, enterprise, or EDA problem, validate workflow pain, signoff risk, wasted engineer hours, QoR impact, or runtime cost. "
            "Do not require consumer-style virality signals."
            if self._is_deeptech_context(context)
            else ""
        )

        prompt = f"""Validate this problem statement like a {persona_label}.

Problem: {problem}
Target User: {target_user}
{domain_hint}

Ask the hard questions:
1. How do you KNOW this is a real problem?
2. How are they solving it today?
3. How much are they paying (in time or money) for current solutions?
4. Is this a hair-on-fire problem or a nice-to-have?
5. Have you talked to 10+ potential users?
6. In enterprise or EDA cases, who inside the workflow feels the pain first and who would sponsor a pilot?

Return JSON:
{{
    "status": "success|partial|failure",
    "output": "Problem validation assessment",
    "findings": ["Evidence of real pain", "Users currently use X workaround"],
    "blockers": ["No user interviews", "Nice-to-have, not must-have"],
    "data": {{"validation_level": "strong|weak|none"}}
}}"""

        try:
            result = await self._chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )
            return result
        except Exception as e:
            return {
                "status": "error",
                "output": f"Problem validation failed: {e}",
                "findings": [],
            }

    async def _assess_tech_risk(self, context: dict, persona: str = "general") -> dict:
        """Assess technical risks and feasibility."""

        solution = context.get("solution", "")
        persona_label = GSTACK_PERSONAS.get(persona, {}).get("name", persona)
        domain_hint = (
            "For deep-tech, enterprise, or EDA ideas, focus on validation burden, insertion cost into existing flows, trust requirements, benchmarkability, and proof artifacts."
            if self._is_deeptech_context(context)
            else ""
        )

        prompt = f"""Assess technical risks for this solution from the perspective of a {persona_label}.

Solution: {solution}
{domain_hint}

Evaluate:
1. Technical feasibility (can it be built?)
2. Complexity vs timeline
3. Key technical dependencies
4. Team capability requirements
5. Infrastructure costs at scale
6. Security/compliance considerations
7. Workflow insertion and validation burden

Return JSON:
{{
    "status": "success|partial|failure",
    "output": "Technical risk assessment",
    "findings": ["Feasible with current stack", "Will need ML expertise"],
    "blockers": ["Requires impossible scale", "Regulatory barrier"],
    "data": {{"risk_level": "low|medium|high", "mvp_complexity": "weeks|months"}}
}}"""

        try:
            result = await self._chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return result
        except Exception as e:
            return {
                "status": "error",
                "output": f"Tech risk assessment failed: {e}",
                "findings": [],
            }

    async def _check_deployment_readiness(self, context: dict, persona: str = "general") -> dict:
        """Check if the product is ready to ship."""

        # If we have tool executor, run actual checks
        if self.tool_executor:
            checks = []

            # Run health check
            try:
                health = await self.tool_executor("health_check")
                checks.append(("health", health))
            except Exception:
                checks.append(("health", {"status": "unknown"}))

            return {
                "status": "success",
                "output": f"Deployment checks: {len([c for c in checks if c[1].get('status') == 'ok'])}/{len(checks)} passing",
                "findings": [f"{name}: {data.get('status')}" for name, data in checks],
                "data": {"checks": dict(checks)},
            }

        # Fallback
        return {
            "status": "partial",
            "output": "Deployment readiness check skipped - no tool executor",
            "findings": ["Manual deployment verification needed"],
        }

    async def _generate_verdict(
        self, session: GStackSession, persona: str = "general"
    ) -> tuple[str, float]:
        """Generate final verdict based on all actions taken."""

        # Build context from all actions
        action_outputs = "\n\n".join([
            f"=== {a.action} ===\nStatus: {a.status}\n{a.output}"
            for a in session.actions
        ])

        findings = "\n".join([f"- {f}" for f in session.key_findings])
        blockers = "\n".join([f"- {b}" for b in session.blockers])
        opportunities = "\n".join([f"- {o}" for o in session.opportunities])

        prompt = f"""Based on the GStack evaluation actions taken, render a final verdict.

Hypothesis: {session.hypothesis}
Problem: {session.problem}
Solution: {session.solution}
Target User: {session.target_user}

Actions Taken:
{action_outputs}

Key Findings:
{findings}

Blockers:
{blockers}

Opportunities:
{opportunities}

Render verdict like a senior partner would - direct and concrete:
- build_it: Strong signal, clear path, real problem
- pivot: Something's off but there's potential
- kill_it: Fatal flaws, don't waste time
- needs_more_validation: Not enough data to decide

Important:
- In deep-tech or EDA cases, a narrow wedge is acceptable if buyer, workflow pain, proof path, and insertion strategy are credible.
- Do not default to kill_it only because the market is specialized or enterprise-led.

Return JSON:
{{
    "verdict": "build_it|pivot|kill_it|needs_more_validation",
    "confidence": 0.0-1.0,
    "reasoning": "Why this verdict",
    "critical_factors": ["factor 1", "factor 2"],
    "next_steps": ["do this", "then this"]
}}"""

        system_prompt = self._persona_system_prompt(persona, {
            "hypothesis": session.hypothesis,
            "problem": session.problem,
            "solution": session.solution,
            "target_user": session.target_user,
        })

        try:
            result = await self._chat_json(
                messages=[{
                    "role": "system",
                    "content": system_prompt,
                }, {
                    "role": "user",
                    "content": prompt,
                }],
                temperature=0.7,
            )

            return (
                result.get("verdict", "needs_more_validation"),
                result.get("confidence", 0.5),
            )
        except Exception as e:
            logger.error("[GStack Agent] Verdict generation failed: %s", e)
            return ("needs_more_validation", 0.3)

    def get_session_report(self, session: GStackSession) -> str:
        """Generate human-readable session report."""

        lines = [
            f"# GStack Evaluation: {session.hypothesis}",
            "",
            f"**Persona:** {GSTACK_PERSONAS.get(session.persona, {}).get('name', session.persona)}",
            f"**Verdict:** {session.verdict.upper()}",
            f"**Confidence:** {session.confidence:.0%}",
            f"**Duration:** {len(session.actions)} actions taken",
            "",
            "## Actions Taken",
        ]

        for action in session.actions:
            emoji = "✅" if action.status == "success" else "⚠️" if action.status == "partial" else "❌"
            lines.append(f"{emoji} **{action.action}**: {action.status}")
            lines.append(f"   {action.output[:150]}...")
            lines.append("")

        if session.key_findings:
            lines.extend(["## Key Findings", ""])
            for f in session.key_findings[:10]:
                lines.append(f"- {f}")
            lines.append("")

        if session.blockers:
            lines.extend(["## Blockers", ""])
            for b in session.blockers:
                lines.append(f"- ⚠️ {b}")
            lines.append("")

        if session.opportunities:
            lines.extend(["## Opportunities", ""])
            for o in session.opportunities:
                lines.append(f"- 💡 {o}")
            lines.append("")

        return "\n".join(lines)

    def get_panel_report(self, panel: GStackPanelResult) -> str:
        """Generate human-readable panel report."""

        lines = [
            f"# GStack Panel Evaluation: {panel.sessions[0].hypothesis}" if panel.sessions else "# GStack Panel Evaluation",
            "",
            f"**Final Verdict:** {panel.final_verdict.upper()}",
            f"**Final Confidence:** {panel.final_confidence:.0%}",
            f"**Split Verdict:** {'Yes' if panel.split_verdict else 'No'}",
            "",
            "## Panel Verdicts",
        ]

        for session in panel.sessions:
            persona_name = GSTACK_PERSONAS.get(session.persona, {}).get("name", session.persona)
            lines.append(f"- **{persona_name}**: {session.verdict} ({session.confidence:.0%})")

        lines.append("")
        lines.append("## Synthesis Reasoning")
        lines.append(panel.synthesis_reasoning or "No reasoning provided.")
        lines.append("")

        for session in panel.sessions:
            lines.append(self.get_session_report(session))
            lines.append("---")

        return "\n".join(lines)
