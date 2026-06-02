"""Graph-driven incremental DIKIWI pipeline — processes only what changed.

When new files arrive daily, this runs the full 6-stage pipeline but scopes
higher stages (KNOWLEDGE → IMPACT) only to affected graph neighborhoods.
Uses NetworkSynthesisSelector (changed subgraph detection) + ObsidianCLI
(grounded_in staleness scanning) to minimize LLM calls while keeping the
vault consistent.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aily.dikiwi.agents.obsidian_cli import ObsidianCLI

if TYPE_CHECKING:
    from aily.dikiwi.agents.context import AgentContext
    from aily.dikiwi.network_synthesis import NetworkSynthesisSelector, SubgraphCandidate
    from aily.graph.db import GraphDB

logger = logging.getLogger(__name__)


@dataclass
class IncrementalResult:
    """Result of an incremental pipeline run."""

    new_files: int
    new_data_points: int
    new_info_nodes: int
    affected_subgraphs: int
    stale_insights: int
    stale_wisdom: int
    stale_impacts: int
    regenerated_insights: int
    regenerated_wisdom: int
    regenerated_impacts: int
    skipped_insights: int
    skipped_wisdom: int
    skipped_impacts: int
    elapsed_seconds: float
    error: str = ""


@dataclass
class _ContentHashCache:
    """Tracks content hashes to detect meaningful changes before rewriting notes."""

    _hashes: dict[str, str] = field(default_factory=dict)

    def has_changed(self, note_id: str, new_content: str) -> bool:
        h = hashlib.sha1(new_content.encode()).hexdigest()
        if note_id not in self._hashes:
            self._hashes[note_id] = h
            return True  # first time seeing this note
        if self._hashes[note_id] != h:
            self._hashes[note_id] = h
            return True
        return False

    @classmethod
    def from_existing_note(cls, vault_path: Path, note_path: str) -> str | None:
        """Read existing note and return its content hash."""
        full_path = vault_path / note_path
        if not full_path.exists():
            return None
        content = full_path.read_text(encoding="utf-8")
        return hashlib.sha1(content.encode()).hexdigest()


class IncrementalOrchestrator:
    """Orchestrate incremental DIKIWI processing from new files.

    Preserves existing higher-stage output for unchanged graph regions.
    Only regenerates notes for graph neighborhoods where information nodes
    or their relationships have changed.
    """

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.cli = ObsidianCLI(vault_path=vault_path)
        self._content_cache = _ContentHashCache()
        self._force: bool = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def ingest(
        self,
        new_file_paths: list[Path],
        ctx: "AgentContext",
        *,
        force: bool = False,
    ) -> IncrementalResult:
        """Process new files through the incremental DIKIWI pipeline.

        Args:
            new_file_paths: Markdown files to process
            ctx: Agent context with LLM client, GraphDB, writer
            force: If True, bypass the graph threshold and always run
                   higher stages even if the change is small
        """
        t0 = time.monotonic()
        self._force = force

        if not new_file_paths:
            return IncrementalResult(new_files=0, new_data_points=0, new_info_nodes=0,
                                     affected_subgraphs=0, stale_insights=0, stale_wisdom=0,
                                     stale_impacts=0, regenerated_insights=0, regenerated_wisdom=0,
                                     regenerated_impacts=0, skipped_insights=0, skipped_wisdom=0,
                                     skipped_impacts=0, elapsed_seconds=0)

        try:
            # Step 1-2: DATA + INFORMATION for new content only
            new_node_ids, subgraph_candidates = await self._run_lower_stages(new_file_paths, ctx)

            if not new_node_ids:
                return IncrementalResult(
                    new_files=len(new_file_paths), new_data_points=0, new_info_nodes=0,
                    affected_subgraphs=0, stale_insights=0, stale_wisdom=0, stale_impacts=0,
                    regenerated_insights=0, regenerated_wisdom=0, regenerated_impacts=0,
                    skipped_insights=0, skipped_wisdom=0, skipped_impacts=0,
                    elapsed_seconds=round(time.monotonic() - t0, 2),
                )

            # Step 3: Find stale notes via vault's grounded_in
            changed_ids = set(new_node_ids)
            for c in subgraph_candidates:
                changed_ids.update(c.changed_node_ids)

            stale_insights = self._find_stale_notes(changed_ids, stage_filter="insight")
            stale_wisdom = self._find_stale_notes(changed_ids, stage_filter="wisdom")
            stale_impacts = self._find_stale_notes(changed_ids, stage_filter="impact")

            # Step 4-6: Regenerate stale higher-stage notes
            result = await self._run_higher_stages(
                ctx, subgraph_candidates,
                stale_insights, stale_wisdom, stale_impacts,
            )

            result.new_files = len(new_file_paths)
            result.new_info_nodes = len(new_node_ids)
            result.affected_subgraphs = len(subgraph_candidates)
            result.elapsed_seconds = round(time.monotonic() - t0, 2)
            return result

        except Exception as exc:
            logger.exception("[IncrementalOrchestrator] Pipeline failed: %s", exc)
            return IncrementalResult(
                new_files=len(new_file_paths), new_data_points=0, new_info_nodes=0,
                affected_subgraphs=0, stale_insights=0, stale_wisdom=0, stale_impacts=0,
                regenerated_insights=0, regenerated_wisdom=0, regenerated_impacts=0,
                skipped_insights=0, skipped_wisdom=0, skipped_impacts=0,
                elapsed_seconds=round(time.monotonic() - t0, 2), error=str(exc),
            )

    # ------------------------------------------------------------------
    # Lower stages: DATA + INFORMATION for new files
    # ------------------------------------------------------------------

    async def _run_lower_stages(
        self,
        new_file_paths: list[Path],
        ctx: "AgentContext",
    ) -> tuple[set[str], list["SubgraphCandidate"]]:
        """Run DATA and INFORMATION only for new files.

        Returns (new_info_node_ids, affected_subgraph_candidates).
        """
        from aily.dikiwi.agents.data_agent import DataAgent
        from aily.dikiwi.agents.information_agent import InformationAgent
        from aily.dikiwi.network_synthesis import NetworkSynthesisSelector
        from aily.sessions.dikiwi_mind import DikiwiStage, StageResult

        new_node_ids: set[str] = set()
        subgraph_candidates: list[SubgraphCandidate] = []

        for file_path in new_file_paths:
            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            # Save original content and replace with file content
            original_content = ctx.drop.content
            ctx.drop.content = content

            try:
                # DATA
                data_agent = DataAgent()
                data_result = await data_agent.execute(ctx)
                if not data_result.success or not data_result.data.get("data_points"):
                    continue

                data_points = data_result.data["data_points"]
                ctx.stage_results.append(data_result)

                # INFORMATION
                info_agent = InformationAgent()
                info_result = await info_agent.execute(ctx)
                ctx.stage_results.append(info_result)

                if not info_result.success:
                    continue

                info_nodes = info_result.data.get("information_nodes", [])
                for node in info_nodes:
                    new_node_ids.add(node.id)

                # KNOWLEDGE: use NetworkSynthesisSelector to detect affected subgraphs
                selector = NetworkSynthesisSelector()
                candidates = selector.assess(
                    new_info_nodes=info_nodes,
                    graph_db=ctx.graph_db,
                )
                subgraph_candidates.extend(candidates)

            finally:
                ctx.drop.content = original_content

        return new_node_ids, subgraph_candidates

    # ------------------------------------------------------------------
    # Staleness detection via vault
    # ------------------------------------------------------------------

    def _find_stale_notes(
        self,
        changed_node_ids: set[str],
        stage_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all higher-stage notes whose grounded_in references changed nodes."""
        stale: dict[str, dict[str, Any]] = {}  # dedup by dikiwi_id
        for node_id in changed_node_ids:
            results = self.cli.search_by_frontmatter("grounded_in", node_id)
            for r in results:
                if stage_filter and r.get("stage") != stage_filter:
                    continue
                did = r.get("dikiwi_id", "")
                if did and did not in stale:
                    stale[did] = r
        return list(stale.values())

    # ------------------------------------------------------------------
    # Higher stages: INSIGHT → WISDOM → IMPACT (incremental)
    # ------------------------------------------------------------------

    async def _run_higher_stages(
        self,
        ctx: "AgentContext",
        subgraph_candidates: list["SubgraphCandidate"],
        stale_insights: list[dict[str, Any]],
        stale_wisdom: list[dict[str, Any]],
        stale_impacts: list[dict[str, Any]],
    ) -> IncrementalResult:
        """Run INSIGHT, WISDOM, IMPACT only for affected graph areas."""
        from aily.dikiwi.agents.insight_agent import InsightAgent
        from aily.dikiwi.agents.wisdom_agent import WisdomAgent
        from aily.dikiwi.agents.impact_agent import ImpactAgent
        from aily.sessions.dikiwi_mind import DikiwiStage, StageResult

        result = IncrementalResult(
            new_files=0, new_data_points=0, new_info_nodes=0,
            affected_subgraphs=len(subgraph_candidates),
            stale_insights=len(stale_insights), stale_wisdom=len(stale_wisdom),
            stale_impacts=len(stale_impacts),
            regenerated_insights=0, regenerated_wisdom=0, regenerated_impacts=0,
            skipped_insights=0, skipped_wisdom=0, skipped_impacts=0,
            elapsed_seconds=0,
        )

        if not subgraph_candidates:
            return result

        # --- INSIGHT ---
        insight_agent = InsightAgent()
        regenerated_insight_ids: set[str] = set()
        for candidate in subgraph_candidates:
            existing = self._find_existing_insight(candidate.id)
            if not self._force and existing and existing.get("dikiwi_id") not in [s.get("dikiwi_id") for s in stale_insights]:
                result.skipped_insights += 1
                continue
            try:
                ins_result = await insight_agent.execute(ctx)
                ctx.stage_results.append(ins_result)
                if ins_result.success:
                    result.regenerated_insights += 1
                    for ins in ins_result.data.get("insights", []):
                        regenerated_insight_ids.add(ins.id)
            except Exception as exc:
                logger.warning("[Incremental] Insight generation failed for subgraph %s: %s", candidate.id, exc)

        if self._force or result.regenerated_insights > 0:
            # --- WISDOM (triggered by new/changed insights, or forced) ---
            wisdom_agent = WisdomAgent()
            for stale_w in stale_wisdom:
                try:
                    wis_result = await wisdom_agent.execute(ctx)
                    ctx.stage_results.append(wis_result)
                    if wis_result.success:
                        result.regenerated_wisdom += 1
                except Exception as exc:
                    logger.warning("[Incremental] Wisdom synthesis failed: %s", exc)
            # Also run on completely new insight subgraphs
            existing_wisdom_set = {w.get("dikiwi_id") for w in stale_wisdom}
            new_insight_needs = [c for c in subgraph_candidates
                                if not self._find_existing_insight(c.id)]
            if new_insight_needs:
                try:
                    wis_result = await wisdom_agent.execute(ctx)
                    ctx.stage_results.append(wis_result)
                    if wis_result.success:
                        result.regenerated_wisdom += 1
                except Exception as exc:
                    logger.warning("[Incremental] New wisdom synthesis failed: %s", exc)
        else:
            result.skipped_wisdom = len(stale_wisdom)

        if self._force or result.regenerated_wisdom > 0:
            # --- IMPACT (triggered by new/changed wisdom, or forced) ---
            impact_agent = ImpactAgent()
            for stale_i in stale_impacts:
                try:
                    imp_result = await impact_agent.execute(ctx)
                    ctx.stage_results.append(imp_result)
                    if imp_result.success:
                        result.regenerated_impacts += 1
                except Exception as exc:
                    logger.warning("[Incremental] Impact generation failed: %s", exc)
        else:
            result.skipped_impacts = len(stale_impacts)

        return result

    def _find_existing_insight(self, subgraph_id: str) -> dict[str, Any] | None:
        """Check if an insight note already covers this subgraph.

        Searches vault for insight notes whose graph_provenance includes the subgraph ID.
        """
        results = self.cli.search_by_frontmatter("dikiwi_level", "insight")
        for r in results:
            try:
                gp = r.get("frontmatter", {}).get("graph_provenance", "")
                if subgraph_id in str(gp):
                    return r
            except Exception:
                pass
        return None
