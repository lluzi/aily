"""Channels - input and output connectors for the gating system.

Input channels bring rain into the drainage system.
Output channels flow breakthrough content to destinations.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from aily.gating.drainage import DrainageSystem, RainDrop, RainType

logger = logging.getLogger(__name__)


class InputChannel:
    """Base class for input channels.

    All inputs (Feishu, clipboard, manual) go through here
    before entering the drainage system.
    """

    def __init__(self, drainage: DrainageSystem) -> None:
        """Initialize input channel.

        Args:
            drainage: The drainage system to flow into
        """
        self.drainage = drainage

    async def receive(
        self,
        content: str,
        source: str,
        source_id: str = "",
        creator_id: str = "",
        raw_bytes: Optional[bytes] = None,
        metadata: Optional[dict] = None,
    ) -> RainDrop:
        """Receive content and flow to drainage.

        Args:
            content: Text content
            source: Origin identifier
            source_id: Original ID from source
            creator_id: Who created the content
            raw_bytes: Binary data if applicable
            metadata: Additional context

        Returns:
            RainDrop that was created
        """
        # Determine rain type from content
        rain_type = self._classify_content(content, raw_bytes)

        drop = await self.drainage.collect(
            rain_type=rain_type,
            content=content,
            source=source,
            source_id=source_id,
            creator_id=creator_id,
            raw_bytes=raw_bytes,
            metadata=metadata or {},
        )

        logger.info(
            "[InputChannel:%s] Received %s → drop %s",
            source,
            rain_type.name,
            drop.id[:12],
        )

        return drop

    def _classify_content(
        self,
        content: str,
        raw_bytes: Optional[bytes] = None,
    ) -> RainType:
        """Classify content into rain type."""
        import re

        # URL detection
        if re.search(r"https?://\S+", content):
            return RainType.URL

        # File type detection from bytes
        if raw_bytes:
            if raw_bytes[:4] == b"%PDF":
                return RainType.DOCUMENT
            if raw_bytes[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
                return RainType.IMAGE
            if any(raw_bytes[:4].startswith(magic) for magic in [b"RIFF", b"ID3"]):
                return RainType.VOICE

        # Default to chat
        return RainType.CHAT


class FeishuInputChannel(InputChannel):
    """Input channel for Feishu messages."""

    async def receive_message(
        self,
        message_type: str,
        content: str,
        message_id: str,
        open_id: str,
        file_key: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> RainDrop:
        """Receive a Feishu message.

        Args:
            message_type: text, image, file, voice
            content: Message content or JSON
            message_id: Feishu message ID
            open_id: Sender's open_id
            file_key: For file/image/voice messages
            file_name: Original filename

        Returns:
            RainDrop
        """
        import json

        # Parse content based on message type
        rain_type = RainType.CHAT
        text_content = content

        if message_type == "text":
            try:
                parsed = json.loads(content)
                text_content = parsed.get("text", content)
            except json.JSONDecodeError:
                text_content = content
            rain_type = RainType.CHAT

        elif message_type == "image":
            rain_type = RainType.IMAGE
            text_content = f"[Image: {file_key}]"

        elif message_type == "file":
            rain_type = RainType.DOCUMENT
            text_content = f"[File: {file_name}]"

        elif message_type == "voice":
            rain_type = RainType.VOICE
            text_content = f"[Voice: {file_key}]"

        return await self.receive(
            content=text_content,
            source="feishu",
            source_id=message_id,
            creator_id=open_id,
            metadata={
                "message_type": message_type,
                "file_key": file_key,
                "file_name": file_name,
            },
        )


class ClipboardInputChannel(InputChannel):
    """Input channel for clipboard/passive capture."""

    async def receive_clipboard(
        self,
        content: str,
        source_url: Optional[str] = None,
    ) -> RainDrop:
        """Receive clipboard content.

        Args:
            content: Clipboard text
            source_url: Optional source URL

        Returns:
            RainDrop
        """
        metadata = {}
        if source_url:
            metadata["source_url"] = source_url

        return await self.receive(
            content=content,
            source="clipboard",
            metadata=metadata,
        )


class OutputChannel:
    """Output channel for breakthrough content.

    Delivers high-impact insights to destinations.
    """

    def __init__(
        self,
        name: str,
        handler: Any,
    ) -> None:
        """Initialize output channel.

        Args:
            name: Channel name (feishu, obsidian, etc)
            handler: Handler function/instance
        """
        self.name = name
        self.handler = handler
        self._success_count = 0
        self._failure_count = 0

    async def deliver(
        self,
        content: Any,
        metadata: dict[str, Any],
    ) -> bool:
        """Deliver content through this channel.

        Args:
            content: Content to deliver
            metadata: Delivery metadata

        Returns:
            True if successful
        """
        try:
            success = await self._send(content, metadata)
            if success:
                self._success_count += 1
                logger.info("[OutputChannel:%s] Delivered successfully", self.name)
            else:
                self._failure_count += 1
                logger.warning("[OutputChannel:%s] Delivery returned False", self.name)
            return success

        except Exception as e:
            self._failure_count += 1
            logger.error("[OutputChannel:%s] Delivery failed: %s", self.name, e)
            return False

    async def _send(self, content: Any, metadata: dict[str, Any]) -> bool:
        """Override in subclasses."""
        raise NotImplementedError

    def get_stats(self) -> dict[str, Any]:
        """Get channel statistics."""
        total = self._success_count + self._failure_count
        return {
            "name": self.name,
            "success": self._success_count,
            "failure": self._failure_count,
            "success_rate": self._success_count / max(total, 1),
        }


class FeishuOutputChannel(OutputChannel):
    """Output channel for Feishu messages."""

    async def _send(self, content: Any, metadata: dict[str, Any]) -> bool:
        """Send to Feishu."""
        open_id = metadata.get("open_id")
        if not open_id:
            logger.warning("[FeishuOutput] No open_id in metadata")
            return False

        # The handler should be a FeishuPusher
        if hasattr(self.handler, "send_message"):
            return await self.handler.send_message(
                receive_id=open_id,
                content=str(content),
            )
        return False


class ObsidianOutputChannel(OutputChannel):
    """Output channel for Obsidian notes."""

    async def _send(self, content: Any, metadata: dict[str, Any]) -> bool:
        """Write to Obsidian."""
        title = metadata.get("title", "Aily Note")
        source_url = metadata.get("source_url", "")

        if hasattr(self.handler, "write_note"):
            path = await self.handler.write_note(
                title=title,
                markdown=str(content),
                source_url=source_url,
            )
            return bool(path)
        return False
