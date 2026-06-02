"""Claim verification - like a human researcher clicking sources.

When AI generates a report, a human would:
1. Read the summary
2. Click the source links
3. Verify claims match the actual content
4. Flag discrepancies

This module automates that verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from aily.browser.fetcher import BrowserFetcher
from aily.llm.client import LLMClient


@dataclass
class VerificationResult:
    """Result of verifying a claim against a source."""
    claim: str
    source_url: str
    verified: bool
    confidence: float  # 0.0 - 1.0
    notes: str
    source_snippet: str | None = None  # What we found in the source


class ClaimExtractor:
    """Extract verifiable claims from markdown text."""

    # Patterns that indicate factual claims
    CLAIM_PATTERNS = [
        r'\*\*([^*]+)\*\*',  # Bold text often contains key facts
        r'`([^`]+)`',        # Inline code often has specific values
        r'\d+\.\d+%',        # Percentages
        r'\d+\s*(?:million|billion|trillion|M|B|T)',  # Large numbers
        r'(?:version|v)?\d+\.\d+(?:\.\d+)?',  # Version numbers
        r'\d{4}-\d{2}-\d{2}',  # Dates
    ]

    def extract(self, markdown: str) -> list[str]:
        """Extract claims that should be verified."""
        claims = []

        # Look for lines with bold or specific patterns
        for line in markdown.split('\n'):
            # Skip headers only (keep bullet points - they often contain claims)
            if line.startswith('#'):
                continue

            # Remove bullet markers for processing but keep the content
            clean_line = line.lstrip('*- ').strip()

            # Find bolded claims
            bold_matches = re.findall(r'\*\*([^*]+)\*\*', clean_line)
            for match in bold_matches:
                if len(match) > 10:  # Substantial claims only
                    claims.append(match.strip())

            # Find specific facts (numbers, versions, etc.)
            for pattern in self.CLAIM_PATTERNS[2:]:  # Skip bold/code
                matches = re.findall(pattern, clean_line)
                for match in matches:
                    # Include surrounding context
                    idx = clean_line.find(match)
                    start = max(0, idx - 30)
                    end = min(len(clean_line), idx + len(match) + 30)
                    context = clean_line[start:end].strip()
                    if context not in claims:
                        claims.append(context)

        # Deduplicate while preserving order
        seen = set()
        unique_claims = []
        for claim in claims:
            key = claim.lower()
            if key not in seen and len(claim) > 15:
                seen.add(key)
                unique_claims.append(claim)

        return unique_claims[:10]  # Limit to top 10 claims


class ClaimVerifier:
    """Verify claims by fetching sources and comparing content."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        fetcher: BrowserFetcher | None = None,
    ) -> None:
        self.llm = llm
        self.fetcher = fetcher or BrowserFetcher()
        self.extractor = ClaimExtractor()

    async def verify_claim(
        self,
        claim: str,
        source_url: str,
    ) -> VerificationResult:
        """Verify a single claim against a source URL.

        This is like a human clicking the link and reading the page
to see if it actually supports the claim.
        """
        try:
            # Fetch the source content
            content = await self._fetch_content(source_url)
            if not content:
                return VerificationResult(
                    claim=claim,
                    source_url=source_url,
                    verified=False,
                    confidence=0.0,
                    notes="Could not fetch source content",
                )

            # Use LLM to verify if claim is supported
            if self.llm:
                verification = await self._llm_verify(claim, content)
            else:
                # Simple keyword fallback
                verification = self._keyword_verify(claim, content)

            return VerificationResult(
                claim=claim,
                source_url=source_url,
                verified=verification["verified"],
                confidence=verification["confidence"],
                notes=verification["notes"],
                source_snippet=verification.get("snippet"),
            )

        except Exception as e:
            return VerificationResult(
                claim=claim,
                source_url=source_url,
                verified=False,
                confidence=0.0,
                notes=f"Verification error: {e}",
            )

    async def verify_digest(
        self,
        markdown: str,
        source_urls: list[str],
    ) -> list[VerificationResult]:
        """Verify all claims in a digest against available sources.

        Like a human reviewer going through a report and checking
each claim against the cited sources.
        """
        claims = self.extractor.extract(markdown)
        if not claims or not source_urls:
            return []

        results = []
        for claim in claims[:5]:  # Verify top 5 claims
            # Try each source until we find verification
            for url in source_urls[:3]:  # Check up to 3 sources per claim
                result = await self.verify_claim(claim, url)
                if result.verified and result.confidence > 0.7:
                    results.append(result)
                    break
            else:
                # No source verified this claim
                results.append(VerificationResult(
                    claim=claim,
                    source_url="",
                    verified=False,
                    confidence=0.0,
                    notes="No supporting source found",
                ))

        return results

    async def _fetch_content(self, url: str) -> str:
        """Fetch content from a URL."""
        try:
            # Try simple HTTP first
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    # Extract text content (simple HTML stripping)
                    text = self._strip_html(resp.text)
                    return text[:5000]  # Limit content length
        except Exception:
            pass

        # Fallback to browser fetch for JavaScript-heavy pages
        return await self.fetcher.fetch_text(url)

    def _strip_html(self, html: str) -> str:
        """Simple HTML tag removal."""
        # Remove script and style elements
        text = re.sub(r'<(script|style)[^>]*>[^<]*</\1>', ' ', html, flags=re.I)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    async def _llm_verify(self, claim: str, content: str) -> dict[str, Any]:
        """Use LLM to verify if content supports claim."""
        system_prompt = """You are a fact-checker. Given a claim and source content, determine if the content supports the claim.

Respond with JSON:
{
    "verified": true/false,
    "confidence": 0.0-1.0,
    "notes": "explanation of findings",
    "snippet": "relevant quote from source (if found)"
}"""

        user_prompt = f"""CLAIM: {claim}

SOURCE CONTENT:
{content[:3000]}

Does the source content support this claim?"""

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = await self.llm.chat(messages, temperature=0.0)
            # Parse JSON response
            import json
            result = json.loads(response)
            return {
                "verified": result.get("verified", False),
                "confidence": result.get("confidence", 0.0),
                "notes": result.get("notes", "No explanation"),
                "snippet": result.get("snippet"),
            }
        except Exception as e:
            return {
                "verified": False,
                "confidence": 0.0,
                "notes": f"LLM verification failed: {e}",
                "snippet": None,
            }

    def _keyword_verify(self, claim: str, content: str) -> dict[str, Any]:
        """Simple keyword-based verification fallback."""
        claim_words = set(claim.lower().split())
        content_lower = content.lower()

        # Check for key terms
        matches = sum(1 for word in claim_words if word in content_lower)
        ratio = matches / len(claim_words) if claim_words else 0

        # Look for exact phrase
        exact_match = claim.lower() in content_lower

        verified = exact_match or ratio > 0.6

        return {
            "verified": verified,
            "confidence": 0.5 + (ratio * 0.5),
            "notes": f"Keyword match: {ratio:.0%}" if not exact_match else "Exact phrase found",
            "snippet": None,
        }


async def verify_digest_claims(
    markdown: str,
    source_urls: list[str],
    llm: LLMClient | None = None,
) -> list[VerificationResult]:
    """Convenience function to verify digest claims."""
    verifier = ClaimVerifier(llm=llm)
    return await verifier.verify_digest(markdown, source_urls)
