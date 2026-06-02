from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aily.graph.db import GraphDB
from aily.llm.client import LLMClient
from aily.push.feishu import FeishuPusher
from aily.queue.db import QueueDB
from aily.verify.verifier import ClaimVerifier
from aily.writer.obsidian import ObsidianWriter

logger = logging.getLogger(__name__)


class DigestPipeline:
    def __init__(
        self,
        graph_db: GraphDB,
        queue_db: QueueDB,
        llm: LLMClient,
        writer: ObsidianWriter,
        pusher: FeishuPusher | None = None,
        verify_claims: bool = True,
    ) -> None:
        self.graph_db = graph_db
        self.queue_db = queue_db
        self.llm = llm
        self.writer = writer
        self.pusher = pusher
        self.verify_claims = verify_claims
        self.verifier = ClaimVerifier(llm=llm) if verify_claims else None

    async def run(self, open_id: str = "") -> str:
        logger.info("DigestPipeline started")

        top_nodes = await self.graph_db.get_top_nodes_by_edge_count(hours=24, limit=10)
        collisions = await self.graph_db.get_collisions_within_hours(24, min_occurrences=2)
        nodes = await self.graph_db.get_nodes_within_hours(24)
        edges = await self.graph_db.get_edges_within_hours(24)
        nodes_count = len(nodes)
        edges_count = len(edges)
        logger.info(
            "Fetched graph data: %s nodes, %s edges, %s top nodes, %s collisions",
            nodes_count,
            edges_count,
            len(top_nodes),
            len(collisions),
        )

        collisions_with_urls: list[dict[str, Any]] = []
        for collision in collisions:
            node_id = collision["node_id"]
            source_logs = await self.graph_db.get_source_logs_for_node(node_id, hours=24)
            raw_log_ids = [log["raw_log_id"] for log in source_logs]
            url_map = await self.queue_db.get_urls_for_raw_logs(raw_log_ids)
            source_urls = list(url_map.values())
            collision_data = dict(collision)
            collision_data["source_urls"] = source_urls
            collisions_with_urls.append(collision_data)
        logger.info("Resolved source URLs for %s collisions", len(collisions_with_urls))

        payload = {
            "overview": {
                "nodes_count": nodes_count,
                "edges_count": edges_count,
            },
            "top_nodes": top_nodes,
            "collisions": collisions_with_urls,
        }

        system_prompt = (
            "You are Aily, a personal knowledge assistant. Curate a concise daily digest "
            "markdown note from the past 24h of activity. Use sections: ## Overview, "
            "## Top Entities, ## Collisions / Connections, ## Raw Activity. Be concise "
            "but insightful. Link to source URLs where available."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        logger.info("Calling LLM to generate digest markdown")
        markdown = await self.llm.chat(messages, temperature=0.7)
        logger.info("LLM returned markdown (%s chars)", len(markdown))

        # VERIFY CLAIMS - like a human clicking source links
        verification_section = ""
        all_source_urls = list(dict.fromkeys(
            url for data in collisions_with_urls for url in data.get("source_urls", [])
        ))
        verified_results: list = []
        if self.verifier and all_source_urls:
            logger.info("Verifying claims against %d sources", len(all_source_urls))
            verified_results = await self.verifier.verify_digest(markdown, all_source_urls)

            verified_count = sum(1 for r in verified_results if r.verified)
            flagged_count = len(verified_results) - verified_count

            if verified_results:
                verification_section = self._format_verification(verified_results)
                markdown += verification_section
                logger.info(
                    "Verification complete: %d verified, %d flagged",
                    verified_count, flagged_count,
                )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = f"Aily Daily Digest {today}"

        note_path = await self.writer.write_note(title, markdown, source_url="")
        logger.info("Obsidian note written: %s", note_path)

        if open_id and self.pusher is not None:
            # Include verification status in notification
            verified_count = sum(
                1 for r in verified_results if r.verified
            ) if self.verifier and all_source_urls else 0
            flagged_count = len(verified_results) - verified_count if self.verifier else 0

            summary_parts = [
                f"Daily digest: {nodes_count} entities, {len(collisions)} collisions",
            ]
            if verified_count > 0:
                summary_parts.append(f"✅ {verified_count} claims verified")
            if flagged_count > 0:
                summary_parts.append(f"⚠️ {flagged_count} need review")

            summary = "\n".join(summary_parts)

            try:
                success = await self.pusher.send_message(open_id, summary)
                logger.info("Feishu push sent: %s", success)
            except Exception:
                logger.exception("Feishu push failed, continuing pipeline")

        logger.info("DigestPipeline finished")
        return note_path

    def _format_verification(self, results: list) -> str:
        """Format verification results as markdown section."""
        if not results:
            return ""

        lines = [
            "",
            "## Verification",
            "",
            "Like a human researcher clicking source links to check claims:",
            "",
        ]

        verified = [r for r in results if r.verified]
        flagged = [r for r in results if not r.verified]

        if verified:
            lines.append(f"✅ **Verified ({len(verified)})**")
            lines.append("")
            for r in verified:
                lines.append(f"- ✓ {r.claim[:60]}...")
                lines.append(f"  → [{r.source_url[:40]}...]({r.source_url})")
                if r.source_snippet:
                    snippet = r.source_snippet[:100].replace('\n', ' ')
                    lines.append(f"  > \"{snippet}...\"")
                lines.append("")

        if flagged:
            lines.append(f"⚠️ **Needs Review ({len(flagged)})**")
            lines.append("")
            for r in flagged:
                lines.append(f"- ⚠ {r.claim[:60]}...")
                lines.append(f"  → {r.notes}")
                if r.source_url:
                    lines.append(f"  → Checked: [{r.source_url[:40]}...]({r.source_url})")
                lines.append("")

        return "\n".join(lines)
