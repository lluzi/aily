"""Markdownize processor - converts any content to clean markdown.

This layer sits between content fetching and DIKIWI processing:
1. URLs → Browser-use fetch → Extract main content → Convert to markdown
2. HTML → Extract article content → Convert to markdown
3. PDF/Text/etc. → Extract text → Format as markdown
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aily.processing.processors import ExtractedContent

if TYPE_CHECKING:
    from aily.browser.manager import BrowserUseManager

logger = logging.getLogger(__name__)


@dataclass
class MarkdownContent:
    """Content converted to markdown format."""

    markdown: str
    title: str | None = None
    source_url: str | None = None
    source_type: str = "unknown"  # url, html, pdf, text, image
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class MarkdownizeProcessor:
    """Converts any content to clean markdown.

    For URLs: Uses browser-use to fetch fully rendered content, then
    extracts the main article content and converts to markdown.

    Usage:
        processor = MarkdownizeProcessor(browser_manager)
        result = await processor.process_url("https://example.com/article")
        # result.markdown contains clean markdown
    """

    def __init__(self, browser_manager: "BrowserUseManager | None" = None) -> None:
        self.browser_manager = browser_manager

    async def process_url(self, url: str, use_browser: bool = True) -> MarkdownContent:
        """Process a URL into markdown.

        Args:
            url: The URL to process
            use_browser: If True, use browser-use for JavaScript-rendered pages

        Returns:
            MarkdownContent with extracted markdown
        """
        logger.info("Markdownizing URL: %s", url)

        # Check if this needs browser rendering
        needs_browser = use_browser and self._needs_browser(url)

        if needs_browser and self.browser_manager:
            try:
                return await self._fetch_with_browser(url)
            except Exception as e:
                logger.warning("Browser fetch failed for %s: %s, falling back to static", url, e)
                return await self._fetch_static(url)
        else:
            return await self._fetch_static(url)

    async def process_content(self, content: ExtractedContent) -> MarkdownContent:
        """Convert already-extracted content to markdown.

        Args:
            content: ExtractedContent from another processor

        Returns:
            MarkdownContent with formatted markdown
        """
        # Check if content is low quality (generic branding, too short)
        if self._is_low_quality(content):
            logger.warning(
                "Low quality content detected from %s (length: %d)",
                content.source_type,
                len(content.text) if content.text else 0
            )
            # If we have a URL in metadata, try browser fetch
            url = content.metadata.get("url") if content.metadata else None
            if url and self.browser_manager:
                logger.info("Retrying with browser for: %s", url)
                return await self._fetch_with_browser(url)

        # Convert to markdown format
        markdown = self._format_as_markdown(content)

        return MarkdownContent(
            markdown=markdown,
            title=content.title,
            source_type=content.source_type,
            metadata=content.metadata or {},
        )

    async def _fetch_with_browser(self, url: str) -> MarkdownContent:
        """Fetch URL using browser-use and convert to markdown."""
        if not self.browser_manager:
            raise ValueError("Browser manager not available")

        logger.info("Fetching with browser: %s", url)

        # Check if this is a Monica share link
        is_monica_share = "monica.im/share" in url.lower()

        if is_monica_share:
            return await self._fetch_monica_share(url)

        # General browser fetch for other sites
        text = await self.browser_manager.fetch(url, timeout=60)

        if not text or len(text) < 100:
            raise ValueError(f"Browser returned insufficient content: {len(text) if text else 0} chars")

        # Clean up and format as markdown
        markdown = self._clean_to_markdown(text, url)

        return MarkdownContent(
            markdown=markdown,
            title=None,
            source_url=url,
            source_type="url",
            metadata={"fetched_with": "browser", "url": url},
        )

    async def _fetch_monica_share(self, url: str) -> MarkdownContent:
        """Specialized extraction for Monica share links.

        Monica share pages contain conversations between user and AI.
        We need to extract the full Q&A pairs with proper formatting.
        """
        logger.info("Fetching Monica share link with specialized extraction: %s", url)

        # Use browser to fetch with longer timeout for JS rendering
        text = await self.browser_manager.fetch(url, timeout=90)

        if not text:
            raise ValueError("Browser returned empty content")

        # Parse the conversation from the extracted text
        markdown = self._parse_monica_conversation(text, url)

        return MarkdownContent(
            markdown=markdown,
            title=None,
            source_url=url,
            source_type="url",
            metadata={
                "fetched_with": "browser",
                "url": url,
                "parser": "monica_share",
            },
        )

    def _parse_monica_conversation(self, text: str, url: str) -> str:
        """Parse Monica conversation text into structured markdown.

        Monica pages typically have format:
        - Title at top
        - Date
        - User messages (often starting with "You" or just content)
        - AI responses (marked with "Monica" or model name)
        """
        lines = text.split('\n')
        markdown_parts = []

        # Extract title (usually first few lines)
        title = None
        for line in lines[:5]:
            if line.strip() and not line.startswith('Monica') and len(line) > 10:
                title = line.strip()
                break

        if title:
            markdown_parts.append(f"# {title}")
            markdown_parts.append(f"\n*Source: {url}*\n")

        # Parse conversation turns
        conversation = []
        current_speaker = None
        current_message = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and metadata
            if not line or line in ['Share', 'Continue in Chat', 'Download']:
                i += 1
                continue

            # Detect speaker (Monica = AI, otherwise likely user)
            if line == 'Monica' or line.startswith('Monica '):
                # Save previous message if exists
                if current_speaker and current_message:
                    conversation.append((current_speaker, ' '.join(current_message)))
                current_speaker = 'AI'
                current_message = []
                i += 1
                continue

            # Date patterns indicate user turn (after date)
            if any(x in line for x in ['2024', '2025', '2026', 'AM', 'PM']) and len(line) < 50:
                if current_speaker and current_message:
                    conversation.append((current_speaker, ' '.join(current_message)))
                current_speaker = 'User'
                current_message = []
                i += 1
                continue

            # Accumulate message content
            if current_speaker and line:
                current_message.append(line)

            i += 1

        # Don't forget last message
        if current_speaker and current_message:
            conversation.append((current_speaker, ' '.join(current_message)))

        # Format as markdown Q&A
        for speaker, message in conversation:
            if speaker == 'User':
                markdown_parts.append(f"\n## Q: {message}\n")
            else:
                markdown_parts.append(f"**A:** {message}\n")

        result = '\n'.join(markdown_parts)

        # If parsing failed, return raw text
        if len(result) < 200:
            logger.warning("Monica parsing returned short result, using raw text")
            return f"# Monica Conversation\n\n{text}\n\n---\n*Source: {url}*"

        return result

    async def _fetch_static(self, url: str) -> MarkdownContent:
        """Fetch URL using static HTTP request."""
        import httpx

        logger.info("Fetching statically: %s", url)

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Try to decode content
            try:
                text = response.text
            except Exception:
                text = response.content.decode("utf-8", errors="ignore")

            content_type = response.headers.get("content-type", "")

            # Handle different content types
            if "text/html" in content_type:
                markdown = self._html_to_markdown(text, url)
            elif "text/plain" in content_type:
                markdown = f"```\n{text}\n```"
            else:
                markdown = self._clean_to_markdown(text, url)

            return MarkdownContent(
                markdown=markdown,
                title=None,
                source_url=url,
                source_type="url",
                metadata={
                    "fetched_with": "static",
                    "url": url,
                    "content_type": content_type,
                },
            )

    def _needs_browser(self, url: str) -> bool:
        """Check if URL likely needs browser rendering."""
        js_domains = [
            "monica.im",
            "chatgpt.com",
            "claude.ai",
            "bard.google.com",
            "gemini.google.com",
            "twitter.com",
            "x.com",
            "linkedin.com",
            "facebook.com",
            "instagram.com",
        ]
        url_lower = url.lower()
        return any(domain in url_lower for domain in js_domains)

    def _is_low_quality(self, content: ExtractedContent) -> bool:
        """Check if content extraction produced low-quality results."""
        if not content.text:
            return True

        # Too short
        if len(content.text) < 500:
            return True

        # Generic branding titles
        generic_titles = [
            "monica - your gpt ai assistant",
            "chatgpt",
            "claude",
            "loading...",
            "just a moment...",
            "checking your browser",
        ]

        if content.title:
            title_lower = content.title.lower()
            if any(g in title_lower for g in generic_titles):
                return True

        return False

    def _html_to_markdown(self, html: str, url: str | None = None) -> str:
        """Convert HTML to clean markdown."""
        import re

        # Remove script and style tags
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<noscript[^>]*>.*?</noscript>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # Extract title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else None

        # Try to extract main content (article, main, or body)
        main_content = self._extract_main_content(html)

        # Convert HTML tags to markdown
        markdown = self._convert_tags_to_markdown(main_content)

        # Add title as header if available
        if title and title not in markdown[:200]:
            markdown = f"# {title}\n\n{markdown}"

        # Add source URL if available
        if url:
            markdown += f"\n\n---\n*Source: {url}*"

        return markdown.strip()

    def _extract_main_content(self, html: str) -> str:
        """Extract main article/content from HTML."""
        import re

        # Try to find main content areas
        patterns = [
            r"<article[^>]*>(.*?)</article>",
            r"<main[^>]*>(.*?)</main>",
            r'<div[^>]*class=["\'][^"\']*(?:content|article|post)[^"\']*["\'][^>]*>(.*?)</div>',
            r"<body[^>]*>(.*?)</body>",
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1)

        return html

    def _convert_tags_to_markdown(self, html: str) -> str:
        """Convert common HTML tags to markdown."""
        import re

        # Headers
        html = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h4[^>]*>(.*?)</h4>", r"#### \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h5[^>]*>(.*?)</h5>", r"##### \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h6[^>]*>(.*?)</h6>", r"###### \1\n\n", html, flags=re.DOTALL | re.IGNORECASE)

        # Paragraphs and breaks
        html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)

        # Links
        html = re.sub(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            r"[\2](\1)",
            html,
            flags=re.DOTALL | re.IGNORECASE
        )

        # Images
        html = re.sub(
            r'<img[^>]*src=["\']([^"\']+)["\'][^>]*alt=["\']([^"\']*)["\'][^>]*>',
            r"![\2](\1)",
            html,
            flags=re.IGNORECASE
        )
        html = re.sub(
            r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>',
            r"![](\1)",
            html,
            flags=re.IGNORECASE
        )

        # Lists
        html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"</ul>|</ol>", "\n", html, flags=re.IGNORECASE)

        # Bold and italic
        html = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)

        # Code
        html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<pre[^>]*>(.*?)</pre>", r"```\n\1\n```", html, flags=re.DOTALL | re.IGNORECASE)

        # Blockquote
        html = re.sub(
            r"<blockquote[^>]*>(.*?)</blockquote>",
            lambda m: "> " + m.group(1).replace("\n", "\n> "),
            html,
            flags=re.DOTALL | re.IGNORECASE
        )

        # Remove remaining HTML tags
        html = re.sub(r"<[^>]+>", "", html)

        # Clean up whitespace
        html = re.sub(r"\n{3,}", "\n\n", html)
        html = re.sub(r"[ \t]+", " ", html)

        return html.strip()

    def _clean_to_markdown(self, text: str, url: str | None = None) -> str:
        """Clean raw text and format as markdown."""
        # Basic cleaning
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        markdown = "\n\n".join(lines)

        # Add source URL if available
        if url:
            markdown += f"\n\n---\n*Source: {url}*"

        return markdown

    def _format_as_markdown(self, content: ExtractedContent) -> str:
        """Format ExtractedContent as markdown."""
        parts = []

        if content.title:
            parts.append(f"# {content.title}\n")

        if content.text:
            parts.append(content.text)

        if content.metadata and content.metadata.get("url"):
            parts.append(f"\n\n---\n*Source: {content.metadata['url']}*")

        return "\n".join(parts)
