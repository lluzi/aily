"""KnowledgeAgent - Stage 3: KNOWLEDGE relationship mapping."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import chat_json
from aily.dikiwi.network_synthesis import NetworkSynthesisSelector, candidate_nodes_to_information
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DikiwiStage, InformationNode, KnowledgeLink, StageResult

logger = logging.getLogger(__name__)


class KnowledgeAgent(DikiwiAgent):
    """Stage 3: Map relationships from graph-selected information subgraphs."""

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            info_result = self._find_stage_result(ctx, DikiwiStage.INFORMATION)
            if not info_result:
                raise RuntimeError("INFORMATION stage result not found in context")

            info_nodes: list[InformationNode] = info_result.data.get("information_nodes", [])
            info_note_ids: dict[str, str] = info_result.data.get("info_note_ids", {})
            source = ctx.drop.source

            local_links: list[KnowledgeLink] = []
            if len(info_nodes) >= 2:
                local_links = await self._llm_map_relations_batch(info_nodes, source, ctx)

                if ctx.graph_db:
                    await self._persist_links(local_links, ctx, source="dikiwi_local")

            assessment = await NetworkSynthesisSelector().assess(ctx, info_nodes, local_links)
            network_nodes = candidate_nodes_to_information(assessment.candidates)
            links: list[KnowledgeLink] = local_links

            if assessment.triggered and len(network_nodes) >= 2:
                network_context = assessment.to_prompt_context()
                if ctx.memory:
                    ctx.memory.add_system(
                        "Network synthesis trigger:\n"
                        f"{assessment.reason}\n\n{network_context[:3000]}"
                    )
                network_links = await self._llm_map_relations_batch(
                    network_nodes,
                    "dikiwi_network",
                    ctx,
                    subgraph_context=network_context,
                )
                links = network_links
                if network_links:
                    if ctx.graph_db:
                        await self._persist_links(network_links, ctx, source="dikiwi_network")

            # Write knowledge notes
            knowledge_note_ids: list[str] = []
            node_map = {n.id: n for n in [*info_nodes, *network_nodes]}
            write_source = "dikiwi_network" if assessment.triggered else source
            source_paths = ctx.drop.metadata.get("source_paths", []) if getattr(ctx.drop, "metadata", None) else []
            if ctx.dikiwi_obsidian_writer and links:
                for link in links:
                    src_node = node_map.get(link.source_id)
                    tgt_node = node_map.get(link.target_id)
                    if src_node and tgt_node:
                        try:
                            kid = await ctx.dikiwi_obsidian_writer.write_knowledge_note(
                                link,
                                src_node,
                                tgt_node,
                                info_note_ids.get(link.source_id, ""),
                                info_note_ids.get(link.target_id, ""),
                                write_source,
                                source_paths,
                            )
                            knowledge_note_ids.append(kid)
                        except Exception as e:
                            logger.warning("[DIKIWI] Failed to write knowledge note: %s", e)

            if ctx.memory and links:
                ctx.memory.add_assistant(
                    f"STAGE 3 (KNOWLEDGE) Complete: Found {len(links)} meaningful links. "
                    f"Examples: {', '.join(link.relation_type for link in links[:4])}"
                )

            processing_time = (time.time() - start) * 1000

            return StageResult(
                stage=DikiwiStage.KNOWLEDGE,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(links),
                processing_time_ms=processing_time,
                data={
                    "links": links,
                    "local_links": local_links,
                    "knowledge_note_ids": knowledge_note_ids,
                    "network_synthesis_triggered": assessment.triggered,
                    "graph_change_assessment": assessment,
                    "graph_change_score": assessment.score,
                    "subgraph_candidates": assessment.candidates,
                    "network_nodes": network_nodes,
                    "network_context": assessment.to_prompt_context(),
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.KNOWLEDGE,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _llm_map_relations_batch(
        self,
        info_nodes: list[InformationNode],
        source: str,
        ctx: AgentContext,
        subgraph_context: str = "",
    ) -> list[KnowledgeLink]:
        memory_context = DikiwiPromptRegistry.render_memory(ctx.memory, limit=1200)
        messages = DikiwiPromptRegistry.relation_batch(
            nodes=info_nodes,
            memory_context=memory_context,
            subgraph_context=subgraph_context,
        )
        stage_hash = hashlib.sha1((source + subgraph_context[:200]).encode("utf-8")).hexdigest()[:8]
        stage_key = f"knowledge:batch:{stage_hash}"
        id_map = {i: n.id for i, n in enumerate(info_nodes)}

        try:
            result = await chat_json(
                llm_client=ctx.llm_client,
                stage="knowledge",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                budget=ctx.budget,
            )
            if not isinstance(result, dict):
                return []

            links: list[KnowledgeLink] = []
            for raw in result.get("links", []):
                if not isinstance(raw, dict):
                    continue
                a_idx = raw.get("node_a_index")
                b_idx = raw.get("node_b_index")
                relation = raw.get("relation_type", "none")
                strength = float(raw.get("strength", 0.0))
                if (
                    a_idx is None
                    or b_idx is None
                    or relation == "none"
                    or strength <= 0.5
                    or a_idx not in id_map
                    or b_idx not in id_map
                    or a_idx == b_idx
                ):
                    continue
                links.append(
                    KnowledgeLink(
                        source_id=id_map[a_idx],
                        target_id=id_map[b_idx],
                        relation_type=relation,
                        strength=strength,
                        reasoning=str(raw.get("reasoning", "")),
                    )
                )
            return links
        except Exception as exc:
            logger.warning("[DIKIWI] Batch relation mapping failed: %s", exc)
            return []

    async def _persist_links(
        self,
        links: list[KnowledgeLink],
        ctx: AgentContext,
        source: str,
    ) -> None:
        if not ctx.graph_db:
            return
        for link in links:
            await ctx.graph_db.insert_edge(
                edge_id=f"link_{uuid.uuid4().hex[:8]}",
                source_node_id=link.source_id,
                target_node_id=link.target_id,
                relation_type=link.relation_type,
                weight=link.strength,
                source=source,
            )

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
