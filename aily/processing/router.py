"""Universal content processing router.

Routes content to the appropriate processor based on detected type.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse, urlunparse

from aily.config import SETTINGS
from aily.processing.detector import ContentType, ContentTypeDetector
from aily.processing.processors import (
    ContentProcessor,
    CSVProcessor,
    DocxProcessor,
    ExtractedContent,
    ImageProcessor,
    MarkdownProcessor,
    PDFProcessor,
    TextProcessor,
    WebProcessor,
    XLSXProcessor,
)

if TYPE_CHECKING:
    from aily.browser.manager import BrowserUseManager

logger = logging.getLogger(__name__)


class UnsafeURLError(ValueError):
    """Raised when URL intake targets a disallowed network location."""


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _private_url_intake_allowed() -> bool:
    return bool(getattr(SETTINGS, "url_intake_allow_private_network", False))


async def _public_addresses_for_url(url: str) -> list[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only http(s) URLs are supported")
    if not parsed.hostname:
        raise UnsafeURLError("URL hostname is required")
    hostname = parsed.hostname.strip().lower().rstrip(".")
    blocked_hosts = {"metadata.google.internal"}
    if hostname in blocked_hosts:
        raise UnsafeURLError("URL host is not allowed")
    if hostname == "169.254.169.254":
        raise UnsafeURLError("Cloud metadata URLs are not allowed")
    if not _private_url_intake_allowed() and (hostname == "localhost" or hostname.endswith(".localhost")):
        raise UnsafeURLError("URL host is not allowed")
    try:
        if not _is_public_ip(hostname) and not _private_url_intake_allowed():
            raise UnsafeURLError("URL host resolves to a non-public address")
        return [hostname]
    except ValueError:
        pass

    def resolve() -> list[str]:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return [info[4][0] for info in infos]

    try:
        addresses = await asyncio.to_thread(resolve)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"URL hostname could not be resolved: {hostname}") from exc
    if not addresses or (not _private_url_intake_allowed() and any(not _is_public_ip(address) for address in addresses)):
        raise UnsafeURLError("URL host resolves to a non-public address")
    return addresses


async def _validate_public_http_url(url: str) -> None:
    await _public_addresses_for_url(url)


def _host_header(parsed) -> str:
    if parsed.port is None:
        return parsed.hostname or ""
    return f"{parsed.hostname}:{parsed.port}"


def _url_with_host(url: str, host: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    netloc_host = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        netloc_host = f"{netloc_host}:{parsed.port}"
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc_host = f"{userinfo}@{netloc_host}"
    return urlunparse(parsed._replace(netloc=netloc_host))


def _response_peer_address(response: object) -> str | None:
    extensions = getattr(response, "extensions", {}) or {}
    stream = extensions.get("network_stream") if isinstance(extensions, dict) else None
    if stream is None or not hasattr(stream, "get_extra_info"):
        return None
    for info_name in ("peername", "server_addr"):
        peer = stream.get_extra_info(info_name)
        if isinstance(peer, tuple) and peer:
            return str(peer[0])
    socket_obj = stream.get_extra_info("socket")
    if socket_obj is not None:
        try:
            peer = socket_obj.getpeername()
        except OSError:
            peer = None
        if isinstance(peer, tuple) and peer:
            return str(peer[0])
    return None


def _format_size(size_bytes: int) -> str:
    """Format byte size to human readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


