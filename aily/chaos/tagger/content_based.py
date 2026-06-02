"""Content-based tagging using file metadata and text analysis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aily.chaos.types import ExtractedContentMultimodal


class ContentBasedTagger:
    """Generate tags from content metadata and statistics."""

    # Domain keywords for auto-detection
    DOMAIN_KEYWORDS = {
        "eda": ["eda", "cadence", "synopsys", "mentor", "innovus", "genus", "dc", "icc"],
        "semiconductor": ["chip", "silicon", "wafer", "foundry", "tsmc", "process", "node", "nm"],
        "ai": ["ai", "ml", "model", "neural", "training", "inference", "llm", "gpt"],
        "mcp": ["mcp", "model context protocol", "tool use", "agent", "skill"],
        "architecture": ["architecture", "system design", "microservices", "pattern"],
        "verification": ["verification", "validation", "simulation", "formal", "uvm"],
        "quantization": ["quantization", "8bit", "4bit", "int8", "fp16", "compression"],
        "signoff": ["signoff", "sta", "timing", "power", "area", "ppa"],
    }

    # Format to type mapping
    FORMAT_TYPES = {
        "pdf": "document",
        "mp4": "video",
        "mov": "video",
        "avi": "video",
        "mkv": "video",
        "pptx": "presentation",
        "md": "markdown",
        "txt": "text",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
        "gif": "image",
        "webp": "image",
    }

    def tag(self, content: "ExtractedContentMultimodal") -> list[str]:
        """Generate tags from content metadata."""
        tags: set[str] = set()

        # Source type tag
        if content.source_type:
            tags.add(f"type:{content.source_type}")

        # File format tag
        if content.source_path:
            ext = content.source_path.suffix.lower().lstrip(".")
            if ext in self.FORMAT_TYPES:
                tags.add(f"format:{self.FORMAT_TYPES[ext]}")
            tags.add(f"ext:{ext}")

        # Language detection
        lang = self._detect_language(content.text)
        if lang:
            tags.add(f"lang:{lang}")

        # Content length category
        length_cat = self._categorize_length(content.text)
        tags.add(f"length:{length_cat}")

        # Domain detection
        domains = self._detect_domains(content.text)
        tags.update(domains)

        # Technical level
        tech_level = self._estimate_technical_level(content.text)
        tags.add(f"level:{tech_level}")

        # Content features
        if self._has_code(content.text):
            tags.add("has:code")
        if self._has_math(content.text):
            tags.add("has:math")
        if self._has_urls(content.text):
            tags.add("has:links")

        # Visual elements
        if content.visual_elements:
            tags.add("has:visuals")
            for elem in content.visual_elements:
                tags.add(f"visual:{elem.element_type}")

        # Transcript/video
        if content.transcript:
            tags.add("has:transcript")

        return list(tags)

    def _detect_language(self, text: str) -> str | None:
        """Detect primary language."""
        # Simple heuristic - can be improved with langdetect
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        total_chars = len(text)

        if total_chars == 0:
            return None

        chinese_ratio = chinese_chars / total_chars

        if chinese_ratio > 0.1:
            return "zh"
        else:
            return "en"

    def _categorize_length(self, text: str) -> str:
        """Categorize content length."""
        word_count = len(text.split())

        if word_count < 100:
            return "short"
        elif word_count < 500:
            return "medium"
        elif word_count < 2000:
            return "long"
        else:
            return "very-long"

    def _detect_domains(self, text: str) -> list[str]:
        """Detect technical domains."""
        text_lower = text.lower()
        detected: set[str] = set()

        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    detected.add(domain)
                    break

        return list(detected)

    def _estimate_technical_level(self, text: str) -> str:
        """Estimate technical complexity."""
        # Count technical indicators
        technical_patterns = [
            r"\b\w+_\w+\b",  # snake_case
            r"\b[A-Z]{2,}\b",  # ACRONYMS
            r"\b\d+\.?\d*\s*(nm|MHz|GHz|V|A|W)\b",  # units
            r"\b[a-z]+\([a-z,_]*\)",  # function calls
        ]

        technical_count = sum(
            len(re.findall(pattern, text))
            for pattern in technical_patterns
        )

        words = len(text.split())
        if words == 0:
            return "unknown"

        ratio = technical_count / words

        if ratio < 0.01:
            return "beginner"
        elif ratio < 0.03:
            return "intermediate"
        elif ratio < 0.05:
            return "advanced"
        else:
            return "expert"

    def _has_code(self, text: str) -> bool:
        """Check if text contains code."""
        code_indicators = [
            r"```",  # code blocks
            r"def\s+\w+\s*\(",  # Python function
            r"function\s+\w+\s*\(",  # JS function
            r"#include",
            r"import\s+\w+",
            r"class\s+\w+",
        ]
        return any(re.search(pattern, text) for pattern in code_indicators)

    def _has_math(self, text: str) -> bool:
        """Check if text contains math."""
        math_indicators = [
            r"\$[^$]+\$",  # LaTeX math
            r"\\\w+",  # LaTeX commands
            r"[\^\*\/\+\-]\s*\d",  # formulas
            r"=\s*[\d\w]+",  # equations
        ]
        return any(re.search(pattern, text) for pattern in math_indicators)

    def _has_urls(self, text: str) -> bool:
        """Check if text contains URLs."""
        url_pattern = r"https?://[^\s]+"
        return bool(re.search(url_pattern, text))
