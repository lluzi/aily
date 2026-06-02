"""Grounded vault chat for Aily-Copilot."""

from __future__ import annotations

import re
from typing import Any, Protocol

from aily.copilot.context import CopilotContextEnvelopeBuilder
from aily.copilot.vault import VaultSearchService
from aily.ui.telemetry import ui_telemetry_scope


class ChatLLM(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        response_format: dict[str, str] | None = None,
    ) -> str: ...


class CopilotVaultChatService:
    """Answer user questions from vault evidence with citation metadata."""

    def __init__(
        self,
        *,
        vault_search: VaultSearchService,
        context_builder: CopilotContextEnvelopeBuilder | None = None,
    ) -> None:
        self.vault_search = vault_search
        self.context_builder = context_builder or CopilotContextEnvelopeBuilder()

    async def answer(
        self,
        *,
        message: str,
        search_query: str = "",
        limit: int = 8,
        include_dirs: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
        chat_history: list[dict[str, Any]] | None = None,
        use_llm: bool = True,
        llm_client: ChatLLM | None = None,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        user_message = str(message or "").strip()
        query = str(search_query or user_message).strip()
        search = self.vault_search.search(
            query,
            limit=limit,
            include_dirs=include_dirs or [],
            exclude_dirs=exclude_dirs or [],
        )
        results = search.get("results", [])
        envelope = self.context_builder.build(
            user_message=user_message,
            search_results=results,
            chat_history=chat_history or [],
            system_prompt=_merged_system_prompt(system_prompt),
        )

        if not results:
            return {
                "answer": (
                    "I do not have enough vault evidence to answer that reliably. "
                    "Try adding a more specific note, folder, tag, or source term."
                ),
                "grounding_status": "insufficient_evidence",
                "used_llm": False,
                "search": search,
                "context_envelope": envelope,
                "citations": [],
                "suggested_actions": ["refine_search", "add_context", "run_vault_search"],
            }

        used_llm = False
        if use_llm and llm_client is not None:
            answer = await self._answer_with_llm(llm_client, envelope)
            used_llm = True
        else:
            answer = _extractive_answer(user_message, results)

        citations = envelope["citation_catalog"]
        return {
            "answer": _ensure_source_section(answer, citations),
            "grounding_status": "grounded",
            "used_llm": used_llm,
            "search": search,
            "context_envelope": envelope,
            "citations": citations,
            "suggested_actions": [
                "trace_claim",
                "generate_dossier",
                "show_graph_neighborhood",
                "find_weak_evidence",
            ],
        }

    async def _answer_with_llm(self, llm_client: ChatLLM, envelope: dict[str, Any]) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Aily-Copilot. Answer from the supplied vault evidence only. "
                    "Use concise expert prose. Cite claims with bracketed citation IDs like [V001]. "
                    "If evidence is insufficient, say so directly and list what is missing."
                ),
            },
            {
                "role": "user",
                "content": envelope["serialized_text"],
            },
        ]
        with ui_telemetry_scope(workload="copilot.chat"):
            return await llm_client.chat(messages, temperature=0.2)


_COPILOT_SYSTEM_PROMPT = (
    "You are Aily-Copilot, a vault-grounded reasoning assistant. "
    "Separate facts from inference. Use citation IDs for all substantive claims. "
    "Do not invent sources. If evidence is weak, say what is missing."
)


def _merged_system_prompt(project_prompt: str = "") -> str:
    clean_project_prompt = str(project_prompt or "").strip()
    if not clean_project_prompt:
        return _COPILOT_SYSTEM_PROMPT
    return f"{_COPILOT_SYSTEM_PROMPT}\n\nProject instructions:\n{clean_project_prompt}"


def _extractive_answer(user_message: str, results: list[dict[str, Any]]) -> str:
    top = results[:5]
    lines = [
        "Based on the vault evidence I found, here is the grounded reading:",
        "",
    ]
    for item in top:
        citation = item.get("citation_id", "")
        title = item.get("title", "Untitled")
        excerpt = _clean_excerpt(str(item.get("excerpt") or ""))
        if not excerpt:
            continue
        lines.append(f"- **{title}** [{citation}]: {excerpt}")
    if not any(line.startswith("- ") for line in lines):
        lines.append("The search found notes, but their excerpts were not substantive enough to support an answer.")
    lines.extend(
        [
            "",
            "Interpretation:",
            _interpretive_sentence(user_message, top),
        ]
    )
    return "\n".join(lines).strip()


def _interpretive_sentence(user_message: str, results: list[dict[str, Any]]) -> str:
    citations = ", ".join(f"[{item.get('citation_id')}]" for item in results[:3] if item.get("citation_id"))
    if not citations:
        return "The vault evidence is too thin for a reliable interpretation."
    topic = _topic_phrase(user_message)
    return (
        f"The safest conclusion is that `{topic}` should be treated as an evidence-backed topic only within "
        f"the scope of {citations}; broader claims need additional source review."
    )


def _topic_phrase(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    return " ".join(words[:8]) or "this question"


def _clean_excerpt(text: str, *, max_len: int = 420) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


def _ensure_source_section(answer: str, citations: list[dict[str, Any]]) -> str:
    if not citations:
        return answer
    if "## Sources" in answer or "#### Sources" in answer:
        return answer
    lines = [answer.rstrip(), "", "## Sources"]
    for item in citations[:10]:
        citation_id = item.get("citation_id", "")
        title = item.get("title", "")
        path = item.get("relative_path", "")
        lines.append(f"- [{citation_id}] [[{path}|{title}]]")
    return "\n".join(lines).strip()
