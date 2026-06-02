"""InsightAgent - Stage 4: INSIGHT pattern recognition."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import multi_agent_json
from aily.dikiwi.network_synthesis import SubgraphCandidate
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DikiwiStage, InformationNode, Insight, KnowledgeLink, StageResult

logger = logging.getLogger(__name__)


class InsightAgent(DikiwiAgent):
    """Stage 4: Detect patterns from knowledge network via producer-reviewer."""

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            info_result = self._find_stage_result(ctx, DikiwiStage.INFORMATION)
            knowledge_result = self._find_stage_result(ctx, DikiwiStage.KNOWLEDGE)
            if not info_result or not knowledge_result:
                raise RuntimeError("Prior stage results not found in context")

            candidates: list[SubgraphCandidate] = knowledge_result.data.get("subgraph_candidates", [])
            info_nodes: list[InformationNode] = knowledge_result.data.get("network_nodes", [])
            links: list[KnowledgeLink] = knowledge_result.data.get("links", [])
            knowledge_note_ids: list[str] = knowledge_result.data.get("knowledge_note_ids", [])
            network_context: str = knowledge_result.data.get("network_context", "")

            insights: list[Insight] = []
            if candidates and len(info_nodes) >= 2:
                path_context = self._short_path_context(candidates)
                if path_context:
                    insights = await self._llm_detect_patterns(
                        info_nodes,
                        links,
                        ctx,
                        "\n\n".join(part for part in (network_context, path_context) if part).strip(),
                    )
                    provenance = self._graph_provenance(candidates, "short_information_paths")
                    for insight_item in insights:
                        setattr(insight_item, "graph_provenance", provenance)

            # Write insight notes
            insight_note_ids: list[str] = []
            if ctx.dikiwi_obsidian_writer and insights:
                source_paths = ctx.drop.metadata.get("source_paths", []) if getattr(ctx.drop, "metadata", None) else []
                for insight_item in insights:
                    try:
                        iid = await ctx.dikiwi_obsidian_writer.write_insight_note(
                            insight_item, knowledge_note_ids, ctx.drop, source_paths
                        )
                        insight_note_ids.append(iid)
                    except Exception as e:
                        logger.warning("[DIKIWI] Failed to write insight note: %s", e)

            # Add to memory
            if ctx.memory and insights:
                ctx.memory.add_assistant(
                    f"STAGE 4 (INSIGHT) Complete: Found {len(insights)} patterns. "
                    f"Key insights: {', '.join(i.description[:60] for i in insights[:2])}..."
                )

            processing_time = (time.time() - start) * 1000

            return StageResult(
                stage=DikiwiStage.INSIGHT,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(insights),
                processing_time_ms=processing_time,
                data={
                    "insights": insights,
                    "insight_note_ids": insight_note_ids,
                    "subgraph_candidates": candidates,
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.INSIGHT,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _llm_detect_patterns(
        self,
        info_nodes: list[InformationNode],
        links: list[KnowledgeLink],
        ctx: AgentContext,
        network_context: str = "",
    ) -> list[Insight]:
        nodes_desc = "\n".join(
            f"E{i+1}. [{n.domain}] {getattr(n, 'concept', '') or n.content[:180]}"
            for i, n in enumerate(info_nodes[:15])
        )
        node_label_map = {f"E{i+1}": n.id for i, n in enumerate(info_nodes[:15])}
        node_reference_map = {
            f"E{i+1}": {
                "node_id": n.id,
                "label": getattr(n, "concept", "") or n.content[:180],
                "domain": n.domain,
            }
            for i, n in enumerate(info_nodes[:15])
        }
        links_desc = "\n".join(
            f"- {l.source_id[:8]}... {l.relation_type} {l.target_id[:8]}... (strength: {l.strength:.2f})"
            for l in links[:10]
        )

        memory_context = ""
        if ctx.memory and len(ctx.memory.messages) > 2:
            memory_context = f"\n\nProcessing context:\n{ctx.memory.to_prompt_context()[-1500:]}\n\n"

        messages = DikiwiPromptRegistry.insight(
            nodes_desc=(network_context + "\n\n" + nodes_desc).strip(),
            links_desc=links_desc,
            memory_context=memory_context.strip(),
        )
        stage_seed = nodes_desc + links_desc + network_context[:500]
        stage_key = f"insight:{hashlib.sha1(stage_seed.encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await multi_agent_json(
                llm_client=ctx.llm_client,
                stage="insight",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="INSIGHT",
                    reviewer_role="Pattern Editor",
                    objective="Review the draft insights and keep only non-obvious patterns that deserve long-term note space.",
                    output_contract=DikiwiPromptRegistry.INSIGHT_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context.strip(),
                    review_focus=(
                        "Remove restatements of single facts.",
                        "Prefer tensions, mechanisms, gaps, and recurring patterns.",
                        "Keep the insight list compact but meaningful.",
                    ),
                    context_sections=(
                        ("Graph Paths", network_context or "No graph path context available."),
                        ("Information Nodes", nodes_desc or "No nodes available."),
                        ("Relationships", links_desc or "No explicit relationships available."),
                    ),
                ),
                temperature=0.4,
                budget=ctx.budget,
            )

            if not isinstance(result, dict):
                return []

            insights_data = result.get("insights", [])
            insights: list[Insight] = []
            for p in insights_data:
                if isinstance(p, dict) and p.get("description"):
                    related_nodes = self._resolve_related_nodes(p, node_label_map)
                    insight = Insight(
                        id=f"insight_{uuid.uuid4().hex[:8]}",
                        insight_type=p.get("type", "pattern"),
                        description=p.get("description", ""),
                        related_nodes=related_nodes,
                        confidence=p.get("confidence", 0.5),
                    )
                    setattr(insight, "title", p.get("insight_title", ""))
                    setattr(insight, "why_nonobvious", p.get("why_nonobvious", ""))
                    setattr(insight, "significance", p.get("significance", ""))
                    setattr(insight, "design_implication", p.get("design_implication", ""))
                    node_references = []
                    for ref in p.get("related_evidence", []) if isinstance(p.get("related_evidence"), list) else []:
                        mapped = node_reference_map.get(str(ref).strip())
                        if mapped:
                            node_references.append(mapped)
                    setattr(insight, "node_references", node_references)
                    setattr(
                        insight,
                        "evidence_label_map",
                        {
                            ref: str(mapped.get("label") or "")
                            for ref, mapped in node_reference_map.items()
                            if mapped.get("label")
                        },
                    )
                    insights.append(insight)
            return insights
        except Exception as exc:
            logger.debug("[DIKIWI] Pattern detection failed: %s", exc)
            return []

    @staticmethod
    def _resolve_related_nodes(raw: dict, node_label_map: dict[str, str]) -> list[str]:
        labels = raw.get("related_evidence", [])
        resolved: list[str] = []
        if isinstance(labels, list):
            for label in labels:
                node_id = node_label_map.get(str(label).strip())
                if node_id:
                    resolved.append(node_id)
        if resolved:
            return resolved

        # Backward compatibility for older test doubles or cached prompts.
        indices = raw.get("related_node_indices", [])
        if isinstance(indices, list):
            for idx in indices:
                if isinstance(idx, int):
                    node_id = node_label_map.get(f"E{idx + 1}")
                    if node_id:
                        resolved.append(node_id)
        return resolved

    @staticmethod
    def _short_path_context(candidates: list[SubgraphCandidate]) -> str:
        """Describe graph paths; Insight must be derived from these paths."""
        paths: list[str] = []
        for candidate in candidates:
            labels = {str(node.get("id")): str(node.get("label", "")) for node in candidate.nodes}
            info_ids = {str(node.get("id")) for node in candidate.nodes if node.get("type") == "information"}
            path_count = 0
            for edge in candidate.edges:
                src = str(edge.get("source_node_id", ""))
                tgt = str(edge.get("target_node_id", ""))
                if src not in info_ids or tgt not in info_ids:
                    continue
                path_count += 1
                paths.append(
                    f"- {candidate.id}: {labels.get(src, src)[:180]} "
                    f"--{edge.get('relation_type')}({float(edge.get('weight', 0.0)):.2f})--> "
                    f"{labels.get(tgt, tgt)[:180]}"
                )
                if path_count >= 10:
                    break
        if not paths:
            return ""
        return "\n".join(["Graph paths for Insight synthesis:", *paths])

    @staticmethod
    def _graph_provenance(candidates: list[SubgraphCandidate], mode: str) -> dict:
        node_ids: list[str] = []
        edge_ids: list[str] = []
        anchors: list[str] = []
        for candidate in candidates:
            anchors.append(candidate.anchor_label)
            node_ids.extend(str(node.get("id")) for node in candidate.nodes if node.get("id"))
            edge_ids.extend(str(edge.get("id")) for edge in candidate.edges if edge.get("id"))
        return {
            "mode": mode,
            "subgraph_ids": [candidate.id for candidate in candidates],
            "anchors": list(dict.fromkeys(anchors)),
            "node_ids": list(dict.fromkeys(node_ids)),
            "edge_ids": list(dict.fromkeys(edge_ids)),
        }

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
