"""Intelligent message intent detection for Feishu messages.

Analyzes user messages to determine:
- Simple chat response
- URL save only
- Deep thinking analysis (ARMY OF TOP MINDS)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class IntentType(Enum):
    """Types of user intent."""

    CHAT = auto()  # Just chat, no action needed
    URL_SAVE = auto()  # Save URL to Obsidian
    THINKING_ANALYSIS = auto()  # Deep ARMY OF TOP MINDS analysis
    VOICE = auto()  # Voice message
    FILE = auto()  # File attachment
    IMAGE = auto()  # Image for OCR
    MIND_CONTROL = auto()  # Enable/disable Three-Mind System components


@dataclass
class MessageIntent:
    """Detected intent from a message."""

    intent_type: IntentType
    url: Optional[str] = None
    text: str = ""
    confidence: float = 1.0
    reasoning: str = ""
    # Mind control metadata
    mind_name: Optional[str] = None  # "innovation", "entrepreneur", "dikiwi"
    mind_action: Optional[str] = None  # "enable", "disable", "status"


class IntentRouter:
    """Routes messages to appropriate handlers based on intent."""

    # Keywords that trigger deep thinking analysis
    THINKING_KEYWORDS = {
        # Analysis requests
        "分析", "analyze", "analysis",
        "思考", "think", "thinking",
        "拆解", "break down", "breakdown",
        "研究", "research",
        "评估", "evaluate", "assessment",
        "review", "审计", "审查",

        # Framework mentions
        "triz", "mckinsey", "麦肯锡", "gstack",
        "army of top minds", "顶级思维",
        "框架", "framework",
        "方法论", "methodology",

        # Deep dive indicators
        "深挖", "deep dive",
        "仔细看看", "take a close look",
        "怎么看", "what do you think",
        "有什么想法", "your thoughts",
        "帮我看看", "help me understand",
        "解释一下", "explain",
        "解读", "interpret",

        # Problem solving
        "问题", "problem",
        "矛盾", "contradiction",
        "痛点", "pain point",
        "解决方案", "solution",
        "怎么解决", "how to solve",
    }

    # Simple save indicators - no analysis needed
    SAVE_ONLY_KEYWORDS = {
        "保存", "save",
        "收藏", "bookmark",
        "记下来", "note down",
        "存一下", "store",
        "丢进去", "drop it",
        "扔进去", "throw it in",
        "稍后看", "read later",
    }

    # Explicit skip indicators
    SKIP_ANALYSIS = {
        "不用分析", "no analysis",
        "不用思考", "no thinking",
        "直接保存", "just save",
        "只保存", "save only",
    }

    # Mind control commands (Three-Mind System)
    MIND_CONTROL_KEYWORDS = {
        # Enable/disable verbs
        "enable", "disable", "start", "stop", "status",
        "开启", "关闭", "启动", "停止", "状态",
        # Mind names
        "innovation", "entrepreneur", "dikiwi",
        "创新", "创业", "知识",
        # Combined phrases
        "disable innovation", "disable entrepreneur", "disable dikiwi",
        "enable innovation", "enable entrepreneur", "enable dikiwi",
        "关闭创新", "关闭创业", "关闭知识",
        "开启创新", "开启创业", "开启知识",
        "停止创新", "停止创业",
        "启动创新", "启动创业",
    }

    @classmethod
    def analyze(cls, text: str) -> MessageIntent:
        """Analyze message text and determine intent.

        Args:
            text: Message text content

        Returns:
            MessageIntent with detected type and metadata
        """
        text_lower = text.lower().strip()

        # Check for mind control commands first (highest priority)
        mind_control = cls._parse_mind_control(text_lower)
        if mind_control:
            return mind_control

        # Extract URL if present
        url_match = re.search(r"https?://\S+", text)
        url = url_match.group(0) if url_match else None

        # Remove URL for text analysis
        text_without_url = re.sub(r"https?://\S+", "", text).strip()

        # Check for explicit skip indicators first
        if any(kw in text_lower for kw in cls.SKIP_ANALYSIS):
            return MessageIntent(
                intent_type=IntentType.URL_SAVE if url else IntentType.CHAT,
                url=url,
                text=text,
                confidence=0.95,
                reasoning="Explicit skip analysis indicator found",
            )

        # Check for save-only indicators (no URL means just chat)
        if any(kw in text_lower for kw in cls.SAVE_ONLY_KEYWORDS):
            return MessageIntent(
                intent_type=IntentType.URL_SAVE if url else IntentType.CHAT,
                url=url,
                text=text,
                confidence=0.9,
                reasoning="Save-only keyword detected",
            )

        # Check for thinking/analysis keywords (only in user-provided text, not URL titles)
        has_thinking_keyword = any(kw in text_lower for kw in cls.THINKING_KEYWORDS)

        # Special case: URLs with share link patterns should be saved, not analyzed
        # These often have descriptive titles with analysis keywords that trigger false positives
        share_link_indicators = ["/share/", "shareId=", "monica.im/share"]
        is_share_link = any(indicator in url for indicator in share_link_indicators) if url else False

        # If there's a URL and thinking keywords → deep analysis
        # BUT: Skip analysis for share links unless user explicitly asks for it
        if url and has_thinking_keyword and not is_share_link:
            return MessageIntent(
                intent_type=IntentType.THINKING_ANALYSIS,
                url=url,
                text=text,
                confidence=0.85,
                reasoning=f"URL + thinking keyword detected in: {text_without_url[:50]}",
            )

        # Share links with thinking keywords in title → still save (not analyze)
        if url and has_thinking_keyword and is_share_link:
            return MessageIntent(
                intent_type=IntentType.URL_SAVE,
                url=url,
                text=text,
                confidence=0.85,
                reasoning="Share link with analysis keywords in title - saving (not analyzing)",
            )

        # If strong thinking language but no URL → chat with analysis
        if has_thinking_keyword and len(text_without_url) > 6:
            return MessageIntent(
                intent_type=IntentType.THINKING_ANALYSIS,
                url=None,
                text=text,
                confidence=0.7,
                reasoning="Thinking keywords without URL - will analyze text directly",
            )

        # URL without thinking keywords → simple save
        if url:
            # Check if it's just a URL with minimal context
            if len(text_without_url.strip()) < 20:
                return MessageIntent(
                    intent_type=IntentType.URL_SAVE,
                    url=url,
                    text=text,
                    confidence=0.8,
                    reasoning="URL with minimal context - save only",
                )

            # URL with some context but no explicit thinking request
            # Default to save (conservative - user can explicitly ask for analysis)
            return MessageIntent(
                intent_type=IntentType.URL_SAVE,
                url=url,
                text=text,
                confidence=0.6,
                reasoning="URL found but no explicit analysis request - saving",
            )

        # No URL, no thinking keywords → just chat
        return MessageIntent(
            intent_type=IntentType.CHAT,
            url=None,
            text=text,
            confidence=0.9,
            reasoning="No URL or analysis indicators - simple chat",
        )

    @classmethod
    def _parse_mind_control(cls, text_lower: str) -> Optional[MessageIntent]:
        """Parse mind control commands.

        Args:
            text_lower: Lowercase message text

        Returns:
            MessageIntent if mind control command detected, None otherwise
        """
        # Check for mind control patterns
        # Pattern: [enable|disable|start|stop|status] [mind_name] mind
        # Examples: "disable innovation mind", "enable entrepreneur", "dikiwi status"

        # Extract action and mind name
        action = None
        mind_name = None

        # Action detection
        if any(word in text_lower for word in ["disable", "关闭", "停止", "stop"]):
            action = "disable"
        elif any(word in text_lower for word in ["enable", "开启", "启动", "start"]):
            action = "enable"
        elif any(word in text_lower for word in ["status", "状态"]):
            action = "status"

        if not action:
            return None

        # Mind name detection
        if any(word in text_lower for word in ["innovation", "创新"]):
            mind_name = "innovation"
        elif any(word in text_lower for word in ["entrepreneur", "创业"]):
            mind_name = "entrepreneur"
        elif any(word in text_lower for word in ["dikiwi", "知识", "dikimi"]):
            mind_name = "dikiwi"
        elif "all" in text_lower or "所有" in text_lower:
            mind_name = "all"

        if not mind_name:
            # Action detected but no mind name - assume user wants help with mind control
            return MessageIntent(
                intent_type=IntentType.MIND_CONTROL,
                text=text_lower,
                confidence=0.6,
                reasoning="Mind control action detected but mind name unclear - need clarification",
                mind_name="unknown",
                mind_action=action,
            )

        return MessageIntent(
            intent_type=IntentType.MIND_CONTROL,
            text=text_lower,
            confidence=0.95,
            reasoning=f"Mind control command: {action} {mind_name}",
            mind_name=mind_name,
            mind_action=action,
        )

    @classmethod
    def should_analyze(cls, text: str) -> bool:
        """Quick check if message should trigger thinking analysis.

        Args:
            text: Message text

        Returns:
            True if should trigger thinking analysis
        """
        intent = cls.analyze(text)
        return intent.intent_type == IntentType.THINKING_ANALYSIS
