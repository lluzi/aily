"""ImpactAgent - Stage 6: IMPACT actionable outcome generation."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import multi_agent_json
from aily.llm.prompt_registry import DikiwiPromptRegistry

from aily.sessions.dikiwi_mind import DikiwiStage, StageResult, ZettelkastenNote

logger = logging.getLogger(__name__)


class ImpactAgent(DikiwiAgent):
    """Stage 6: Generate actionable outcomes from Zettelkasten notes via producer-reviewer."""

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            wisdom_result = self._find_stage_result(ctx, DikiwiStage.WISDOM)
            if not wisdom_result:
                raise RuntimeError("WISDOM stage result not found in context")

            zettels: list[ZettelkastenNote] = wisdom_result.data.get("zettels", [])
            wisdom_note_ids: list[str] = wisdom_result.data.get("wisdom_note_ids", [])
            center_nodes = await self._graph_centers(ctx)

            impacts: list[dict] = []
            if zettels and center_nodes:
                impacts = await self._llm_generate_impacts(zettels, ctx, center_nodes)
                provenance = {
                    "mode": "high_connectivity_center_nodes",
                    "center_node_ids": [str(node.get("id")) for node in center_nodes if node.get("id")],
                }
                for impact_item in impacts:
                    impact_item["graph_provenance"] = provenance

            # Write impact notes
            if ctx.dikiwi_obsidian_writer and impacts:
                source_paths = ctx.drop.metadata.get("source_paths", []) if getattr(ctx.drop, "metadata", None) else []
                for impact_item in impacts:
                    try:
                        await ctx.dikiwi_obsidian_writer.write_impact_note(
                            impact_item, wisdom_note_ids, ctx.drop, source_paths
                        )
                    except Exception as e:
                        logger.warning("[DIKIWI] Failed to write impact note: %s", e)

            processing_time = (time.time() - start) * 1000

            return StageResult(
                stage=DikiwiStage.IMPACT,
                success=True,
                items_processed=len(zettels),
                items_output=len(impacts),
                processing_time_ms=processing_time,
                data={
                    "impacts": impacts,
                    "center_nodes": center_nodes,
                    "ready_for_scheduled_minds": len(impacts) > 0,
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.IMPACT,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _llm_generate_impacts(
        self,
        zettels: list[ZettelkastenNote],
        ctx: AgentContext,
        center_nodes: list[dict[str, Any]],
    ) -> list[dict]:
        zettels_desc = "\n".join(
            f"- {z.title[:100]}"
            for z in zettels[:3]
        )
        center_nodes = self._filter_centers_for_zettels(center_nodes, zettels)
        center_context = "\n".join(
            f"- C{i + 1}: [{node.get('type')}] {node.get('label')} "
            f"(edges={node.get('edge_count')}, weight={float(node.get('total_weight', 0.0)):.2f})"
            for i, node in enumerate(center_nodes[:10])
        )
        memory_context = DikiwiPromptRegistry.render_memory(ctx.memory, limit=1200)
        messages = DikiwiPromptRegistry.impact(
            zettels_desc=("\n\n".join(part for part in (center_context, zettels_desc) if part)).strip(),
            memory_context=memory_context,
        )
        stage_key = f"impact:{hashlib.sha1((center_context + zettels_desc).encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await multi_agent_json(
                llm_client=ctx.llm_client,
                stage="impact",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="IMPACT",
                    reviewer_role="Action Editor",
                    objective="Review the proposed impacts and keep only actions that faithfully follow from the extracted knowledge.",
                    output_contract=DikiwiPromptRegistry.IMPACT_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context,
                    review_focus=(
                        "Remove generic actions that are not grounded in the notes.",
                        "Prefer concrete next steps that compound the knowledge system.",
                        "Remove cross-domain claims unless the exact domain appears in the supplied zettels or graph centers.",
                        "If the evidence only supports a narrow yield, timing, packaging, or EDA workflow, keep the proposal inside that workflow boundary.",
                    ),
                    context_sections=(
                        ("Graph Center Nodes", center_context or "No graph center nodes available."),
                        ("Zettelkasten Principles", zettels_desc or "No zettels available."),
                    ),
                ),
                temperature=0.5,
                budget=ctx.budget,
            )

            if not isinstance(result, dict):
                return []

            impacts = result.get("impacts", [])
            return [
                {
                    "type": imp.get("type", "action"),
                    "description": imp.get("description", ""),
                    "target_user": imp.get("target_user", ""),
                    "economic_buyer": imp.get("economic_buyer", ""),
                    "workflow_trigger": imp.get("workflow_trigger", ""),
                    "current_workaround": imp.get("current_workaround", ""),
                    "integration_surface": imp.get("integration_surface", ""),
                    "proof_of_value": imp.get("proof_of_value", ""),
                    "why_now": imp.get("why_now", ""),
                    "killer_risk": imp.get("killer_risk", ""),
                    "priority": imp.get("priority", "medium"),
                    "rationale": imp.get("rationale", ""),
                    "effort_estimate": imp.get("effort_estimate", "medium"),
                    "potential_value": imp.get("potential_value", ""),
                }
                for imp in impacts
                if isinstance(imp, dict)
            ]
        except Exception as exc:
            logger.debug("[DIKIWI] Impact generation failed: %s", exc)
            return []

    async def _graph_centers(self, ctx: AgentContext) -> list[dict[str, Any]]:
        """Return central information nodes; Impact must be grounded in hubs."""
        if not ctx.graph_db:
            return []
        try:
            if hasattr(ctx.graph_db, "get_top_information_nodes_by_semantic_edge_count"):
                nodes = await ctx.graph_db.get_top_information_nodes_by_semantic_edge_count(limit=15)
            else:
                nodes = await ctx.graph_db.get_top_nodes_by_edge_count(limit=15)
            return [
                node
                for node in nodes
                if node.get("type") == "information" and not self._is_generic_center(node)
            ]
        except Exception as exc:
            logger.warning("[DIKIWI] Failed to load graph centers for impact: %s", exc)
            return []

    @staticmethod
    def _filter_centers_for_zettels(center_nodes: list[dict[str, Any]], zettels: list[ZettelkastenNote]) -> list[dict[str, Any]]:
        """Keep graph centers that share vocabulary with the wisdom notes.

        Global graph hubs can be valid Obsidian navigation targets while still
        being unsafe for Impact synthesis. This filter prevents a yield-focused
        wisdom note from borrowing unrelated chiplet/STA centers simply because
        they are globally central in the run.
        """
        stopwords = {
            "about",
            "after",
            "analysis",
            "based",
            "between",
            "design",
            "diagnostic",
            "diagnostics",
            "framework",
            "from",
            "into",
            "method",
            "model",
            "note",
            "process",
            "source",
            "system",
            "this",
            "through",
            "using",
            "with",
            "workflow",
        }
        terms: set[str] = set()
        for zettel in zettels:
            haystack = " ".join(
                [
                    getattr(zettel, "title", ""),
                    getattr(zettel, "content", ""),
                    " ".join(getattr(zettel, "tags", []) or []),
                    " ".join(getattr(zettel, "links_to", []) or []),
                ]
            )
            for word in re.findall(r"[A-Za-z][A-Za-z0-9.#+-]{3,}", haystack):
                normalized = word.lower().strip(".")
                if normalized not in stopwords:
                    terms.add(normalized)
        if not terms:
            return center_nodes[:5]

        filtered: list[dict[str, Any]] = []
        for node in center_nodes:
            label = str(node.get("label") or "")
            node_terms = {
                word.lower().strip(".")
                for word in re.findall(r"[A-Za-z][A-Za-z0-9.#+-]{3,}", label)
                if word.lower().strip(".") not in stopwords
            }
            if terms.intersection(node_terms):
                filtered.append(node)
        return filtered[:8] if filtered else center_nodes[:3]

    @staticmethod
    def _is_generic_center(node: dict[str, Any]) -> bool:
        label = str(node.get("label") or "").strip().lower().replace("_", " ").replace("-", " ")
        if not label:
            return True
        if label.startswith(("page ", "slide ")) and label.split(" ", 1)[1].strip().isdigit():
            return True
        return label in {"page", "slide", "visual", "image", "figure", "table"}

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
