from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass
class ParseResult:
    title: str
    markdown: str
    source_type: str


class HTMLTextExtractor(HTMLParser):
    """Extract text content from HTML, preserving some structure."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.in_script_style = False
        self.current_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current_tag = tag
        if tag in ("script", "style", "nav", "header", "footer"):
            self.in_script_style = True
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li"):
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "header", "footer"):
            self.in_script_style = False
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "li"):
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")
        self.current_tag = None

    def handle_data(self, data: str) -> None:
        if not self.in_script_style:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self) -> str:
        text = "".join(self.text_parts)
        # Normalize whitespace
        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(line for line in lines if line)


def _extract_title(raw_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    m = re.search(r"<h1[^>]*>(.*?)</h1>", raw_text, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1)).strip())
    return "Untitled"


def parse_kimi(url: str, raw_text: str) -> ParseResult:
    return ParseResult(title=_extract_title(raw_text), markdown=raw_text, source_type="kimi")


def parse_monica(url: str, raw_text: str) -> ParseResult:
    """Parse Monica chat page into structured markdown.

    Monica chat pages typically have:
    - Chat messages in article or div containers
    - User messages vs assistant messages differentiated by class or position
    - Metadata like timestamps

    This parser attempts to extract the conversation flow into readable markdown.
    """
    from bs4 import BeautifulSoup

    title = _extract_title(raw_text)
    soup = BeautifulSoup(raw_text, "html.parser")

    # Try to find chat messages using common selectors
    # Monica uses various class names, so we try multiple patterns
    messages = []

    # Pattern 1: Look for role-based attributes (aria-label or data-role)
    for elem in soup.find_all(attrs={"data-role": ["user", "assistant", "bot"]}):
        role = elem.get("data-role", "unknown")
        text = elem.get_text(strip=True)
        if text and len(text) > 5:  # Filter out very short fragments
            messages.append(("User" if role == "user" else "Monica", text))

    # Pattern 2: Look for message containers with specific classes
    if not messages:
        # Common chat container class patterns
        msg_selectors = [
            ".chat-message",
            ".message-item",
            ".conversation-item",
            "[class*='message']",
            "[class*='chat']",
        ]
        for selector in msg_selectors:
            elems = soup.select(selector)
            if len(elems) >= 2:  # Found plausible messages
                for elem in elems:
                    text = elem.get_text(strip=True)
                    # Try to determine role from class or position
                    classes = " ".join(elem.get("class", [])).lower()
                    if any(kw in classes for kw in ["user", "human", "me"]):
                        messages.append(("User", text))
                    elif any(kw in classes for kw in ["bot", "ai", "assistant", "monica"]):
                        messages.append(("Monica", text))
                    else:
                        # Alternate based on position
                        role = "User" if len(messages) % 2 == 0 else "Monica"
                        messages.append((role, text))
                break

    # Pattern 3: Look for article elements (common in AI chat UIs)
    if not messages:
        articles = soup.find_all("article")
        if len(articles) >= 2:
            for i, article in enumerate(articles):
                text = article.get_text(strip=True)
                if text and len(text) > 10:
                    role = "User" if i % 2 == 0 else "Monica"
                    messages.append((role, text))

    # Build markdown from extracted messages
    if messages:
        lines = [f"# {title}", ""]
        for role, text in messages:
            lines.append(f"## {role}")
            lines.append("")
            lines.append(text)
            lines.append("")
        markdown = "\n".join(lines)
    else:
        # Fallback: extract readable text
        extractor = HTMLTextExtractor()
        try:
            extractor.feed(raw_text)
            text = extractor.get_text()
            markdown = f"# {title}\n\n{text}"
        except Exception:
            markdown = f"# {title}\n\n{raw_text}"

    return ParseResult(title=title, markdown=markdown, source_type="monica_chat")


def parse_arxiv(url: str, raw_text: str) -> ParseResult:
    return ParseResult(title=_extract_title(raw_text), markdown=raw_text, source_type="arxiv")


def parse_github(url: str, raw_text: str) -> ParseResult:
    return ParseResult(title=_extract_title(raw_text), markdown=raw_text, source_type="github")


def parse_youtube(url: str, raw_text: str) -> ParseResult:
    return ParseResult(title=_extract_title(raw_text), markdown=raw_text, source_type="youtube")


def parse_generic(url: str, raw_text: str) -> ParseResult:
    return ParseResult(title=_extract_title(raw_text), markdown=raw_text, source_type="generic")
