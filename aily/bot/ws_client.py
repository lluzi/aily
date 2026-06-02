"""
Feishu WebSocket long connection client.

This uses Feishu's SDK to establish a WebSocket connection to Feishu servers,
eliminating the need for public webhook URLs.
"""

from __future__ import annotations

import asyncio
import logging
import json
from typing import Callable, TYPE_CHECKING

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from aily.config import SETTINGS
from aily.queue.db import QueueDB
from aily.bot.message_intent import IntentRouter, IntentType

if TYPE_CHECKING:
    from aily.sessions.dikiwi_mind import DikiwiMind

logger = logging.getLogger(__name__)


class FeishuWSClient:
    """
    Feishu WebSocket client for receiving events via long connection.

    This avoids the need for public webhook URLs by establishing a WebSocket
    connection from local machine to Feishu servers.
    """

    def __init__(self, db: QueueDB, pusher=None, input_channel=None, dikiwi_mind: "DikiwiMind" = None) -> None:
        self.db = db
        self.pusher = pusher
        self.input_channel = input_channel  # Gating system input channel (legacy)
        self.dikiwi_mind = dikiwi_mind  # Three-Mind System - DIKIWI Mind
        self.client: lark.ws.Client | None = None
        self._running = False

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        """Handle incoming message event."""
        logger.info("[FeishuWS] Received message: %s", data.header.event_id)

        # Extract message info
        message = data.event.message
        sender = data.event.sender

        msg_type = message.message_type
        open_id = sender.sender_id.open_id if sender.sender_id else ""
        message_id = message.message_id

        logger.info("[FeishuWS] Message type: %s from: %s", msg_type, open_id)

        # Add immediate acknowledgment emoji
        if self.pusher and message_id:
            emoji = "👀" if msg_type == "text" else "✅"
            asyncio.create_task(self._add_reaction(message_id, emoji))

        # Handle text messages with intelligent routing
        if msg_type == "text":
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                logger.error("[FeishuWS] Failed to parse text message JSON")
                return
            text = content.get("text", "")

            # Use intent router to determine what user wants
            intent = IntentRouter.analyze(text)
            logger.info(
                "[FeishuWS] Intent detected: %s (confidence: %.2f, reason: %s)",
                intent.intent_type.name,
                intent.confidence,
                intent.reasoning,
            )

            if intent.intent_type == IntentType.THINKING_ANALYSIS:
                # Deep analysis requested - route to Three-Mind DIKIWI system
                if self.dikiwi_mind:
                    asyncio.create_task(self._route_to_dikiwi(msg_type, text, message_id, open_id))
                elif self.input_channel:
                    # Fallback to legacy gating system
                    asyncio.create_task(self._route_to_gating(msg_type, text, message_id, open_id))
                else:
                    asyncio.create_task(self._enqueue_thinking(text, open_id, message_id))
            elif intent.intent_type == IntentType.URL_SAVE:
                # Simple URL save - route to DIKIWI Mind for knowledge extraction
                if self.dikiwi_mind:
                    asyncio.create_task(self._route_to_dikiwi(msg_type, text, message_id, open_id))
                elif intent.url:
                    asyncio.create_task(self._enqueue_url(intent.url, open_id))
                else:
                    logger.warning("[FeishuWS] URL_SAVE intent but no URL found")
            elif intent.intent_type == IntentType.MIND_CONTROL:
                # Mind control command - enable/disable minds
                asyncio.create_task(self._handle_mind_control(intent, open_id))
            elif intent.intent_type == IntentType.CHAT:
                # Just chat - could integrate with agent system
                logger.info("[FeishuWS] Chat message: %s", text[:50])
                # Optionally respond via simple echo or integrate with agent
                # asyncio.create_task(self._handle_chat(text, open_id))

        # Handle voice messages - route to DIKIWI Mind
        elif msg_type == "voice":
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                logger.error("[FeishuWS] Failed to parse voice message JSON")
                return
            file_key = content.get("file_key")
            file_name = content.get("file_name", "voice.mp3")

            if file_key and SETTINGS.feishu_voice_enabled:
                logger.info("[FeishuWS] Voice message: %s", file_key)
                text = f"Voice message: {file_name} (file_key: {file_key})"
                if self.dikiwi_mind:
                    asyncio.create_task(self._route_to_dikiwi(msg_type, text, message_id, open_id))
                else:
                    asyncio.create_task(self._enqueue_voice(file_key, file_name, open_id))

        # Handle file attachments (PDFs, documents, etc) - route to DIKIWI Mind
        elif msg_type == "file":
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                logger.error("[FeishuWS] Failed to parse file message JSON")
                return
            file_key = content.get("file_key")
            file_name = content.get("file_name", "document")

            if file_key:
                logger.info("[FeishuWS] File attachment: %s (%s)", file_name, file_key)
                text = f"File attachment: {file_name} (file_key: {file_key})"
                if self.dikiwi_mind:
                    asyncio.create_task(self._route_to_dikiwi(msg_type, text, message_id, open_id))
                else:
                    asyncio.create_task(self._enqueue_file(file_key, file_name, open_id))

        # Handle images (for OCR) - route to DIKIWI Mind
        elif msg_type == "image":
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                logger.error("[FeishuWS] Failed to parse image message JSON")
                return
            image_key = content.get("image_key")

            if image_key:
                logger.info("[FeishuWS] Image message: %s", image_key)
                text = f"Image: {image_key}"
                if self.dikiwi_mind:
                    asyncio.create_task(self._route_to_dikiwi(msg_type, text, message_id, open_id))
                else:
                    asyncio.create_task(self._enqueue_image(image_key, open_id))

    async def _enqueue_url(self, url: str, open_id: str) -> None:
        """Enqueue URL fetch job."""
        try:
            enqueued = await self.db.enqueue_url(url, open_id=open_id, source="manual")
            if enqueued:
                logger.info("[FeishuWS] URL enqueued: %s", url)
            else:
                logger.info("[FeishuWS] URL deduplicated: %s", url)
        except Exception as e:
            logger.exception("[FeishuWS] Failed to enqueue URL: %s", e)

    async def _enqueue_thinking(self, text: str, open_id: str, message_id: str = "") -> None:
        """Enqueue ARMY OF TOP MINDS thinking analysis job."""
        try:
            # Determine analysis type based on content
            import re
            url_match = re.search(r"https?://\S+", text)

            if url_match:
                # URL-based thinking analysis - fetch content then analyze
                url = url_match.group(0)
                await self.db.enqueue(
                    "thinking_analysis",
                    {
                        "content": text,  # User's request + URL
                        "source_url": url,
                        "source_title": None,
                        "metadata": {
                            "open_id": open_id,
                            "message_id": message_id,
                            "request_type": "url_deep_dive",
                        },
                    },
                )
                logger.info("[FeishuWS] Thinking analysis enqueued for URL: %s", url)
            else:
                # Text-only thinking analysis
                await self.db.enqueue(
                    "thinking_analysis",
                    {
                        "content": text,
                        "source_url": None,
                        "source_title": None,
                        "metadata": {
                            "open_id": open_id,
                            "message_id": message_id,
                            "request_type": "text_analysis",
                        },
                    },
                )
                logger.info("[FeishuWS] Thinking analysis enqueued for text")
        except Exception as e:
            logger.exception("[FeishuWS] Failed to enqueue thinking analysis: %s", e)

    async def _route_to_gating(self, msg_type: str, text: str, message_id: str, open_id: str) -> None:
        """Route message through gating system (rain -> drainage -> reservoir -> dam)."""
        if not self.input_channel:
            logger.warning("[FeishuWS] No input channel configured, falling back to direct enqueue")
            await self._enqueue_thinking(text, open_id, message_id)
            return

        try:
            # Send through gating system input channel
            drop = await self.input_channel.receive_message(
                message_type=msg_type,
                content=text,
                message_id=message_id,
                open_id=open_id,
            )
            logger.info(
                "[FeishuWS] Message routed to gating system: drop=%s",
                drop.id[:12] if hasattr(drop, 'id') else 'unknown'
            )
        except Exception as e:
            logger.exception("[FeishuWS] Gating routing failed, falling back: %s", e)
            await self._enqueue_thinking(text, open_id, message_id)

    async def _route_to_dikiwi(self, msg_type: str, text: str, message_id: str, open_id: str) -> None:
        """Route message to Three-Mind DIKIWI system.

        This is the primary message handler for the new Three-Mind architecture.
        All inputs flow through DikiwiMind.process_input() for DIKIWI pipeline processing.
        """
        if not self.dikiwi_mind:
            logger.warning("[FeishuWS] DIKIWI Mind not available, falling back to gating")
            await self._route_to_gating(msg_type, text, message_id, open_id)
            return

        try:
            # Import RainDrop here to avoid circular imports
            from aily.gating.drainage import RainDrop, RainType

            # Map message type to RainType
            rain_type_map = {
                "text": RainType.URL if "http" in text.lower() else RainType.CHAT,
                "voice": RainType.VOICE,
                "file": RainType.DOCUMENT,
                "image": RainType.IMAGE,
            }
            rain_type = rain_type_map.get(msg_type, RainType.CHAT)

            # Create RainDrop for DIKIWI processing
            drop = RainDrop(
                id=message_id or str(uuid.uuid4()),
                rain_type=rain_type,
                content=text,
                source="feishu",
                source_id=message_id,
                creator_id=open_id,
                metadata={"message_type": msg_type},
            )

            logger.info("[FeishuWS] Routing to DIKIWI Mind: drop=%s", drop.id[:12])

            # Process through DIKIWI pipeline
            result = await self.dikiwi_mind.process_input(drop)

            if result.final_stage_reached:
                logger.info(
                    "[FeishuWS] DIKIWI processing complete: %s → %s",
                    drop.id[:12],
                    result.final_stage_reached.name,
                )
            else:
                logger.warning("[FeishuWS] DIKIWI processing failed for drop %s", drop.id[:12])

        except Exception as e:
            logger.exception("[FeishuWS] DIKIWI routing failed: %s", e)
            # Fallback to legacy gating system
            await self._route_to_gating(msg_type, text, message_id, open_id)

    async def _add_reaction(self, message_id: str, emoji: str) -> None:
        """Add emoji reaction to message."""
        if not self.pusher or not message_id:
            return
        try:
            # Map emoji to Feishu reaction type
            emoji_map = {
                "👀": "EYES",
                "✅": "DONE",
                "👍": "THUMBSUP",
                "🤔": "THINKING_FACE",
                "💡": "BULB",
            }
            reaction_type = emoji_map.get(emoji, "DONE")
            success = await self.pusher.add_reaction(message_id, reaction_type)
            if success:
                logger.info("[FeishuWS] Added reaction %s to message %s", emoji, message_id[:20])
        except Exception as e:
            logger.debug("[FeishuWS] Failed to add reaction: %s", e)

    async def _enqueue_voice(self, file_key: str, file_name: str, open_id: str) -> None:
        """Enqueue voice message job."""
        try:
            await self.db.enqueue(
                "voice_message",
                {
                    "file_key": file_key,
                    "file_name": file_name,
                    "open_id": open_id,
                    "message_id": "",
                },
            )
            logger.info("[FeishuWS] Voice enqueued: %s", file_key)
        except Exception as e:
            logger.exception("[FeishuWS] Failed to enqueue voice: %s", e)

    async def _enqueue_file(self, file_key: str, file_name: str, open_id: str) -> None:
        """Enqueue file attachment job (PDF, document, etc)."""
        try:
            await self.db.enqueue(
                "file_attachment",
                {
                    "file_key": file_key,
                    "file_name": file_name,
                    "open_id": open_id,
                    "source": "feishu_file",
                },
            )
            logger.info("[FeishuWS] File enqueued: %s (%s)", file_name, file_key)
        except Exception as e:
            logger.exception("[FeishuWS] Failed to enqueue file: %s", e)

    async def _enqueue_image(self, image_key: str, open_id: str) -> None:
        """Enqueue image for OCR processing."""
        try:
            await self.db.enqueue(
                "image_ocr",
                {
                    "image_key": image_key,
                    "open_id": open_id,
                    "source": "feishu_image",
                },
            )
            logger.info("[FeishuWS] Image enqueued for OCR: %s", image_key)
        except Exception as e:
            logger.exception("[FeishuWS] Failed to enqueue image: %s", e)

    async def _handle_mind_control(self, intent, open_id: str) -> None:
        """Handle mind control commands (enable/disable Three-Mind System)."""
        if not self.pusher or not open_id:
            return

        mind_name = intent.mind_name
        action = intent.mind_action

        # Import here to avoid circular imports
        from aily.config import SETTINGS

        # Get current status for all minds
        minds_status = {
            "dikiwi": SETTINGS.minds.dikiwi_enabled,
            "innovation": SETTINGS.minds.innovation_enabled,
            "entrepreneur": SETTINGS.minds.entrepreneur_enabled,
        }

        # Handle unknown mind name - provide help
        if mind_name == "unknown":
            help_msg = """💡 **Mind Control Commands**

Available commands:
• "disable innovation mind" - Stop Innovation Mind (8am TRIZ)
• "enable innovation mind" - Start Innovation Mind
• "disable entrepreneur mind" - Stop Entrepreneur Mind (9am GStack)
• "enable entrepreneur mind" - Start Entrepreneur Mind
• "disable dikiwi mind" - Stop DIKIWI Mind (continuous)
• "enable dikiwi mind" - Start DIKIWI Mind
• "disable all minds" - Stop all minds
• "enable all minds" - Start all minds
• "mind status" - Show current status of all minds"""
            await self.pusher.send_message(open_id, help_msg)
            return

        # Handle status command
        if action == "status" or mind_name == "all" and action == "status":
            status_msg = "🧠 **Mind Status**\n\n"
            for name, enabled in minds_status.items():
                emoji = "✅" if enabled else "❌"
                status_msg += f"{emoji} {name.title()} Mind: {'enabled' if enabled else 'disabled'}\n"
            await self.pusher.send_message(open_id, status_msg)
            return

        # Handle enable/disable for specific mind
        if mind_name in ("dikiwi", "innovation", "entrepreneur"):
            if action == "enable":
                setattr(SETTINGS.minds, f"{mind_name}_enabled", True)
                await self.pusher.send_message(open_id, f"✅ {mind_name.title()} Mind enabled. Will take effect on next cycle.")
                logger.info("[FeishuWS] %s mind enabled by user %s", mind_name, open_id[:20])
            elif action == "disable":
                setattr(SETTINGS.minds, f"{mind_name}_enabled", False)
                await self.pusher.send_message(open_id, f"❌ {mind_name.title()} Mind disabled.")
                logger.info("[FeishuWS] %s mind disabled by user %s", mind_name, open_id[:20])

        # Handle enable/disable for all minds
        elif mind_name == "all":
            if action == "enable":
                SETTINGS.minds.dikiwi_enabled = True
                SETTINGS.minds.innovation_enabled = True
                SETTINGS.minds.entrepreneur_enabled = True
                await self.pusher.send_message(open_id, "✅ All minds enabled. Three-Mind System active.")
                logger.info("[FeishuWS] All minds enabled by user %s", open_id[:20])
            elif action == "disable":
                SETTINGS.minds.dikiwi_enabled = False
                SETTINGS.minds.innovation_enabled = False
                SETTINGS.minds.entrepreneur_enabled = False
                await self.pusher.send_message(open_id, "❌ All minds disabled. Three-Mind System paused.")
                logger.info("[FeishuWS] All minds disabled by user %s", open_id[:20])

    def start(self) -> None:
        """Start the WebSocket client."""
        if self._running:
            logger.warning("[FeishuWS] Already running")
            return

        self._running = True

        # Build event handler
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message)
            .build()
        )

        # Create WebSocket client
        self.client = lark.ws.Client(
            SETTINGS.feishu_app_id,
            SETTINGS.feishu_app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # Start in background thread
        import threading
        self._thread = threading.Thread(target=self._run_client, daemon=True)
        self._thread.start()

        logger.info("[FeishuWS] Client started (APP_ID: %s...)", SETTINGS.feishu_app_id[:20])

    def _run_client(self) -> None:
        """Run the client (blocking)."""
        try:
            self.client.start()
        except Exception as e:
            logger.exception("[FeishuWS] Client error: %s", e)
            self._running = False

    def stop(self) -> None:
        """Stop the WebSocket client."""
        self._running = False
        if self.client:
            # Note: lark.ws.Client doesn't have a clean stop method
            # It will be terminated when the process ends
            pass
        logger.info("[FeishuWS] Client stopped")


# Singleton instance
_ws_client: FeishuWSClient | None = None


def get_ws_client(db: QueueDB | None = None, pusher=None, input_channel=None, dikiwi_mind=None) -> FeishuWSClient:
    """Get or create WebSocket client singleton."""
    global _ws_client
    if _ws_client is None:
        if db is None:
            from aily.queue.db import QueueDB
            from aily.config import SETTINGS
            db = QueueDB(SETTINGS.queue_db_path)
        _ws_client = FeishuWSClient(db, pusher, input_channel, dikiwi_mind)
    return _ws_client
