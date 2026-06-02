from __future__ import annotations

import json

from lark_oapi import Client
from lark_oapi.api.im.v1 import (
    CreateMessageRequestBodyBuilder,
    CreateMessageRequestBuilder,
    CreateMessageReactionRequestBodyBuilder,
    CreateMessageReactionRequestBuilder,
)


class FeishuPusher:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.client = (
            Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

    async def send_message(self, receive_id: str, content: str) -> bool:
        import asyncio
        body = (
            CreateMessageRequestBodyBuilder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": content}, ensure_ascii=False))
            .build()
        )
        req = (
            CreateMessageRequestBuilder()
            .receive_id_type("open_id")
            .request_body(body)
            .build()
        )
        # Run synchronous client in thread pool
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: self.client.im.v1.message.create(req))
        return resp.success()

    async def add_reaction(self, message_id: str, emoji_type: str = "DONE") -> bool:
        """Add emoji reaction to a message.

        Args:
            message_id: The message ID to react to.
            emoji_type: Emoji type (e.g., "DONE", "THUMBSUP", "EYES").

        Returns:
            True if successful.
        """
        import asyncio
        body = (
            CreateMessageReactionRequestBodyBuilder()
            .emoji_type(emoji_type)
            .build()
        )
        req = (
            CreateMessageReactionRequestBuilder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: self.client.im.v1.message_reaction.create(req))
        return resp.success()
