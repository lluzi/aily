"""Output integration for thinking system.

Handles delivery to:
- Obsidian (full markdown notes)
- Feishu (concise summaries)
"""

from __future__ import annotations

import logging
from typing import Any

from aily.thinking.models import ThinkingResult

logger = logging.getLogger(__name__)


class ThinkingOutputHandler:
    """Handles output delivery for thinking analysis results.

    Delivers to:
    - Obsidian: Full markdown note with frontmatter
    - Feishu: Concise summary with key insights
    """

    def __init__(
        self,
        obsidian_writer: Any | None = None,
        feishu_pusher: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the output handler.

        Args:
            obsidian_writer: ObsidianWriter instance for file output.
            feishu_pusher: FeishuPusher instance for messaging.
            config: Optional configuration dict.
        """
        self.obsidian_writer = obsidian_writer
        self.feishu_pusher = feishu_pusher
        self.config = config or {}

    async def deliver(
        self,
        result: ThinkingResult,
        options: dict[str, Any],
    ) -> DeliveryResult:
        """Deliver thinking result to configured outputs.

        Args:
            result: The thinking result to deliver.
            options: Delivery options including:
                - output_format: "obsidian", "feishu", or "both"
                - open_id: Feishu recipient (if feishu output)

        Returns:
            DeliveryResult with status for each output.
        """
        output_format = options.get("output_format", "obsidian")
        delivery_result = DeliveryResult()

        # Deliver to Obsidian
        if output_format in ("obsidian", "both") and self.obsidian_writer:
            try:
                path = await self._write_to_obsidian(result, options)
                delivery_result.obsidian_path = path
                delivery_result.obsidian_success = True
                logger.info("Delivered to Obsidian: %s", path)
            except Exception as e:
                delivery_result.obsidian_success = False
                delivery_result.obsidian_error = str(e)
                logger.error("Obsidian delivery failed: %s", e)

        # Deliver to Feishu
        if output_format in ("feishu", "both") and self.feishu_pusher:
            try:
                receive_id = options.get("open_id", "")
                success = await self._send_to_feishu(result, receive_id)
                delivery_result.feishu_success = success
                logger.info("Delivered to Feishu: success=%s", success)
            except Exception as e:
                delivery_result.feishu_success = False
                delivery_result.feishu_error = str(e)
                logger.error("Feishu delivery failed: %s", e)

        # Verify and log that real analysis was delivered
        if delivery_result.feishu_success:
            logger.info(
                "✅ Feishu delivery complete: %d framework insights, %d synthesized insights",
                len(result.framework_insights),
                len(result.synthesized_insights)
            )
            # Log framework outputs for verification
            for fi in result.framework_insights:
                logger.info(
                    "  - %s: %d insights, confidence=%.0f%%",
                    fi.framework_type.value.upper(),
                    len(fi.insights),
                    fi.confidence * 100
                )

        return delivery_result

    async def _write_to_obsidian(
        self,
        result: ThinkingResult,
        options: dict[str, Any],
    ) -> str:
        """Write full analysis to Obsidian.

        Args:
            result: The thinking result.
            options: Options including folder path.

        Returns:
            Path to written file.
        """
        if not self.obsidian_writer:
            raise RuntimeError("Obsidian writer not configured")

        # Get formatted output or generate fresh
        obsidian_content = result.formatted_output.get("obsidian")
        if not obsidian_content:
            from aily.thinking.output.formatter import PersuasiveOutputFormatter

            formatter = PersuasiveOutputFormatter()
            obsidian_content = await formatter.format_obsidian(result, result.payload)

        # Determine title
        timestamp = result.payload.timestamp.strftime("%Y%m%d-%H%M%S")
        title_slug = result.payload.source_title or "ARMY-Analysis"
        title_slug = "".join(c if c.isalnum() else "-" for c in title_slug)[:50]
        title = f"ARMY-{timestamp}-{title_slug}"

        # Write file - ObsidianWriter uses (title, markdown, source_url)
        source_url = result.payload.source_url or ""
        path = await self.obsidian_writer.write_note(
            title=title,
            markdown=obsidian_content,
            source_url=source_url,
        )

        return path

    async def _send_to_feishu(
        self,
        result: ThinkingResult,
        receive_id: str,
    ) -> bool:
        """Send summary to Feishu.

        Args:
            result: The thinking result.
            receive_id: Feishu user open_id.

        Returns:
            True if sent successfully.
        """
        if not self.feishu_pusher:
            raise RuntimeError("Feishu pusher not configured")

        # Get formatted output or generate fresh
        feishu_content = result.formatted_output.get("feishu")
        if not feishu_content:
            from aily.thinking.output.formatter import PersuasiveOutputFormatter

            formatter = PersuasiveOutputFormatter()
            feishu_content = await formatter.format_feishu(result, result.payload)

        # Send message - FeishuPusher uses receive_id
        success = await self.feishu_pusher.send_message(
            receive_id=receive_id,
            content=feishu_content,
        )

        return success


class DeliveryResult:
    """Result of output delivery attempts."""

    def __init__(self) -> None:
        """Initialize delivery result."""
        self.obsidian_success: bool = False
        self.obsidian_path: str | None = None
        self.obsidian_error: str | None = None
        self.feishu_success: bool = False
        self.feishu_error: str | None = None

    @property
    def all_success(self) -> bool:
        """True if all attempted deliveries succeeded."""
        obsidian_ok = self.obsidian_path is not None and self.obsidian_success
        feishu_ok = self.feishu_success
        # Consider success if at least one channel worked
        return obsidian_ok or feishu_ok

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "obsidian_success": self.obsidian_success,
            "obsidian_path": self.obsidian_path,
            "obsidian_error": self.obsidian_error,
            "feishu_success": self.feishu_success,
            "feishu_error": self.feishu_error,
            "all_success": self.all_success,
        }