class ProcessingRouter:
    """Routes content to the appropriate processor.

    Usage:
        router = ProcessingRouter()
        result = await router.process(file_bytes, filename="paper.pdf")
        # result.text contains extracted text
    """

    def __init__(self, browser_manager: "BrowserUseManager | None" = None) -> None:
        self.browser_manager = browser_manager
        self._processors: list[ContentProcessor] = []
        self._init_processors()

    def _init_processors(self) -> None:
        """Initialize all available processors."""
        self._processors = [
            PDFProcessor(),
            ImageProcessor(languages=["en", "ch_sim", "ch_tra"]),  # English + Chinese
            MarkdownProcessor(),
            DocxProcessor(),
            CSVProcessor(),
            XLSXProcessor(),
            WebProcessor(browser_manager=self.browser_manager),
            TextProcessor(),  # Fallback for text/*
        ]

    async def process(
        self,
        data: bytes,
        filename: str | None = None,
        http_content_type: str | None = None,
        url: str | None = None,
    ) -> ExtractedContent:
        """Process any content and extract text.

        Args:
            data: Raw file bytes
            filename: Original filename (helps with detection)
            http_content_type: HTTP Content-Type header (if fetched from URL)
            url: Source URL (for JS-required domain detection)

        Returns:
            ExtractedContent with text and metadata
        """
        # 1. Check file size limits first
        size_limit = SETTINGS.max_file_size
        if http_content_type and http_content_type.startswith("image/"):
            size_limit = SETTINGS.max_image_size

        if len(data) > size_limit:
            logger.warning(
                "File too large: %s > %s limit",
                _format_size(len(data)),
                _format_size(size_limit),
            )
            return ExtractedContent(
                text=f"[File too large: {_format_size(len(data))} exceeds limit of {_format_size(size_limit)}]",
                source_type="error",
            )

        # 2. Detect content type
        content_type = ContentTypeDetector.detect(
            data=data,
            filename=filename,
            http_content_type=http_content_type,
        )

        logger.info(
            "Processing content: type=%s, confidence=%.2f, filename=%s",
            content_type.mime_type,
            content_type.confidence,
            filename,
        )

        # 3. Find matching processor
        processor = self._get_processor(content_type.mime_type)

        if processor is None:
            logger.warning(
                "No processor for type %s, trying text fallback",
                content_type.mime_type,
            )
            # Try text processor as last resort
            processor = TextProcessor()

        # 3. Process and return
        try:
            # Pass URL to WebProcessor for JS-required domain detection
            if isinstance(processor, WebProcessor):
                result = await processor.process(data, filename, url=url)
            else:
                result = await processor.process(data, filename)
            logger.info(
                "Extracted %d chars from %s",
                len(result.text),
                content_type.mime_type,
            )
            return result
        except Exception as e:
            logger.exception("Processing failed for %s", content_type.mime_type)
            return ExtractedContent(
                text=f"[Processing failed: {e}]",
                source_type="error",
            )

    def _get_processor(self, mime_type: str) -> ContentProcessor | None:
        """Find the best processor for a MIME type."""
        # Exact match first
        for processor in self._processors:
            if mime_type in processor.SUPPORTED_TYPES:
                return processor

        # Wildcard match (e.g., image/*)
        for processor in self._processors:
            if processor.can_process(mime_type):
                return processor

        return None

    async def process_url(
        self,
        url: str,
        browser_manager: "BrowserUseManager | None" = None,
    ) -> ExtractedContent:
        """Process content from a URL.

        Args:
            url: URL to fetch and process
            browser_manager: Optional browser for JS-rendered pages

        Returns:
            ExtractedContent with text and metadata
        """
        import httpx

        logger.info("Fetching URL: %s", url)

        try:
            current_url = url
            max_bytes = max(1, int(SETTINGS.max_file_size))
            async with httpx.AsyncClient(follow_redirects=False, timeout=30, trust_env=False) as client:
                for _redirect in range(6):
                    addresses = await _public_addresses_for_url(current_url)
                    parsed = urlparse(current_url)
                    request_url = _url_with_host(current_url, addresses[0])
                    headers = {"Host": _host_header(parsed)}
                    extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" and parsed.hostname else None
                    async with client.stream("GET", request_url, headers=headers, extensions=extensions) as response:
                        peer_address = _response_peer_address(response)
                        if not peer_address:
                            raise UnsafeURLError("URL connection peer could not be verified")
                        if not _is_public_ip(peer_address) and not _private_url_intake_allowed():
                            raise UnsafeURLError("URL connection reached a non-public address")
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise RuntimeError("Redirect response missing Location header")
                            current_url = urljoin(current_url, location)
                            continue

                        response.raise_for_status()
                        content_type = response.headers.get("content-type")
                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > max_bytes:
                            raise RuntimeError(f"URL response exceeds max fetch size of {max_bytes} bytes")

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise RuntimeError(f"URL response exceeds max fetch size of {max_bytes} bytes")
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        return await self.process(
                            data=data,
                            filename=urlparse(current_url).path.rsplit("/", 1)[-1] or "index.html",
                            http_content_type=content_type,
                            url=current_url,
                        )
                raise RuntimeError("Too many redirects while fetching URL")

        except Exception as e:
            logger.exception("Failed to fetch URL: %s", url)
            return ExtractedContent(
                text=f"[Failed to fetch {url}: {e}]",
                source_type="web",
            )
