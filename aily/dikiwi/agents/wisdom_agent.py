"""WisdomAgent - Stage 5: WISDOM synthesis into Zettelkasten notes."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.config import SETTINGS
from aily.dikiwi.agents.llm_tools import chat_json, multi_agent_json
from aily.dikiwi.network_synthesis import SubgraphCandidate
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DikiwiStage, InformationNode, Insight, StageResult, ZettelkastenNote

logger = logging.getLogger(__name__)


class WisdomAgent(DikiwiAgent):
    """Stage 5: Synthesize insights into Zettelkasten permanent notes via producer-reviewer."""

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            insight_result = self._find_stage_result(ctx, DikiwiStage.INSIGHT)
            info_result = self._find_stage_result(ctx, DikiwiStage.INFORMATION)
            knowledge_result = self._find_stage_result(ctx, DikiwiStage.KNOWLEDGE)
            if not insight_result or not info_result:
                raise RuntimeError("Prior stage results not found in context")

            insights: list[Insight] = insight_result.data.get("insights", [])
            insight_note_ids: list[str] = insight_result.data.get("insight_note_ids", [])
            candidates: list[SubgraphCandidate] = insight_result.data.get("subgraph_candidates", [])
            info_nodes: list[InformationNode] = []
            if knowledge_result:
                info_nodes = knowledge_result.data.get("network_nodes", [])
            if not info_nodes:
                info_nodes = info_result.data.get("information_nodes", [])

            zettels: list[ZettelkastenNote] = []
            long_path_context = self._long_path_context(candidates, max_paths=6)
            if info_nodes and (insights or long_path_context):
                zettels = await self._llm_synthesize_wisdom(
                    insights,
                    info_nodes,
                    ctx,
                    long_path_context,
                )
            elif info_nodes:
                zettels = await self._llm_synthesize_wisdom(
                    insights,
                    info_nodes,
                    ctx,
                    "Direct information-node fallback for legacy Wisdom synthesis.",
                )
            if candidates and zettels:
                provenance = self._graph_provenance(candidates, "long_information_paths")
                for zettel in zettels:
                    setattr(zettel, "source", "dikiwi_graph")
                    setattr(zettel, "graph_provenance", provenance)

            # Build title -> dikiwi_id map so Related links resolve exactly
            title_map: dict[str, str] = {}
            for z in zettels:
                zid = f"wisdom_{z.id}"
                title_map[z.title.lower()] = zid
                # Also index by slugified title for robust matching
                slug = "".join(c for c in z.title.lower() if c.isalnum())
                if slug in title_map:
                    slug = f"{slug}_{z.id[:6]}"
                title_map[slug] = zid

            # Pre-register all zettel titles so cross-links use full filenames
            if ctx.dikiwi_obsidian_writer and zettels:
                for z in zettels:
                    ctx.dikiwi_obsidian_writer.register_note_title(
                        f"wisdom_{z.id}", z.title
                    )

            # Write wisdom notes
            wisdom_note_ids: list[str] = []
            if ctx.dikiwi_obsidian_writer and zettels:
                source_paths = ctx.drop.metadata.get("source_paths", []) if getattr(ctx.drop, "metadata", None) else []
                for zettel in zettels:
                    try:
                        wid = await ctx.dikiwi_obsidian_writer.write_wisdom_note(
                            zettel, insight_note_ids, ctx.drop, source_paths, link_map=title_map
                        )
                        wisdom_note_ids.append(wid)
                        logger.info(
                            "[DIKIWI] Wrote wisdom note: %s (%d words)",
                            zettel.title[:40],
                            len(zettel.content.split()),
                        )
                    except Exception as e:
                        logger.warning("[DIKIWI] Failed to write wisdom note: %s", e)

            if ctx.memory and zettels:
                ctx.memory.add_assistant(
                    f"STAGE 5 (WISDOM) Complete: Authored {len(zettels)} permanent notes. "
                    f"Titles: {', '.join(z.title[:80] for z in zettels[:3])}"
                )

            processing_time = (time.time() - start) * 1000

            return StageResult(
                stage=DikiwiStage.WISDOM,
                success=True,
                items_processed=len(insights),
                items_output=len(zettels),
                processing_time_ms=processing_time,
                data={
                    "zettels": zettels,
                    "wisdom_note_ids": wisdom_note_ids,
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.WISDOM,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _llm_synthesize_wisdom(
        self,
        insights: list[Insight],
        info_nodes: list[InformationNode],
        ctx: AgentContext,
        long_path_context: str = "",
    ) -> list[ZettelkastenNote]:
        insights_desc = "\n".join(
            f"- [{i.insight_type}] {i.description[:140]}"
            for i in insights[:4]
        )
        info_samples = "\n".join(
            f"- [{n.domain}] {n.content[:140]}"
            for n in info_nodes[:8]
        )
        knowledge_context = (
            "\n\n".join(part for part in (long_path_context, info_samples) if part)
        ).strip()

        memory_context = DikiwiPromptRegistry.render_memory(ctx.memory, limit=700)
        messages = DikiwiPromptRegistry.wisdom(
            insights_desc=insights_desc,
            info_samples=knowledge_context,
            memory_context=memory_context,
        )
        stage_key = f"wisdom:{hashlib.sha1((insights_desc + knowledge_context).encode('utf-8')).hexdigest()[:8]}"

        try:
            if SETTINGS.dikiwi_wisdom_review_enabled:
                result = await multi_agent_json(
                    llm_client=ctx.llm_client,
                    stage="wisdom",
                    stage_key=stage_key,
                    producer_messages=messages,
                    reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                        stage="WISDOM",
                        reviewer_role="Slip-Box Editor",
                        objective="Review the draft permanent notes and return a cleaner set of atomic, source-grounded zettels for long-term use.",
                        output_contract=DikiwiPromptRegistry.WISDOM_CONTRACT,
                        draft_json=draft_json,
                        memory_context=memory_context,
                        review_focus=(
                            "Reject summary-shaped notes and preserve distinct durable ideas instead.",
                            "Split mechanisms, workflows, constraints, tradeoffs, and examples into separate notes when the source supports them.",
                            "Keep titles readable, human, and conceptually precise.",
                        ),
                        context_sections=(
                            ("Insights", insights_desc or "No insights available."),
                            ("Graph Long Paths", long_path_context or "No long-path context available."),
                            ("Knowledge Base", knowledge_context or "No information samples available."),
                        ),
                    ),
                    temperature=0.5,
                    budget=ctx.budget,
                )
            else:
                result = await chat_json(
                    llm_client=ctx.llm_client,
                    stage="wisdom",
                    stage_key=stage_key,
                    messages=messages,
                    temperature=0.45,
                    budget=ctx.budget,
                )

            if not isinstance(result, dict):
                return []

            zettels_data = result.get("zettels", [])
            zettels: list[ZettelkastenNote] = []
            for z in zettels_data:
                if not isinstance(z, dict) or not z.get("title") or len(z.get("content", "")) <= 100:
                    continue
                note = ZettelkastenNote(
                    id=f"z{uuid.uuid4().hex[:6]}",
                    title=z.get("canonical_title") or z.get("title", "Untitled"),
                    content=z.get("content", ""),
                    tags=z.get("tags", []),
                    links_to=z.get("links_to", []),
                    confidence=z.get("confidence", 0.5),
                )
                setattr(note, "thesis", z.get("thesis", ""))
                setattr(note, "source_evidence", z.get("source_evidence", []))
                setattr(note, "open_questions", z.get("open_questions", []))
                zettels.append(note)
            return zettels
        except Exception as exc:
            logger.debug("[DIKIWI] Wisdom synthesis failed: %s", exc)
            return []

    @staticmethod
    def _long_path_context(candidates: list[SubgraphCandidate], max_paths: int = 6) -> str:
        """Describe longer candidate paths connecting distant information nodes."""
        paths: list[str] = []
        for candidate in candidates[:4]:
            labels = {str(node.get("id")): str(node.get("label", "")) for node in candidate.nodes}
            info_ids = [str(node.get("id")) for node in candidate.nodes if node.get("type") == "information"]
            adjacency: dict[str, list[tuple[str, dict]]] = {node_id: [] for node_id in info_ids}
            for edge in candidate.edges:
                src = str(edge.get("source_node_id", ""))
                tgt = str(edge.get("target_node_id", ""))
                if src in adjacency and tgt in adjacency:
                    adjacency[src].append((tgt, edge))
                    adjacency[tgt].append((src, edge))
            for start in info_ids[:2]:
                if len(paths) >= max_paths:
                    break
                seen = {start}
                path_nodes = [start]
                path_edges: list[dict] = []
                current = start
                for _ in range(3):
                    next_items = [(n, e) for n, e in adjacency.get(current, []) if n not in seen]
                    if not next_items:
                        break
                    next_node, edge = sorted(
                        next_items,
                        key=lambda item: float(item[1].get("weight", 0.0)),
                        reverse=True,
                    )[0]
                    seen.add(next_node)
                    path_nodes.append(next_node)
                    path_edges.append(edge)
                    current = next_node
                if len(path_nodes) >= 3:
                    rendered = []
                    for idx, node_id in enumerate(path_nodes):
                        rendered.append(labels.get(node_id, node_id)[:120])
                        if idx < len(path_edges):
                            rendered.append(f"--{path_edges[idx].get('relation_type')}-->")
                    paths.append(f"- {candidate.id}: " + " ".join(rendered))
            if len(paths) >= max_paths:
                break
        if not paths:
            return ""
        return "\n".join(["Long-path candidates for Wisdom synthesis:", *paths])

    @staticmethod
    def _graph_provenance(candidates: list[SubgraphCandidate], mode: str) -> dict:
        return {
            "mode": mode,
            "subgraph_ids": [candidate.id for candidate in candidates],
            "anchors": list(dict.fromkeys(candidate.anchor_label for candidate in candidates)),
            "node_ids": list(
                dict.fromkeys(
                    str(node.get("id"))
                    for candidate in candidates
                    for node in candidate.nodes
                    if node.get("id")
                )
            ),
            "edge_ids": list(
                dict.fromkeys(
                    str(edge.get("id"))
                    for candidate in candidates
                    for edge in candidate.edges
                    if edge.get("id")
                )
            ),
        }

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
