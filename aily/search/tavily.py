"""Tavily Search API client for Aily.

Tavily provides AI-optimized search results - no browser automation needed.
Docs: https://docs.tavily.com/
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from aily.config import SETTINGS


@dataclass
class SearchResult:
    """Single search result from Tavily."""
    title: str
    url: str
    content: str
    score: float
    raw: dict[str, Any]


class TavilyClient:
    """Client for Tavily Search API.

    Tavily is an AI search engine that returns structured, LLM-ready results.
    No anti-bot, no CAPTCHAs, just clean search data.
    """

    BASE_URL = "https://api.tavily.com"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or SETTINGS.tavily_api_key
        if not self.api_key:
            raise ValueError("Tavily API key required. Set TAVILY_API_KEY env var.")

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
        )

    async def search(
        self,
        query: str,
        search_depth: str | None = None,
        max_results: int = 5,
        include_answer: bool = True,
        include_raw_content: bool = False,
    ) -> dict[str, Any]:
        """Execute a search query.

        Args:
            query: Search query string
            search_depth: "basic" (fast) or "advanced" (comprehensive)
            max_results: Number of results (1-20)
            include_answer: Include AI-generated answer summary
            include_raw_content: Include full page content (slower)

        Returns:
            Tavily API response with results and optional answer
        """
        depth = search_depth or SETTINGS.tavily_search_depth

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": depth,
            "max_results": max_results,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
        }

        resp = await self.client.post("/search", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search_to_notes(
        self,
        query: str,
        max_results: int = 5,
    ) -> str:
        """Search and format results as markdown for Obsidian.

        Returns a formatted markdown string with search results,
        ready to save as an Obsidian note.
        """
        results = await self.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            include_raw_content=False,
        )

        lines = [
            f"# Search: {query}",
            "",
            f"*Search depth: {results.get('search_depth', 'basic')}*",
            "",
        ]

        # AI-generated answer if available
        answer = results.get("answer")
        if answer:
            lines.extend([
                "## Summary",
                "",
                answer,
                "",
                "---",
                "",
            ])

        # Individual results
        lines.append("## Sources")
        lines.append("")

        for i, result in enumerate(results.get("results", []), 1):
            lines.extend([
                f"### {i}. {result.get('title', 'Untitled')}",
                "",
                f"**URL:** {result.get('url', 'N/A')}",
                "",
                f"**Relevance:** {result.get('score', 0):.2f}",
                "",
                result.get('content', 'No content available'),
                "",
            ])

        lines.extend([
            "---",
            "",
            f"*Results from Tavily Search API*",
        ])

        return "\n".join(lines)

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> TavilyClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


# Convenience function for quick searches
async def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Quick search using Tavily API.

    Example:
        >>> results = await tavily_search("What is the weight of LLM")
        >>> print(results["answer"])
    """
    async with TavilyClient() as client:
        return await client.search(query, max_results=max_results)
