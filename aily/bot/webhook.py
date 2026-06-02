from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, Request, HTTPException

from aily.config import SETTINGS
from aily.queue.db import QueueDB
from aily.network.tailscale import TailscaleClient

logger = logging.getLogger(__name__)
router = APIRouter()
tailscale_client = TailscaleClient()

dedup_cache: dict[str, float] = {}
DEDUP_TTL = 60.0


def _extract_url(text: str) -> Optional[str]:
    m = re.search(r"https?://\S+", text)
    return m.group(0) if m else None


def _clean_dedup() -> None:
    now = time.time()
    expired = [k for k, v in dedup_cache.items() if now - v > DEDUP_TTL]
    for k in expired:
        dedup_cache.pop(k, None)


def _verify_signature(body: bytes, timestamp: str, signature: str, encrypt_key: str) -> bool:
    if not signature or not timestamp:
        return False
    sign = signature[len("sha256="):]
    expected = hmac.new(
        encrypt_key.encode(),
        f"{timestamp}\n{body.decode()}\n".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sign, expected)


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request) -> dict:
    body = await request.body()
    timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
    signature = request.headers.get("X-Lark-Signature", "")

    if not _verify_signature(body, timestamp, signature, SETTINGS.feishu_encrypt_key):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = json.loads(body)

    if "challenge" in data:
        return {"challenge": data["challenge"]}

    event_id = data.get("header", {}).get("event_id", "")
    _clean_dedup()
    now = time.time()
    if event_id in dedup_cache:
        return {"status": "ok"}
    dedup_cache[event_id] = now

    event = data.get("event", {})
    message = event.get("message", {})
    msg_type = message.get("message_type", "")

    db = QueueDB(SETTINGS.queue_db_path)
    await db.initialize()
    try:
        open_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")

        # Handle voice messages
        if msg_type == "voice":
            content = json.loads(message.get("content", "{}"))
            file_key = content.get("file_key")
            file_name = content.get("file_name", "voice.mp3")
            if file_key and SETTINGS.feishu_voice_enabled:
                await db.enqueue(
                    "voice_message",
                    {
                        "file_key": file_key,
                        "file_name": file_name,
                        "open_id": open_id,
                        "message_id": message.get("message_id", ""),
                    },
                )
                logger.info("Enqueued voice message: %s", file_key)
            return {"status": "ok"}

        # Only handle text messages from here
        if msg_type != "text":
            return {"status": "ok"}

        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "")
        url = _extract_url(text)

        if url is None:
            logger.info("Ignoring non-URL Feishu text while V1 orchestrator is being rebuilt")
            return {"status": "ok"}

        enqueued = await db.enqueue_url(url, open_id=open_id, source="manual")
        if not enqueued:
            logger.info("Deduplicated URL from Feishu: %s", url)
            return {"status": "ok"}
        logger.info("Enqueued URL from Feishu: %s", url)
        return {"status": "ok"}
    finally:
        await db.close()


@router.get("/status")
async def status() -> dict:
    """Get Aily status including Tailscale connectivity."""
    ts_status = await tailscale_client.get_status()
    return {
        "aily_version": "0.7.0",
        "tailscale": {
            "is_running": ts_status.is_running,
            "is_logged_in": ts_status.is_logged_in,
            "tailnet_name": ts_status.tailnet_name,
            "ip_addresses": ts_status.ip_addresses,
            "magic_dns_name": ts_status.magic_dns_name,
            "aily_url": tailscale_client.get_aily_url(ts_status),
        },
    }
