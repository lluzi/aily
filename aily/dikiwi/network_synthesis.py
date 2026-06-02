"""Graph-triggered subgraph selection for DIKIWI synthesis stages."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aily.config import SETTINGS
from aily.sessions.dikiwi_mind import InformationNode, KnowledgeLink

if TYPE_CHECKING:
    from aily.dikiwi.agents.context import AgentContext

logger = logging.getLogger(__name__)

BOOKKEEPING_TAGS = {
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

BOOKKEEPING_RELATIONS = {"has_tag"}


@dataclass
class SubgraphCandidate:
    """A meaningful graph neighborhood that may deserve DIKIWI synthesis."""

    id: str
    anchor_id: str
    anchor_label: str
    anchor_type: str
    reason: str
    score: float
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    changed_node_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_prompt_context(self, max_nodes: int = 18, max_edges: int = 24) -> str:
        lines = [
            f"Subgraph {self.id}",
            f"Anchor: {self.anchor_label} ({self.anchor_type})",
            f"Trigger: {self.reason}",
            f"Score: {self.score:.2f}",
            "Nodes:",
        ]
        for idx, node in enumerate(self.nodes[:max_nodes], start=1):
            marker = "*" if node.get("id") in set(self.changed_node_ids) else "-"
            source = _source_label(node)
            lines.append(
                f"{marker} E{idx}: [{node.get('type', 'node')}] "
                f"{node.get('label', '')[:220]} | source={source}"
            )
        if self.edges:
            lines.append("Edges:")
            node_labels = {node.get("id"): node.get("label", "")[:60] for node in self.nodes}
            for edge in self.edges[:max_edges]:
                src = node_labels.get(edge.get("source_node_id"), edge.get("source_node_id", ""))
                tgt = node_labels.get(edge.get("target_node_id"), edge.get("target_node_id", ""))
                lines.append(
                    f"- {src} --{edge.get('relation_type')}({float(edge.get('weight', 0.0)):.2f})--> {tgt}"
                )
        return "\n".join(lines)


@dataclass
class GraphChangeAssessment:
    """Decision about whether graph changes justify higher-order generation."""

    triggered: bool
    reason: str
    score: float = 0.0
    candidates: list[SubgraphCandidate] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        if not self.candidates:
            return f"No synthesis-grade subgraph found. Reason: {self.reason}"
        return "\n\n".join(candidate.to_prompt_context() for candidate in self.candidates)


class NetworkSynthesisSelector:
    """Select graph neighborhoods where new information changes the corpus network.

    This is intentionally lightweight: it uses tag bridges and local graph expansion
    already present in GraphDB. The selector favors changed nodes that attach to
    existing neighborhoods, dense new edges, and source diversity.
    """

    def __init__(
        self,
        min_nodes: int | None = None,
        trigger_score: float | None = None,
        max_candidate_nodes: int | None = None,
    ) -> None:
        self.min_nodes = min_nodes if min_nodes is not None else SETTINGS.dikiwi_network_min_nodes
        self.trigger_score = (
            trigger_score if trigger_score is not None else SETTINGS.dikiwi_network_trigger_score
        )
        self.max_candidate_nodes = (
            max_candidate_nodes
            if max_candidate_nodes is not None
            else SETTINGS.dikiwi_network_max_candidate_nodes
        )

    async def assess(
        self,
        ctx: "AgentContext",
        current_nodes: list[InformationNode],
        current_links: list[KnowledgeLink],
    ) -> GraphChangeAssessment:
        if not ctx.graph_db:
            return GraphChangeAssessment(
                False,
                "GraphDB unavailable; higher DIKIWI synthesis requires a persisted information graph",
                metrics={"requires_persisted_graph": True},
            )

        if not current_nodes:
            return GraphChangeAssessment(False, "No current information nodes to attach to graph")

        current_node_ids = {node.id for node in current_nodes}
        candidates: list[SubgraphCandidate] = []
        seen_candidate_ids: set[str] = set()

        semantic_tags = sorted({
            tag for node in current_nodes for tag in node.tags if _is_semantic_tag(tag)
        })
        for tag in semantic_tags:
            tag_id = f"tag_{tag}"
            try:
                tag_neighbors = await ctx.graph_db.get_neighbors(
                    tag_id,
                    relation_type="has_tag",
                    direction="in",
                    limit=max(self.max_candidate_nodes * 3, 30),
                )
            except Exception as exc:
                logger.warning("[DIKIWI] Failed to inspect tag neighborhood %s: %s", tag, exc)
                continue

            info_neighbors = [
                node
                for node in tag_neighbors
                if node.get("type") == "information" and not _is_generic_information_node(node)
            ]
            if not info_neighbors:
                continue
            await self._attach_properties(ctx, info_neighbors)
            info_neighbors = [
                node for node in info_neighbors if not _is_generic_information_node(node)
            ]

            ordered_nodes = self._rank_nodes(info_neighbors, current_node_ids)
            # Tags are useful retrieval anchors, but they are not evidence nodes.
            # Keep only information nodes in synthesis candidates.
            subgraph_nodes = ordered_nodes[: self.max_candidate_nodes]
            node_ids = [node["id"] for node in subgraph_nodes]

            try:
                edges = _semantic_edges(
                    await ctx.graph_db.get_edges_for_nodes(node_ids, limit=200),
                    {str(node.get("id")) for node in subgraph_nodes if node.get("type") == "information"},
                )
            except Exception as exc:
                logger.warning("[DIKIWI] Failed to load subgraph edges for %s: %s", tag, exc)
                edges = []

            candidate = self._build_candidate(
                anchor_id=tag_id,
                anchor_label=tag,
                anchor_type="tag",
                nodes=subgraph_nodes,
                edges=edges,
                changed_node_ids=current_node_ids,
                current_links=current_links,
            )
            if candidate.id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate.id)
            if (
                len([n for n in candidate.nodes if n.get("type") == "information"]) >= self.min_nodes
                and candidate.metrics.get("semantic_edge_count", 0) > 0
            ):
                candidates.append(candidate)

        for candidate in self._current_link_component_candidates(
            current_nodes,
            current_links,
            current_node_ids,
        ):
            if candidate.id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate.id)
            if (
                len([n for n in candidate.nodes if n.get("type") == "information"]) >= self.min_nodes
                and candidate.metrics.get("semantic_edge_count", 0) > 0
            ):
                candidates.append(candidate)

        if not candidates:
            return GraphChangeAssessment(
                False,
                "No synthesis-grade information subgraph reached minimum size and semantic edge requirements",
                metrics={
                    "requires_existing_information_neighbor": True,
                    "requires_semantic_information_edges": True,
                    "semantic_tags_considered": semantic_tags,
                },
            )

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        best_score = candidates[0].score
        triggered = best_score >= self.trigger_score
        reason = (
            "Graph change crossed synthesis threshold"
            if triggered
            else (
                f"Best subgraph score {best_score:.2f} below threshold {self.trigger_score:.2f}"
                if best_score < self.trigger_score
                else "No synthesis-grade subgraph reached threshold"
            )
        )
        return GraphChangeAssessment(
            triggered=triggered,
            reason=reason,
            score=best_score,
            candidates=candidates[:3],
            metrics={
                "candidate_count": len(candidates),
                "trigger_score": self.trigger_score,
                "min_nodes": self.min_nodes,
                "allows_cold_start_batch_subgraphs": True,
            },
        )

    def _build_candidate(
        self,
        *,
        anchor_id: str,
        anchor_label: str,
        anchor_type: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        changed_node_ids: set[str],
        current_links: list[KnowledgeLink],
    ) -> SubgraphCandidate:
        node_ids = {node.get("id") for node in nodes}
        changed_count = len(node_ids & changed_node_ids)
        existing_count = len([node for node in nodes if node.get("id") not in changed_node_ids])
        existing_information_count = len(
            [
                node
                for node in nodes
                if node.get("id") not in changed_node_ids and node.get("type") == "information"
            ]
        )
        source_count = len({_source_label(node) for node in nodes if node.get("type") == "information"})
        semantic_edge_count = len(_semantic_edges(edges, {str(node.get("id")) for node in nodes}))
        density = semantic_edge_count / max(len(nodes), 1)
        score = (
            changed_count * 2.0
            + min(existing_count, 10) * 0.45
            + source_count * 0.75
            + min(semantic_edge_count, 20) * 0.35
            + sum(link.strength for link in current_links) * 0.5
        )
        candidate_hash = hashlib.sha1(
            "|".join([anchor_id, *sorted(str(node_id) for node_id in node_ids)]).encode("utf-8")
        ).hexdigest()[:10]
        reason = (
            f"{changed_count} changed node(s), {existing_count} existing neighbor(s), "
            f"{semantic_edge_count} semantic edge(s), {source_count} source cluster(s)"
        )
        return SubgraphCandidate(
            id=f"subgraph_{candidate_hash}",
            anchor_id=anchor_id,
            anchor_label=anchor_label,
            anchor_type=anchor_type,
            reason=reason,
            score=score,
            nodes=nodes,
            edges=edges,
            changed_node_ids=sorted(changed_node_ids),
            metrics={
                "changed_nodes": changed_count,
                "existing_neighbors": existing_count,
                "existing_information_neighbors": existing_information_count,
                "edge_count": len(edges),
                "semantic_edge_count": semantic_edge_count,
                "source_count": source_count,
                "density": round(density, 3),
            },
        )

    @staticmethod
    def _rank_nodes(nodes: list[dict[str, Any]], current_node_ids: set[str]) -> list[dict[str, Any]]:
        return sorted(
            nodes,
            key=lambda node: (
                0 if node.get("id") in current_node_ids else 1,
                str(node.get("created_at") or ""),
            ),
        )

    async def _attach_properties(self, ctx: "AgentContext", nodes: list[dict[str, Any]]) -> None:
        if not ctx.graph_db:
            return
        for node in nodes:
            try:
                node["properties"] = await ctx.graph_db.get_node_properties(str(node.get("id", "")))
            except Exception:
                node["properties"] = {}

    def _current_link_component_candidates(
        self,
        current_nodes: list[InformationNode],
        current_links: list[KnowledgeLink],
        current_node_ids: set[str],
    ) -> list[SubgraphCandidate]:
        """Build candidates from semantic information-link components.

        Tag neighborhoods are useful but too brittle for cold-start batches:
        a meaningful graph may be connected by `depends_on`, `enables`, or
        `tradeoff_with` links without three nodes sharing one exact tag.
        """
        node_by_id = {node.id: node for node in current_nodes if node.id}
        semantic_links = [
            link
            for link in current_links
            if link.source_id in node_by_id
            and link.target_id in node_by_id
            and link.source_id != link.target_id
            and str(link.relation_type or "").strip() not in BOOKKEEPING_RELATIONS
        ]
        if not semantic_links:
            return []

        adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
        for link in semantic_links:
            adjacency.setdefault(link.source_id, set()).add(link.target_id)
            adjacency.setdefault(link.target_id, set()).add(link.source_id)

        candidates: list[SubgraphCandidate] = []
        visited: set[str] = set()
        for start_id in sorted(adjacency):
            if start_id in visited:
                continue
            stack = [start_id]
            component_ids: set[str] = set()
            while stack:
                node_id = stack.pop()
                if node_id in visited:
                    continue
                visited.add(node_id)
                component_ids.add(node_id)
                stack.extend(sorted(adjacency.get(node_id, set()) - visited))

            component_links = [
                link
                for link in semantic_links
                if link.source_id in component_ids and link.target_id in component_ids
            ]
            if len(component_ids) < self.min_nodes or not component_links:
                continue

            ordered_nodes = sorted(
                (node_by_id[node_id] for node_id in component_ids),
                key=lambda node: (0 if node.id in current_node_ids else 1, node.concept or node.content),
            )[: self.max_candidate_nodes]
            kept_ids = {node.id for node in ordered_nodes}
            node_dicts = [
                {
                    "id": node.id,
                    "type": "information",
                    "label": (node.concept or node.content)[:200],
                    "source": "dikiwi_batch",
                    "created_at": "",
                    "properties": {
                        "data_point_id": node.data_point_id,
                        "data_point_ids": node.data_point_ids,
                        "concept": node.concept,
                        "tags": node.tags,
                        "info_type": node.info_type,
                        "domain": node.domain,
                        "source_evidence": node.source_evidence,
                    },
                }
                for node in ordered_nodes
            ]
            edge_dicts = [
                {
                    "id": f"edge_{link.source_id}_{link.target_id}_{link.relation_type}",
                    "source_node_id": link.source_id,
                    "target_node_id": link.target_id,
                    "relation_type": link.relation_type,
                    "weight": link.strength,
                    "source": "dikiwi_current_links",
                    "created_at": "",
                }
                for link in component_links
                if link.source_id in kept_ids and link.target_id in kept_ids
            ]
            if not edge_dicts:
                continue
            candidate = self._build_candidate(
                anchor_id=f"component_{start_id}",
                anchor_label="semantic information component",
                anchor_type="information_component",
                nodes=node_dicts,
                edges=edge_dicts,
                changed_node_ids=current_node_ids,
                current_links=component_links,
            )
            candidates.append(candidate)
        return candidates


def candidate_nodes_to_information(candidates: list[SubgraphCandidate]) -> list[InformationNode]:
    """Convert selected information graph nodes into InformationNode objects."""
    converted: list[InformationNode] = []
    seen: set[str] = set()
    for candidate in candidates:
        for node in candidate.nodes:
            node_id = str(node.get("id", ""))
            if (
                not node_id
                or node_id in seen
                or node.get("type") != "information"
                or _is_generic_information_node(node)
            ):
                continue
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            converted.append(
                InformationNode(
                    id=node_id,
                    data_point_id=str(props.get("data_point_id", "")),
                    content=str(node.get("label", "")),
                    tags=list(props.get("tags", [])) if isinstance(props.get("tags"), list) else [],
                    info_type=str(props.get("info_type", "")),
                    domain=str(props.get("domain", "")),
                    concept=str(props.get("concept", "")),
                )
            )
            seen.add(node_id)
    return converted


def _source_label(node: dict[str, Any]) -> str:
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    source_paths = props.get("source_paths") if isinstance(props, dict) else None
    if isinstance(source_paths, list) and source_paths:
        return str(source_paths[0])
    return str(node.get("source") or "unknown")


def _is_semantic_tag(tag: object) -> bool:
    cleaned = str(tag or "").strip().strip("#")
    if not cleaned:
        return False
    normalized = cleaned.lower().replace(" ", "_").replace("-", "_")
    if normalized in BOOKKEEPING_TAGS:
        return False
    if normalized.startswith(("page_", "slide_", "type:", "has:")):
        return False
    if "/" in cleaned or "\\" in cleaned or cleaned.endswith((".pdf", ".ppt", ".pptx", ".doc", ".docx")):
        return False
    return len(cleaned) >= 3


def _is_generic_information_node(node: dict[str, Any]) -> bool:
    label = str(node.get("label") or "").strip()
    if not label:
        return True
    normalized = label.lower().replace("_", " ").replace("-", " ")
    if normalized.startswith(("page ", "slide ")):
        suffix = normalized.split(" ", 1)[1].strip()
        if suffix.isdigit():
            return True
    if normalized in {"page", "slide", "visual", "image", "figure", "table"}:
        return True

    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    concept = str(props.get("concept") or "").strip()
    if concept:
        concept_norm = concept.lower().replace("_", " ").replace("-", " ")
        if concept_norm.startswith(("page ", "slide ")) and concept_norm.split(" ", 1)[1].strip().isdigit():
            return True
    tags = props.get("tags") if isinstance(props, dict) else []
    if isinstance(tags, list) and tags and not any(_is_semantic_tag(tag) for tag in tags):
        return True
    return False


def _semantic_edges(edges: list[dict[str, Any]], info_node_ids: set[str]) -> list[dict[str, Any]]:
    semantic: list[dict[str, Any]] = []
    for edge in edges:
        source_id = str(edge.get("source_node_id", ""))
        target_id = str(edge.get("target_node_id", ""))
        relation = str(edge.get("relation_type", "")).strip()
        if not source_id or not target_id or source_id == target_id:
            continue
        if source_id not in info_node_ids or target_id not in info_node_ids:
            continue
        if relation in BOOKKEEPING_RELATIONS:
            continue
        semantic.append(edge)
    return semantic
