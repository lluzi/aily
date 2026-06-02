from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from aily.config import SETTINGS
from aily.research.store import ResearchStore
from aily.search.tavily import TavilyClient


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_start() -> str:
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc).isoformat()


def _research_model_depth(model: str) -> str:
    normalized = model.strip().lower()
    if normalized == "pro":
        return "advanced"
    return "basic"


def _result_claim(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim": str(result.get("content") or "")[:600],
        "source_url": str(result.get("url") or ""),
        "source_title": str(result.get("title") or "Untitled"),
        "origin": "tavily_external_research",
        "authority": "external_unverified_until_reconciled",
    }


class TavilyResearchService:
    def __init__(
        self,
        *,
        store: ResearchStore,
        client: TavilyClient | None = None,
        daily_budget: int | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.daily_budget = SETTINGS.research_daily_budget if daily_budget is None else daily_budget

    async def create_and_run_packet(
        self,
        *,
        workflow_run_id: str,
        topic: str,
        trigger: str,
        query: str,
        topic_extraction_id: str = "",
        model: str = "mini",
        internal_context: list[dict[str, Any]] | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        checked_at = _utc_now()
        used_today = await self.store.count_research_jobs_since(_today_start())
        quota_allowed = used_today < max(0, int(self.daily_budget))
        job = await self.store.create_research_job(
            workflow_run_id=workflow_run_id,
            topic=topic,
            trigger=trigger,
            model=model,
            query=query,
            topic_extraction_id=topic_extraction_id,
            quota_allowed=quota_allowed,
            quota_checked_at=checked_at,
        )
        if not quota_allowed:
            failed = await self.store.fail_research_job(
                job["research_id"],
                error=f"Research daily budget exhausted: {used_today}/{self.daily_budget}",
            )
            return failed

        await self.store.mark_research_running(job["research_id"])
        try:
            client = self.client or TavilyClient()
            response = await client.search(
                query=query,
                search_depth=_research_model_depth(model),
                max_results=max(1, min(10, max_results)),
                include_answer=True,
                include_raw_content=False,
            )
            packet = build_research_packet(
                research_id=job["research_id"],
                topic=topic,
                trigger=trigger,
                model=model,
                query=query,
                response=response,
                internal_context=internal_context or [],
            )
            return await self.store.complete_research_job(job["research_id"], packet)
        except Exception as exc:
            return await self.store.fail_research_job(job["research_id"], error=str(exc))
        finally:
            if self.client is None and "client" in locals():
                await client.close()


def build_research_packet(
    *,
    research_id: str,
    topic: str,
    trigger: str,
    model: str,
    query: str,
    response: dict[str, Any],
    internal_context: list[dict[str, Any]],
) -> dict[str, Any]:
    results = list(response.get("results", []) or [])
    sources = [
        {
            "title": str(item.get("title") or "Untitled"),
            "url": str(item.get("url") or ""),
            "score": float(item.get("score") or 0.0),
            "origin": "tavily_external_research",
        }
        for item in results
    ]
    claims = [_result_claim(item) for item in results if str(item.get("content") or "").strip()]
    internal_source_ids = sorted(
        {
            str(item.get("source_id") or source_id).strip()
            for item in internal_context
            for source_id in (
                item.get("source_ids", [])
                if isinstance(item.get("source_ids"), list)
                else [item.get("source_id", "")]
            )
            if str(item.get("source_id") or source_id).strip()
        }
    )
    return {
        "research_id": research_id,
        "topic": topic,
        "trigger": trigger,
        "model": model,
        "query": query,
        "status": "completed",
        "provider": "tavily",
        "search_depth": response.get("search_depth") or _research_model_depth(model),
        "answer": response.get("answer") or "",
        "claims": claims,
        "evidence": [
            {
                "type": "internal_context",
                "source": "aily_knowledge_context",
                "source_ids": internal_source_ids,
                "context_count": len(internal_context),
            },
            {
                "type": "external_search",
                "source": "tavily",
                "result_count": len(results),
            },
        ],
        "sources": sources,
        "contradictions": [],
        "confidence": "external_search_unverified",
        "freshness": _utc_now(),
        "recommended_next_questions": [
            "Which Tavily claims are supported by Aily Knowledge?",
            "Which external claims require original-source verification?",
        ],
        "truth_policy": {
            "external_research_replaces_aily_evidence": False,
            "requires_reconciliation": True,
            "api_key_recorded": False,
        },
    }


def build_second_opinion_packet(
    *,
    second_opinion_id: str,
    source_id: str,
    attached_to: str,
    document_type: str,
    markdown: str,
    user_note: str = "",
) -> dict[str, Any]:
    text = " ".join(markdown.split())
    sentences = [part.strip() for part in text.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    claims = sentences[:5]
    risks = [sentence for sentence in sentences if "risk" in sentence.lower() or "challenge" in sentence.lower()][:5]
    assumptions = [
        sentence for sentence in sentences if "assume" in sentence.lower() or "assumption" in sentence.lower()
    ][:5]
    return {
        "second_opinion_id": second_opinion_id,
        "source_id": source_id,
        "attached_to": attached_to,
        "document_type": document_type,
        "stance": "unknown",
        "authority": "external_user_provided_non_authoritative",
        "user_note": user_note,
        "major_claims": claims,
        "assumptions": assumptions,
        "recommended_actions": [],
        "risks": risks,
        "agreement_with_aily": [],
        "disagreement_with_aily": [],
        "claims_needing_verification": claims[:3],
        "team_relevance": {
            "technical_innovation": [],
            "engineering_assessment": [],
            "commercial_feasibility": [],
        },
        "truth_policy": {
            "trusted_by_default": False,
            "can_satisfy_evidence_requirements_without_support": False,
            "requires_aily_or_research_support": True,
        },
    }
