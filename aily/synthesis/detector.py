"""Detect ripe topics in the knowledge graph and recommend them for synthesis.

Detection is cheap and automatic; it only enqueues *candidates*. The two triggers
(a daily routine and a knowledge-growth threshold) both call ``detect()`` and feed
the one candidate queue. Volume is a wake-up signal — the readiness score still
decides which scoped subgraphs are actually ripe.
"""

from __future__ import annotations

import logging
from typing import Any

from aily.synthesis.candidates import (
    SynthesisCandidate,
    SynthesisCandidateStore,
    candidate_id_for_nodes,
)

logger = logging.getLogger(__name__)

_LAST_KNOWLEDGE_COUNT_KEY = "last_detection_knowledge_count"


def subgraphs_to_candidates(
    subgraphs: list[dict[str, Any]],
    *,
    trigger_score: float,
    min_nodes: int,
    detected_via: str,
) -> list[SynthesisCandidate]:
    """Pure: keep subgraphs above the readiness threshold and shape candidates."""
    candidates: list[SynthesisCandidate] = []
    for sg in subgraphs:
        node_ids = list(dict.fromkeys(str(n) for n in (sg.get("node_ids") or []) if n))
        if len(node_ids) < min_nodes:
            continue
        score = float(sg.get("score") or 0.0)
        if score < trigger_score:
            continue
        candidates.append(
            SynthesisCandidate(
                candidate_id=candidate_id_for_nodes(node_ids),
                scope_kind="subgraph",
                scope_label=str(sg.get("anchor_label") or "")[:200],
                node_ids=node_ids,
                readiness_score=score,
                reason=str(sg.get("reason") or ""),
                metrics=dict(sg.get("metrics") or {}),
                detected_via=detected_via,
            )
        )
    return candidates


class SynthesisDetector:
    """Scan the information graph for synthesis-ready subgraphs."""

    def __init__(
        self,
        graph_db: Any,
        store: SynthesisCandidateStore,
        *,
        trigger_score: float,
        min_nodes: int = 3,
        max_candidate_nodes: int = 18,
        max_subgraphs: int = 10,
    ) -> None:
        self.graph_db = graph_db
        self.store = store
        self.trigger_score = trigger_score
        self.min_nodes = min_nodes
        self.max_candidate_nodes = max_candidate_nodes
        self.max_subgraphs = max_subgraphs

    async def detect(self, *, detected_via: str, hours: int | None = None) -> dict[str, Any]:
        """Find ripe subgraphs and upsert them as candidates."""
        subgraphs = await self._fetch_ripe_subgraphs(hours)
        candidates = subgraphs_to_candidates(
            subgraphs,
            trigger_score=self.trigger_score,
            min_nodes=self.min_nodes,
            detected_via=detected_via,
        )
        summary = {
            "detected_via": detected_via,
            "subgraphs_examined": len(subgraphs),
            "created": 0,
            "refreshed": 0,
            "skipped": 0,
        }
        for candidate in candidates:
            result = await self.store.upsert(candidate)
            summary[result["action"]] = summary.get(result["action"], 0) + 1
        await self._mark_detection_ran()
        logger.info("[SYNTHESIS] detect(%s): %s", detected_via, summary)
        return summary

    async def knowledge_growth_since_last(self) -> int:
        current = await self._knowledge_count()
        last = int(await self.store.get_meta(_LAST_KNOWLEDGE_COUNT_KEY, "0") or 0)
        return max(0, current - last)

    async def should_run_threshold(self, threshold: int) -> bool:
        return await self.knowledge_growth_since_last() >= max(1, threshold)

    async def _mark_detection_ran(self) -> None:
        await self.store.set_meta(_LAST_KNOWLEDGE_COUNT_KEY, str(await self._knowledge_count()))

    async def _knowledge_count(self) -> int:
        try:
            return int(await self.graph_db.count_nodes_by_type("knowledge"))
        except Exception:
            return 0

    async def _fetch_ripe_subgraphs(self, hours: int | None) -> list[dict[str, Any]]:
        """Defensive graph I/O: hub anchors + their semantic information neighbors."""
        try:
            anchors = await self.graph_db.get_top_information_nodes_by_semantic_edge_count(
                hours=hours, limit=self.max_subgraphs
            )
        except Exception as exc:
            logger.warning("[SYNTHESIS] hub query failed: %s", exc)
            return []

        subgraphs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in anchors or []:
            anchor_id = str(anchor.get("id") or "")
            if not anchor_id:
                continue
            try:
                neighbors = await self.graph_db.get_neighbors(
                    anchor_id, direction="both", limit=self.max_candidate_nodes * 2
                )
            except Exception as exc:
                logger.warning("[SYNTHESIS] neighbor fetch failed for %s: %s", anchor_id, exc)
                continue

            semantic_neighbors = [
                n
                for n in (neighbors or [])
                if n.get("type") == "information"
                and str((n.get("edge") or {}).get("relation_type") or "") != "has_tag"
            ]
            node_ids = list(
                dict.fromkeys(
                    [anchor_id, *[str(n.get("id")) for n in semantic_neighbors if n.get("id")]]
                )
            )[: self.max_candidate_nodes]
            if len(node_ids) < self.min_nodes:
                continue
            key = candidate_id_for_nodes(node_ids)
            if key in seen:
                continue
            seen.add(key)

            sources = {str(anchor.get("source") or "")} | {
                str(n.get("source") or "") for n in semantic_neighbors
            }
            sources.discard("")
            semantic_links = len(semantic_neighbors)
            source_count = len(sources)
            score = float(anchor.get("edge_count") or semantic_links) + source_count * 0.75
            subgraphs.append(
                {
                    "anchor_label": anchor.get("label") or "",
                    "node_ids": node_ids,
                    "score": score,
                    "reason": (
                        f"{len(node_ids)} information nodes, ~{semantic_links} semantic links, "
                        f"{source_count} source(s)"
                    ),
                    "metrics": {
                        "anchor_id": anchor_id,
                        "semantic_neighbors": semantic_links,
                        "source_count": source_count,
                        "anchor_edge_count": anchor.get("edge_count"),
                    },
                }
            )
        return subgraphs
