"""InformationAgent - Stage 2: INFORMATION classification and tagging."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import chat_json
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DataPoint, DikiwiStage, InformationNode, StageResult

logger = logging.getLogger(__name__)

GRAPH_BOOKKEEPING_TAGS = {
    "action",
    "applies_to",
    "contradicts",
    "data",
    "depends_on",
    "dikiwi",
    "eda",
    "enables",
    "example_of",
    "fact",
    "general",
    "has_tag",
    "impact",
    "information",
    "input",
    "insight",
    "knowledge",
    "medium",
    "mineru",
    "page",
    "part_of",
    "pattern",
    "pdf",
    "pending",
    "principle",
    "proposal",
    "relates_to",
    "slide",
    "supports",
    "tradeoff_with",
    "unclassified",
    "visual",
    "wisdom",
}


class InformationAgent(DikiwiAgent):
    """Stage 2: Cluster data points into classified information nodes."""

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            # Retrieve prior stage results
            data_result = self._find_stage_result(ctx, DikiwiStage.DATA)
            if not data_result:
                raise RuntimeError("DATA stage result not found in context")

            data_points: list[DataPoint] = data_result.data.get("data_points", [])
            data_note_id: str = data_result.data.get("data_note_id", "")
            data_note_ids: list[str] = data_result.data.get("data_note_ids", [])
            data_note_id_map: dict[str, str] = data_result.data.get("data_note_id_map", {})
            source = ctx.drop.source

            logger.info(
                "[DIKIWI] INFORMATION: %d data points, %d data_note_ids, first=%s",
                len(data_points), len(data_note_ids), data_note_ids[0] if data_note_ids else "(none)"
            )

            clusters = await self._llm_cluster_batch(data_points, source, ctx)

            # Build a map from data_point chunk index to the corresponding data note id
            def _dp_to_data_note_id(dp_id: str) -> str:
                """Extract chunk index from dp_id (format: dp_{uuid}_{chunk_index}_{i}) and look up data_note_id."""
                mapped = data_note_id_map.get(dp_id)
                if mapped:
                    return mapped
                if not data_note_ids:
                    logger.warning("[DIKIWI] data_note_ids is empty, falling back to data_note_id=%s", data_note_id)
                    return data_note_id
                logger.warning("[DIKIWI] Could not map dp_id=%s to data_note_id", dp_id)
                return data_note_id

            info_nodes = self._build_information_nodes(data_points, clusters)

            if ctx.graph_db:
                for node in info_nodes:
                    await ctx.graph_db.insert_node(
                        node_id=node.id,
                        node_type="information",
                        label=(node.concept or node.content)[:200],
                        source=source,
                    )
                    await self._store_node_metadata(node, ctx)

            # Write information notes
            info_note_ids: dict[str, str] = {}
            if ctx.dikiwi_obsidian_writer and info_nodes:
                source_paths = ctx.drop.metadata.get("source_paths", [])
                for node in info_nodes:
                    try:
                        specific_data_note_ids = [
                            _dp_to_data_note_id(dp_id) for dp_id in (node.data_point_ids or [node.data_point_id])
                        ]
                        specific_data_note_id = specific_data_note_ids[0] if specific_data_note_ids else _dp_to_data_note_id(node.data_point_id)
                        nid = await ctx.dikiwi_obsidian_writer.write_information_note(
                            node,
                            specific_data_note_id,
                            source,
                            source_paths,
                            data_point_id=node.data_point_id,
                            data_note_ids=specific_data_note_ids,
                        )
                        info_note_ids[node.id] = nid
                    except Exception as e:
                        logger.warning("[DIKIWI] Failed to write information note: %s", e)

            # Add to memory
            if ctx.memory and info_nodes:
                ctx.memory.add_assistant(
                    f"STAGE 2 (INFORMATION) Complete: Classified {len(info_nodes)} information nodes. "
                    f"Domains: {', '.join(node.domain for node in info_nodes[:4])}"
                )

            processing_time = (time.time() - start) * 1000

            keywords = list({tag for node in info_nodes for tag in node.tags})

            return StageResult(
                stage=DikiwiStage.INFORMATION,
                success=True,
                items_processed=len(data_points),
                items_output=len(info_nodes),
                processing_time_ms=processing_time,
                data={
                    "information_nodes": info_nodes,
                    "info_note_ids": info_note_ids,
                    "keywords": keywords,
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.INFORMATION,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _llm_cluster_batch(
        self,
        data_points: list[DataPoint],
        source: str,
        ctx: AgentContext,
    ) -> list[dict]:
        if not data_points:
            return []

        memory_context = DikiwiPromptRegistry.render_memory(ctx.memory, limit=1200)
        messages = DikiwiPromptRegistry.information_clustering_batch(
            data_points=data_points,
            source=source,
            memory_context=memory_context,
        )
        stage_key = f"information:batch:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await chat_json(
                llm_client=ctx.llm_client,
                stage="information",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                budget=ctx.budget,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Batch information clustering failed: %s", exc)
            return self._fallback_clusters(data_points)

        if not isinstance(result, dict):
            return self._fallback_clusters(data_points)

        raw_clusters = result.get("clusters", [])
        clusters: list[dict] = []
        for item in raw_clusters:
            if not isinstance(item, dict):
                continue
            member_indices = sorted({
                idx
                for idx in item.get("member_indices", [])
                if isinstance(idx, int) and 0 <= idx < len(data_points)
            })
            if not member_indices:
                continue
            clusters.append(
                {
                    "canonical_title": str(item.get("canonical_title", "")).strip(),
                    "member_indices": member_indices,
                    "summary": str(item.get("summary", "")).strip(),
                    "tags": [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()][:6],
                    "info_type": str(item.get("info_type", "fact")).strip() or "fact",
                    "domain": self._clean_domain(str(item.get("domain", "general"))),
                    "source_evidence": [
                        str(e).strip()
                        for e in item.get("source_evidence", [])
                        if str(e).strip()
                    ][:6],
                    "confidence": float(item.get("confidence", 0.8)),
                }
            )

        return clusters or self._fallback_clusters(data_points)

    def _build_information_nodes(self, data_points: list[DataPoint], clusters: list[dict]) -> list[InformationNode]:
        info_nodes: list[InformationNode] = []
        assigned: set[int] = set()

        for cluster in clusters:
            member_indices = [idx for idx in cluster.get("member_indices", []) if idx not in assigned]
            if not member_indices:
                continue
            members = [data_points[idx] for idx in member_indices]
            for idx in member_indices:
                assigned.add(idx)

            concept = cluster.get("canonical_title") or self._best_cluster_title(members)
            info_nodes.append(
                InformationNode(
                    id=f"info_{uuid.uuid4().hex[:8]}",
                    data_point_id=members[0].id,
                    data_point_ids=[member.id for member in members],
                    content=cluster.get("summary") or self._fallback_cluster_summary(concept, members),
                    concept=concept,
                    tags=self._merge_tags(cluster.get("tags", []), members),
                    info_type=cluster.get("info_type", "fact"),
                    domain=self._clean_domain(cluster.get("domain", "general")),
                    source_evidence=self._merge_source_evidence(cluster.get("source_evidence", []), members),
                    confidence=float(cluster.get("confidence", 0.8)),
                )
            )

        for index, data_point in enumerate(data_points):
            if index in assigned:
                continue
            concept = self._best_cluster_title([data_point])
            info_nodes.append(
                InformationNode(
                    id=f"info_{uuid.uuid4().hex[:8]}",
                    data_point_id=data_point.id,
                    data_point_ids=[data_point.id],
                    content=self._fallback_cluster_summary(concept, [data_point]),
                    concept=concept,
                    tags=self._merge_tags([], [data_point]),
                    info_type="evidence" if data_point.modality == "visual" else "fact",
                    domain="general",
                    source_evidence=self._merge_source_evidence([], [data_point]),
                    confidence=data_point.confidence,
                )
            )

        return info_nodes

    def _fallback_clusters(self, data_points: list[DataPoint]) -> list[dict]:
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index, data_point in enumerate(data_points):
            title = self._normalize_cluster_key(data_point.concept or data_point.content)
            grouped[(title, data_point.modality or "text")].append(index)

        clusters: list[dict] = []
        for indices in grouped.values():
            members = [data_points[idx] for idx in indices]
            concept = self._best_cluster_title(members)
            clusters.append(
                {
                    "canonical_title": concept,
                    "member_indices": indices,
                    "summary": self._fallback_cluster_summary(concept, members),
                    "tags": self._merge_tags([], members),
                    "info_type": "evidence" if any(member.modality == "visual" for member in members) else "fact",
                    "domain": "general",
                    "source_evidence": self._merge_source_evidence([], members),
                    "confidence": sum(member.confidence for member in members) / max(len(members), 1),
                }
            )
        return clusters

    @staticmethod
    def _normalize_cluster_key(value: str) -> str:
        normalized = " ".join(str(value).lower().split())
        return normalized[:120] if normalized else "untitled"

    @staticmethod
    def _best_cluster_title(members: list[DataPoint]) -> str:
        for member in members:
            if member.concept:
                return member.concept
        return max((member.content for member in members if member.content), key=len, default="Untitled information")[:120]

    def _fallback_cluster_summary(self, concept: str, members: list[DataPoint]) -> str:
        if len(members) == 1:
            return members[0].content

        lead = max(members, key=lambda member: len(member.content))
        evidence = self._merge_source_evidence([], members)
        summary = f"{concept}: {lead.content}"
        if evidence:
            summary += f" Supporting evidence includes {', '.join(evidence[:3])}."
        else:
            summary += f" This information cluster is supported by {len(members)} related datapoints."
        return summary

    @staticmethod
    def _merge_tags(initial_tags: list[str], members: list[DataPoint]) -> list[str]:
        tags: list[str] = []
        for tag in initial_tags:
            cleaned = str(tag).strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
        for member in members:
            if member.modality == "visual" and "visual" not in tags:
                tags.append("visual")
            if member.visual_type and member.visual_type not in tags:
                tags.append(member.visual_type)
        return tags[:6]

    @staticmethod
    def _merge_source_evidence(initial_evidence: list[str], members: list[DataPoint]) -> list[str]:
        evidence: list[str] = []
        for item in initial_evidence:
            cleaned = str(item).strip()
            if cleaned and cleaned not in evidence:
                evidence.append(cleaned)
        for member in members:
            for item in getattr(member, "source_evidence", []):
                cleaned = str(item).strip()
                if cleaned and cleaned not in evidence:
                    evidence.append(cleaned)
            if member.source_page is not None:
                page_marker = f"page {member.source_page}"
                if page_marker not in evidence:
                    evidence.append(page_marker)
        return evidence[:6]

    async def _store_node_metadata(self, node: InformationNode, ctx: AgentContext) -> None:
        if not ctx.graph_db:
            return
        source_paths = ctx.drop.metadata.get("source_paths", []) if getattr(ctx.drop, "metadata", None) else []
        try:
            await ctx.graph_db.set_node_property(node.id, "data_point_id", node.data_point_id)
            await ctx.graph_db.set_node_property(node.id, "data_point_ids", node.data_point_ids)
            await ctx.graph_db.set_node_property(node.id, "concept", node.concept)
            await ctx.graph_db.set_node_property(node.id, "tags", node.tags)
            await ctx.graph_db.set_node_property(node.id, "info_type", node.info_type)
            await ctx.graph_db.set_node_property(node.id, "domain", node.domain)
            await ctx.graph_db.set_node_property(node.id, "source_evidence", node.source_evidence)
            await ctx.graph_db.set_node_property(node.id, "source_paths", source_paths)
            await ctx.graph_db.set_node_property(node.id, "pipeline_id", ctx.pipeline_id)
        except AttributeError: pass  # Property write unavailable on test GraphDB mocks
        for tag in self._semantic_graph_tags(node.tags):
            await ctx.graph_db.insert_node(
                node_id=f"tag_{tag}", node_type="tag", label=tag, source="dikiwi"
            )
            await ctx.graph_db.insert_edge(
                edge_id=f"edge_{uuid.uuid4().hex[:8]}",
                source_node_id=node.id,
                target_node_id=f"tag_{tag}",
                relation_type="has_tag",
                source="dikiwi",
                weight=1.0,
            )
        try:
            raw_log_id = str(source_paths[0]) if source_paths else ctx.pipeline_id
            await ctx.graph_db.insert_occurrence(
                occurrence_id=f"occ_{uuid.uuid4().hex[:8]}",
                node_id=node.id,
                raw_log_id=raw_log_id,
            )
        except AttributeError: pass  # Property write unavailable on test GraphDB mocks

    @staticmethod
    def _clean_domain(domain: str) -> str:
        return domain.split("|")[0].strip() if "|" in domain else domain.strip()

    @staticmethod
    def _semantic_graph_tags(tags: list[str]) -> list[str]:
        """Only persist content-bearing tags as graph bridge nodes."""
        semantic: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            tag = str(raw or "").strip().strip("#")
            if not tag:
                continue
            normalized = tag.lower().replace(" ", "_").replace("-", "_")
            if normalized in GRAPH_BOOKKEEPING_TAGS:
                continue
            if normalized.startswith(("page_", "slide_", "type:", "has:")):
                continue
            if "/" in tag or "\\" in tag or tag.endswith((".pdf", ".ppt", ".pptx", ".doc", ".docx")):
                continue
            if len(tag) < 3:
                continue
            if tag not in seen:
                semantic.append(tag)
                seen.add(tag)
        # Keep rich tags as node properties, but only materialize one tag bridge
        # edge per information node. High-volume tag fanout made the persistent
        # graph look connected while drowning out actual DIKIWI knowledge links.
        return semantic[:1]

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
