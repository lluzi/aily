"""ResidualAgent (残差) - Post-pipeline vault analyst and proposal drafter."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import chat_json
from aily.dikiwi.agents.obsidian_cli import ObsidianCLI
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DikiwiStage, StageResult

logger = logging.getLogger(__name__)

WORKFLOW_PROPOSAL_PROPERTY_KEYS = {
    "status",
    "validation_attempts",
    "residual_report_path",
    "innovation_score",
    "innovation_score_details",
    "rejection_reason",
    "business_score",
    "business_score_details",
}


class ResidualAgent(DikiwiAgent):
    """Post-pipeline agent that analyzes the Obsidian vault and drafts formal reports.

    Residual runs after the IMPACT stage completes. It uses obsidian-cli to inspect
    the vault, queries the GraphDB for recent pipeline outputs, and synthesizes a
    formal report with concrete proposals for the Innovation and Entrepreneur minds.
    """

    _MAX_VAULT_NOTES = 20
    _MAX_NOTE_LENGTH = 2000
    _MAX_GRAPH_NODES = 30

    async def synthesize(self, ctx: AgentContext) -> StageResult:
        """Run Residual synthesis without persisting to external stores.

        Used inside the Reactor-Residual MAC loop for intermediate iterations.
        """
        start = time.time()

        try:
            impact_result = self._find_stage_result(ctx, DikiwiStage.IMPACT)
            if not impact_result or not impact_result.success:
                logger.info("[RESIDUAL] Skipping: IMPACT stage did not complete successfully")
                return StageResult(
                    stage=DikiwiStage.RESIDUAL,
                    success=True,
                    items_processed=0,
                    items_output=0,
                    processing_time_ms=0.0,
                    data={"skipped": True, "reason": "impact_not_successful"},
                )

            vault_name = ""
            vault_path = None
            if ctx.dikiwi_obsidian_writer:
                vault_path = getattr(ctx.dikiwi_obsidian_writer, "vault_path", None)
                if vault_path:
                    vault_name = Path(vault_path).name
            obsidian_cli = ObsidianCLI(
                vault_name=vault_name or None,
                vault_path=vault_path,
            )
            vault_excerpts = await self._gather_vault_excerpts(obsidian_cli, ctx)
            graph_nodes = await self._gather_graph_nodes(ctx)
            reactor_proposals = self._format_reactor_proposals(ctx)

            memory_context = (
                DikiwiPromptRegistry.render_memory(ctx.memory, limit=1200)
                if ctx.memory
                else ""
            )
            llm_result = await self._llm_synthesize(
                vault_excerpts=vault_excerpts,
                graph_nodes=graph_nodes,
                reactor_proposals=reactor_proposals,
                memory_context=memory_context,
                ctx=ctx,
            )

            proposals = llm_result.get("proposals", [])
            processing_time = (time.time() - start) * 1000

            return StageResult(
                stage=DikiwiStage.RESIDUAL,
                success=True,
                items_processed=1,
                items_output=len(proposals),
                processing_time_ms=processing_time,
                data={
                    "proposals": proposals,
                    "report_title": llm_result.get("report_title", ""),
                    "summary": llm_result.get("summary", ""),
                    "key_findings": llm_result.get("key_findings", []),
                    "reactor_synthesis": llm_result.get("reactor_synthesis", ""),
                    "report_note_id": "",
                },
            )

        except Exception as exc:
            logger.exception("[RESIDUAL] Synthesis failed: %s", exc)
            return StageResult(
                stage=DikiwiStage.RESIDUAL,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def execute(self, ctx: AgentContext) -> StageResult:
        """Run Residual and persist results to Obsidian and GraphDB."""
        result = await self.synthesize(ctx)

        if not result.success or result.data.get("skipped"):
            return result

        # Persistence layer
        llm_result = {
            "proposals": result.data.get("proposals", []),
            "report_title": result.data.get("report_title", ""),
            "summary": result.data.get("summary", ""),
            "key_findings": result.data.get("key_findings", []),
            "reactor_synthesis": result.data.get("reactor_synthesis", ""),
        }
        proposals = llm_result["proposals"]

        report_note_id = ""
        if ctx.dikiwi_obsidian_writer:
            try:
                report_note_id = await self._write_report(
                    llm_result=llm_result,
                    ctx=ctx,
                )
            except Exception as exc:
                logger.warning("[RESIDUAL] Failed to write report: %s", exc)

        if ctx.graph_db and proposals:
            try:
                await self._persist_proposals(proposals, report_note_id, ctx)
            except Exception as exc:
                logger.warning("[RESIDUAL] Failed to persist proposals: %s", exc)

        feedback = await self._read_rejection_feedback(ctx)
        if feedback and ctx.memory:
            ctx.memory.add_system(f"Residual feedback from past rejections:\n{feedback}")

        if ctx.memory:
            ctx.memory.add_assistant(
                f"RESIDUAL Complete: Drafted report with {len(proposals)} proposals."
            )

        result.data["report_note_id"] = report_note_id
        return result

    async def _gather_vault_excerpts(self, obsidian_cli: ObsidianCLI, ctx: AgentContext) -> str:
        """Collect recent DIKIWI note excerpts from the vault."""
        try:
            results = obsidian_cli.search("dikiwi_level:", limit=self._MAX_VAULT_NOTES)
        except Exception as exc:
            logger.warning("[RESIDUAL] Vault search failed: %s", exc)
            return ""

        if not results:
            logger.info("[RESIDUAL] No vault notes found via obsidian-cli")
            return ""

        excerpts: list[str] = []
        for item in results[: self._MAX_VAULT_NOTES]:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("filePath") or item.get("filename")
            if not path:
                continue

            # Read note content (truncate for LLM context)
            content = obsidian_cli.read_note(path)
            trimmed = content[: self._MAX_NOTE_LENGTH]
            if len(content) > self._MAX_NOTE_LENGTH:
                trimmed = trimmed + "\n... [truncated]"

            excerpts.append(f"--- Note: {path} ---\n{trimmed}")

        return "\n\n".join(excerpts)

    async def _gather_graph_nodes(self, ctx: AgentContext) -> str:
        """Query GraphDB for recent nodes produced by the pipeline."""
        if not ctx.graph_db:
            return ""

        try:
            nodes = await ctx.graph_db.get_nodes_within_hours(24)
        except Exception as exc:
            logger.warning("[RESIDUAL] GraphDB query failed: %s", exc)
            return ""

        # Filter to DIKIWI-relevant types and limit count
        relevant_types = {"information", "knowledge", "insight", "atomic_note", "residual_proposal"}
        filtered = [n for n in nodes if n.get("type") in relevant_types][: self._MAX_GRAPH_NODES]

        if not filtered:
            return ""

        lines: list[str] = []
        for node in filtered:
            lines.append(
                f"- [{node.get('type')}] {node.get('label', '')[:200]} "
                f"(source: {node.get('source', 'unknown')})"
            )

        return "\n".join(lines)

    async def _llm_synthesize(
        self,
        vault_excerpts: str,
        graph_nodes: str,
        reactor_proposals: str,
        memory_context: str,
        ctx: AgentContext,
    ) -> dict:
        """Call LLM to synthesize report and proposals."""
        messages = DikiwiPromptRegistry.residual_synthesis(
            vault_excerpts=vault_excerpts,
            graph_nodes=graph_nodes,
            reactor_proposals=reactor_proposals,
            memory_context=memory_context,
        )
        stage_key = f"residual:synth:{hashlib.sha1(vault_excerpts[:200].encode()).hexdigest()[:8]}"

        try:
            result = await chat_json(
                llm_client=ctx.llm_client,
                stage="residual",
                stage_key=stage_key,
                messages=messages,
                temperature=0.3,
                budget=ctx.budget,
            )
        except Exception as exc:
            logger.warning("[RESIDUAL] Synthesis LLM call failed: %s", exc)
            return {}

        if not isinstance(result, dict):
            return {}

        return result

    async def _write_report(self, llm_result: dict, ctx: AgentContext) -> str:
        """Write the Residual report to Obsidian."""
        if not ctx.dikiwi_obsidian_writer:
            return ""

        vault_path = getattr(ctx.dikiwi_obsidian_writer, "vault_path", None)
        if not vault_path:
            logger.warning("[RESIDUAL] Cannot write report: no vault_path available")
            return ""

        title = llm_result.get("report_title", "Residual Report")
        if not title or title.strip() == "":
            title = "Residual Report"

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        # Slugify: keep alphanumerics, replace everything else with underscore
        safe_title = "_".join("".join(c if c.isalnum() else "_" for c in w) for w in title.split())
        safe_title = safe_title.strip("_")
        while "__" in safe_title:
            safe_title = safe_title.replace("__", "_")
        if not safe_title:
            safe_title = "Residual_Report"
        filename = f"{date_str}_{safe_title}.md"

        report_dir = Path(vault_path) / "07-Proposal"
        report_dir.mkdir(parents=True, exist_ok=True)
        note_path = report_dir / filename

        summary = llm_result.get("summary", "")
        key_findings = llm_result.get("key_findings", [])
        proposals = llm_result.get("proposals", [])
        next_steps = llm_result.get("recommended_next_steps", [])

        lines: list[str] = [
            "---",
            'note_type: "report"',
            'dikiwi_level: "residual"',
            f"date_created: {datetime.now(timezone.utc).isoformat()}",
            f"proposals_count: {len(proposals)}",
            "---",
            "",
            f"# {title}",
            "",
            "## Summary",
            "",
            summary,
            "",
        ]

        if key_findings:
            lines.extend([
                "## Key Findings",
                "",
            ])
            for finding in key_findings:
                lines.append(f"- {finding}")
            lines.append("")

        if proposals:
            lines.extend([
                "## Proposals",
                "",
            ])
            for i, proposal in enumerate(proposals, 1):
                p_title = proposal.get("title", f"Proposal {i}")
                p_desc = proposal.get("description", "")
                p_domain = proposal.get("domain", "general")
                p_priority = proposal.get("priority", "medium")
                p_rationale = proposal.get("rationale", "")
                lines.extend([
                    f"### {i}. {p_title}",
                    "",
                    f"**Domain:** {p_domain} | **Priority:** {p_priority}",
                    "",
                    p_desc,
                    "",
                ])
                if p_rationale:
                    lines.extend([
                        "**Rationale:**",
                        "",
                        p_rationale,
                        "",
                    ])
            lines.append("")

        if next_steps:
            lines.extend([
                "## Recommended Next Steps",
                "",
            ])
            for step in next_steps:
                lines.append(f"- {step}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("*Drafted by Residual (残差) for Innovation and Entrepreneur review.*")

        try:
            note_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("[RESIDUAL] Wrote report to %s", note_path)
            return str(note_path.relative_to(vault_path))
        except Exception as exc:
            logger.warning("[RESIDUAL] Failed to write report: %s", exc)
            return ""

    async def _persist_proposals(
        self, proposals: list[dict], report_note_id: str, ctx: AgentContext
    ) -> None:
        """Insert proposals into GraphDB as residual_proposal nodes with initial status."""
        if not ctx.graph_db:
            return

        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue

            node_id = f"residual_{uuid.uuid4().hex[:8]}"
            title = proposal.get("title", "Untitled Proposal")
            description = (
                proposal.get("description")
                or proposal.get("problem")
                or proposal.get("summary", "")
            )
            label = f"{title}: {description[:200]}"

            try:
                await ctx.graph_db.insert_node(
                    node_id=node_id,
                    node_type="residual_proposal",
                    label=label,
                    source="residual",
                )
                await ctx.graph_db.set_node_property(
                    node_id, "status", "pending_innovation"
                )
                if report_note_id:
                    await ctx.graph_db.set_node_property(
                        node_id, "residual_report_path", report_note_id
                )
                await ctx.graph_db.set_node_property(
                    node_id, "validation_attempts", 0
                )
                for key, value in proposal.items():
                    if value in ("", None, [], {}):
                        continue
                    if key == "status":
                        key = "readiness_status"
                    if key in WORKFLOW_PROPOSAL_PROPERTY_KEYS:
                        key = f"proposal_{key}"
                    await ctx.graph_db.set_node_property(node_id, key, value)
            except Exception as exc:
                logger.warning("[RESIDUAL] Failed to insert proposal node: %s", exc)

    async def _read_rejection_feedback(self, ctx: AgentContext) -> str:
        """Read the Residual Feedback Index for self-correction."""
        if not ctx.graph_db:
            return ""
        try:
            entries = await ctx.graph_db.get_residual_feedback(limit=20)
            if not entries:
                return ""
            lines = ["# Residual Feedback Index", ""]
            for entry in entries:
                lines.append(
                    f"- [{entry['created_at']}] **{entry['proposal_label']}** — {entry['reason']}"
                )
            return "\n".join(lines)[:2000]
        except Exception as exc:
            logger.warning("[RESIDUAL] Failed to read feedback index: %s", exc)
            return ""

    def _format_reactor_proposals(self, ctx: AgentContext) -> str:
        """Format Reactor proposals from artifact store for the LLM prompt."""
        proposals = ctx.artifact_store.get("reactor_proposals", [])
        if not proposals:
            return ""
        lines: list[str] = []
        for i, p in enumerate(proposals, 1):
            lines.append(
                f"{i}. **{getattr(p, 'title', 'Untitled')}** (confidence: {getattr(p, 'confidence', 0):.0%})\n"
                f"   - Framework: {getattr(p, 'framework_used', 'unknown')}\n"
                f"   - Summary: {getattr(p, 'summary', getattr(p, 'content', ''))[:200]}"
            )
        return "\n\n".join(lines)

    def _find_stage_result(self, ctx: AgentContext, stage: DikiwiStage) -> StageResult | None:
        for result in ctx.stage_results:
            if result.stage == stage:
                return result
        return None
