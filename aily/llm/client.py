from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional
from uuid import uuid4

import httpx

from aily.runtime.backpressure import provider_backpressure
from aily.ui.events import emit_ui_event
from aily.ui.telemetry import get_ui_telemetry_context

logger = logging.getLogger(__name__)

PROVIDER_TRACE_HEADER_NAMES = (
    "x-request-id",
    "x-moonshot-request-id",
    "x-ms-request-id",
    "request-id",
    "cf-ray",
    "retry-after",
    "x-ratelimit-reset",
    "x-ratelimit-reset-after",
)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
        max_retries: int = 2,
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.thinking = thinking
        self.max_concurrency = max(1, int(max_concurrency))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._asyncio_loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._pace_lock: asyncio.Lock | None = None
        self._ensure_loop_primitives()
        self._last_request_started = 0.0
        self.usage_stats = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        self.last_response_metadata: dict[str, Any] = {}

    def _ensure_loop_primitives(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._asyncio_loop is not loop or self._semaphore is None or self._pace_lock is None:
            self._asyncio_loop = loop
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
            self._pace_lock = asyncio.Lock()

    def _is_kimi_k2_model(self) -> bool:
        return self.model.startswith("kimi-k2")

    def _is_deepseek_model(self) -> bool:
        return self.model.startswith("deepseek-") or "deepseek.com" in self.base_url

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: Optional[dict[str, str]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        # Kimi K2.x and DeepSeek thinking mode both prefer temperature omitted.
        if not self._is_kimi_k2_model() and not (self._is_deepseek_model() and self.thinking):
            payload["temperature"] = temperature

        if response_format:
            payload["response_format"] = response_format

        if self._is_kimi_k2_model():
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        elif self._is_deepseek_model():
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}

        return payload

    def _provider_name(self) -> str:
        if "moonshot" in self.base_url or self.model.startswith("kimi-"):
            return "kimi"
        if "bigmodel" in self.base_url or self.model.startswith("glm-"):
            return "zhipu"
        if "deepseek" in self.base_url or self.model.startswith("deepseek-"):
            return "deepseek"
        return "unknown"

    @staticmethod
    def _selected_response_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
        """Return non-secret provider receipt headers useful for audit trails."""
        return {name: str(headers[name]) for name in PROVIDER_TRACE_HEADER_NAMES if name in headers}

    def _provider_trace_metadata(
        self,
        *,
        request_id: str,
        started: float,
        success: bool,
        status_code: int | None = None,
        response_data: dict[str, Any] | None = None,
        response_headers: httpx.Headers | dict[str, str] | None = None,
        error: str = "",
        error_type: str = "",
    ) -> dict[str, Any]:
        usage = (response_data or {}).get("usage") or {}
        selected_headers = self._selected_response_headers(response_headers or {})
        provider_request_id = ""
        for header_name in PROVIDER_TRACE_HEADER_NAMES:
            if selected_headers.get(header_name):
                provider_request_id = selected_headers[header_name]
                break
        return {
            "success": success,
            "provider": self._provider_name(),
            "base_url": self.base_url,
            "model": self.model,
            "status_code": status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "local_request_id": request_id,
            "provider_response_id": str((response_data or {}).get("id") or ""),
            "provider_request_id": provider_request_id,
            "response_headers": selected_headers,
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
            "error": error,
            "error_type": error_type,
        }

    def _append_trace_log(self, metadata: dict[str, Any], context: dict[str, Any]) -> None:
        """Append non-secret provider receipt metadata for real-run audits."""
        try:
            from aily.config import SETTINGS

            trace_path = SETTINGS.llm_trace_log_path
        except Exception:
            trace_path = None
        if not trace_path:
            return
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **metadata,
            "pipeline_id": context.get("pipeline_id"),
            "upload_id": context.get("upload_id"),
            "stage": context.get("stage"),
            "workload": context.get("workload"),
        }
        try:
            path = trace_path.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to append LLM trace log")

    async def _wait_for_rate_window(self) -> None:
        """Ensure request starts are spaced out to avoid bursty imports."""
        if self.min_interval_seconds <= 0:
            return

        self._ensure_loop_primitives()
        assert self._pace_lock is not None
        async with self._pace_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_started
            if elapsed < self.min_interval_seconds:
                await asyncio.sleep(self.min_interval_seconds - elapsed)
            self._last_request_started = time.monotonic()

    @staticmethod
    def _retry_after_seconds(headers: httpx.Headers | dict[str, str] | None) -> float | None:
        """Parse provider retry-after hints without throwing on malformed headers."""
        if not headers:
            return None
        for name in ("retry-after", "x-ratelimit-reset-after"):
            value = headers.get(name) if hasattr(headers, "get") else None
            if value in (None, ""):
                continue
            try:
                delay = float(str(value).strip())
            except ValueError:
                continue
            if delay > 0:
                return min(delay, 180.0)
        return None

    def _retry_delay_seconds(self, attempt: int, exc: Exception) -> float:
        """Return conservative retry delays for real provider pressure."""
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            retry_after = self._retry_after_seconds(exc.response.headers)
            if retry_after is not None:
                return retry_after
            return min(30.0 * (2 ** attempt), 180.0)
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            return min(5.0 * (2 ** attempt), 60.0)
        return min(2.0 * (2 ** attempt), 30.0)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        response_format: Optional[dict[str, str]] = None,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._chat_once(messages, temperature, response_format)
            except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning("LLM timeout (attempt %s)", attempt + 1)
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay_seconds(attempt, exc))
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    if attempt < self.max_retries:
                        wait_time = self._retry_delay_seconds(attempt, exc)
                        logger.warning("Rate limited (429), waiting %ss before retry %s", wait_time, attempt + 1)
                        await asyncio.sleep(wait_time)
                else:
                    logger.warning("LLM HTTP error (attempt %s): %s", attempt + 1, exc)
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._retry_delay_seconds(attempt, exc))
            except LLMError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("LLM call failed (attempt %s): %s", attempt + 1, exc)
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay_seconds(attempt, exc))
        raise LLMError(f"LLM failed after {self.max_retries + 1} attempts: {last_error}")

    async def complete(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> str:
        """Compatibility wrapper for single-prompt callers.

        Older DIKIWI gates and skills use a completion-style interface while
        provider implementations use OpenAI-compatible chat completions.
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, temperature=temperature)

    async def _chat_once(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: Optional[dict[str, str]],
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(messages, temperature, response_format)
        context = get_ui_telemetry_context()
        request_id = str(uuid4())
        started = time.monotonic()
        self._ensure_loop_primitives()
        assert self._semaphore is not None
        await emit_ui_event(
            "llm_request_started",
            request_id=request_id,
            provider=self._provider_name(),
            model=self.model,
            base_url=self.base_url,
            message_count=len(messages),
            pipeline_id=context.get("pipeline_id"),
            upload_id=context.get("upload_id"),
            stage=context.get("stage"),
            workload=context.get("workload"),
        )

        try:
            async with provider_backpressure.limit(self._provider_name(), self.max_concurrency):
                async with self._semaphore:
                    await self._wait_for_rate_window()
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=headers,
                            json=payload,
                        )
                        resp.raise_for_status()
                        data = resp.json()
        except Exception as exc:
            response = getattr(exc, "response", None)
            self.last_response_metadata = self._provider_trace_metadata(
                request_id=request_id,
                started=started,
                success=False,
                status_code=getattr(response, "status_code", None),
                response_headers=getattr(response, "headers", None),
                error=str(exc) or repr(exc),
                error_type=exc.__class__.__name__,
            )
            self._append_trace_log(self.last_response_metadata, context)
            await emit_ui_event(
                "llm_request_failed",
                request_id=request_id,
                provider=self._provider_name(),
                model=self.model,
                pipeline_id=context.get("pipeline_id"),
                upload_id=context.get("upload_id"),
                stage=context.get("stage"),
                workload=context.get("workload"),
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            raise

        choices = data.get("choices", [])
        if not choices:
            self.last_response_metadata = self._provider_trace_metadata(
                request_id=request_id,
                started=started,
                success=False,
                status_code=resp.status_code,
                response_data=data,
                response_headers=resp.headers,
                error="Empty response from LLM",
                error_type="LLMError",
            )
            self._append_trace_log(self.last_response_metadata, context)
            await emit_ui_event(
                "llm_request_failed",
                request_id=request_id,
                provider=self._provider_name(),
                model=self.model,
                pipeline_id=context.get("pipeline_id"),
                upload_id=context.get("upload_id"),
                stage=context.get("stage"),
                workload=context.get("workload"),
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                error="Empty response from LLM",
                error_type="LLMError",
            )
            raise LLMError("Empty response from LLM")
        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        self.usage_stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.usage_stats["completion_tokens"] += usage.get("completion_tokens", 0)
        self.usage_stats["total_tokens"] += usage.get("total_tokens", 0)
        self.usage_stats["calls"] += 1
        self.last_response_metadata = self._provider_trace_metadata(
            request_id=request_id,
            started=started,
            success=True,
            status_code=resp.status_code,
            response_data=data,
            response_headers=resp.headers,
        )
        self._append_trace_log(self.last_response_metadata, context)
        await emit_ui_event(
            "llm_request_completed",
            request_id=request_id,
            provider=self._provider_name(),
            model=self.model,
            pipeline_id=context.get("pipeline_id"),
            upload_id=context.get("upload_id"),
            stage=context.get("stage"),
            workload=context.get("workload"),
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            provider_response_id=self.last_response_metadata.get("provider_response_id"),
            provider_request_id=self.last_response_metadata.get("provider_request_id"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
        return content.strip()

    def get_usage_stats(self) -> dict[str, int]:
        return self.usage_stats.copy()

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> Any:
        content = await self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON from LLM, attempting repair")
            try:
                import json_repair

                repaired = await asyncio.to_thread(json_repair.repair_json, content)
                return json.loads(repaired)
            except Exception as repair_exc:
                raise LLMError(f"Could not parse or repair JSON: {repair_exc}") from exc
