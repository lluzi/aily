from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from aily.llm.client import LLMClient
from aily.llm.llm_router import LLMRouter
from aily.llm.provider_capabilities import provider_capability_matrix, workload_route_matrix


@dataclass(frozen=True)
class ProviderRoute:
    """Named route to a concrete LLM provider."""

    provider: str
    model: str
    base_url: str
    mode: str


@dataclass(frozen=True)
class ResolvedLLMRoute:
    """Workload-specific route resolved from settings plus overrides."""

    workload: str
    provider: str
    model: str
    base_url: str
    api_key: str
    thinking: bool
    max_concurrency: int
    min_interval_seconds: float
    timeout: float
    max_retries: int


class PrimaryLLMRoute:
    """Build the app's primary LLM client from workload-aware routes."""

    KIMI_ROUTE = ProviderRoute(
        provider="kimi",
        model="kimi-k2.6",
        base_url="https://api.moonshot.cn/v1",
        mode="standard",
    )

    DEEPSEEK_ROUTE = ProviderRoute(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        mode="standard",
    )

    # Default workload → provider mapping. JSON overrides from
    # llm_workload_routes_json take precedence over these defaults.
    # Stage 01-02 (DATA, INFORMATION): Kimi — fast, cheap extraction
    # Stage 03-08 (KNOWLEDGE → Entrepreneur): DeepSeek — strong reasoning
    DEFAULT_WORKLOAD_ROUTES: dict[str, dict[str, str]] = {
        "dikiwi.data": {"provider": "kimi"},
        "dikiwi.information": {"provider": "kimi"},
        "dikiwi.knowledge": {"provider": "deepseek"},
        "dikiwi.insight": {"provider": "deepseek"},
        "dikiwi.wisdom": {"provider": "deepseek"},
        "dikiwi.impact": {"provider": "deepseek"},
        "dikiwi.residual": {"provider": "deepseek"},
        "reactor": {"provider": "deepseek"},
        "entrepreneur": {"provider": "deepseek"},
        "guru": {"provider": "deepseek"},
        "gstack": {"provider": "deepseek"},
        "chaos.vision": {"provider": "kimi"},
        "copilot.chat": {"provider": "deepseek"},
        "copilot.dossier": {"provider": "deepseek"},
    }

    @classmethod
    def describe_routes(cls) -> dict[str, Any]:
        return {
            "providers": provider_capability_matrix(),
            "workloads": workload_route_matrix(cls.DEFAULT_WORKLOAD_ROUTES),
        }

    @classmethod
    def route_kimi(
        cls,
        *,
        api_key: str,
        model: str = "kimi-k2.6",
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 6.0,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> LLMClient:
        return LLMRouter.standard_kimi(
            api_key=api_key,
            model=model,
            thinking=thinking,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
            timeout=timeout,
            max_retries=max_retries,
        )

    @classmethod
    def route_deepseek(
        cls,
        *,
        api_key: str,
        model: str = "deepseek-v4-pro",
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 6.0,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> LLMClient:
        return LLMRouter.standard_deepseek(
            api_key=api_key,
            model=model,
            thinking=thinking,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
            timeout=timeout,
            max_retries=max_retries,
        )

    @staticmethod
    def _workload_candidates(workload: str) -> list[str]:
        if not isinstance(workload, str) and getattr(workload, "name", ""):
            normalized = f"dikiwi.{str(getattr(workload, 'name')).lower()}"
        else:
            normalized = str(workload or "default").strip().lower() or "default"
        if normalized == "default":
            return ["default"]
        parts = normalized.split(".")
        candidates = [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
        candidates.append("default")
        ordered: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        return ordered

    @staticmethod
    def _load_workload_overrides(settings: Any) -> dict[str, dict[str, Any]]:
        raw = str(getattr(settings, "llm_workload_routes_json", "") or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid llm_workload_routes_json: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("llm_workload_routes_json must decode to an object")

        overrides: dict[str, dict[str, Any]] = {}
        for key, value in parsed.items():
            if isinstance(key, str) and isinstance(value, dict):
                overrides[key.strip().lower()] = value
        return overrides

    @classmethod
    def _default_route_for_provider(cls, provider: str) -> ProviderRoute:
        normalized = str(provider or "kimi").strip().lower()
        if normalized == "kimi":
            return cls.KIMI_ROUTE
        if normalized == "deepseek":
            return cls.DEEPSEEK_ROUTE
        raise ValueError(
            f"Unsupported llm_provider={provider!r}. Active providers are: kimi, deepseek."
        )

    @staticmethod
    def _api_key_for_provider(settings: Any, provider: str) -> str:
        normalized = str(provider).strip().lower()
        if normalized == "kimi":
            return getattr(settings, "kimi_api_key", "") or getattr(settings, "llm_api_key", "")
        if normalized == "deepseek":
            return getattr(settings, "deepseek_api_key", "") or getattr(settings, "llm_api_key", "")
        raise ValueError(f"Unsupported provider={provider!r} for API key resolution.")

    @classmethod
    def resolve_route(
        cls,
        settings: Any,
        *,
        workload: str = "default",
        thinking: bool | None = None,
    ) -> ResolvedLLMRoute:
        provider = str(getattr(settings, "llm_provider", "kimi")).strip().lower()
        default_route = cls._default_route_for_provider(provider)
        # Merge defaults with JSON overrides (JSON takes precedence)
        merged_overrides: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in cls.DEFAULT_WORKLOAD_ROUTES.items()
        }
        json_overrides = cls._load_workload_overrides(settings)
        for k, v in json_overrides.items():
            if k in merged_overrides:
                merged_overrides[k].update(v)
            else:
                merged_overrides[k] = dict(v)

        resolved_provider = provider
        resolved_model = (
            getattr(settings, f"{provider}_model", "")
            or getattr(settings, "llm_model", "")
            or default_route.model
        )
        resolved_base_url = getattr(settings, "llm_base_url", "") or default_route.base_url
        resolved_thinking = bool(thinking) if thinking is not None else False
        resolved_concurrency = int(getattr(settings, "llm_max_concurrency", 1))
        resolved_interval = float(getattr(settings, "llm_min_interval_seconds", 6.0))
        resolved_timeout = float(getattr(settings, "llm_timeout_seconds", 120.0))
        resolved_max_retries = max(0, int(getattr(settings, "llm_max_retries", 2)))

        applied_override: dict[str, Any] = {}
        for candidate in reversed(cls._workload_candidates(workload)):
            override = merged_overrides.get(candidate)
            if not override:
                continue
            applied_override.update(override)

        if applied_override:
            override_provider = str(applied_override.get("provider", resolved_provider)).strip().lower()
            if override_provider:
                override_default = cls._default_route_for_provider(override_provider)
                if override_provider != resolved_provider:
                    resolved_base_url = override_default.base_url
                resolved_provider = override_provider

            provider_model = getattr(settings, f"{resolved_provider}_model", "")
            resolved_model = str(
                applied_override.get("model", provider_model or resolved_model or default_route.model)
            ).strip() or (provider_model or default_route.model)
            resolved_base_url = str(
                applied_override.get(
                    "base_url",
                    resolved_base_url or cls._default_route_for_provider(resolved_provider).base_url,
                )
            ).strip() or cls._default_route_for_provider(resolved_provider).base_url
            if "thinking" in applied_override and thinking is None:
                resolved_thinking = bool(applied_override.get("thinking"))
            if "max_concurrency" in applied_override:
                resolved_concurrency = max(1, int(applied_override["max_concurrency"]))
            if "min_interval_seconds" in applied_override:
                resolved_interval = max(0.0, float(applied_override["min_interval_seconds"]))
            if "timeout" in applied_override:
                resolved_timeout = max(1.0, float(applied_override["timeout"]))
            if "max_retries" in applied_override:
                resolved_max_retries = max(0, int(applied_override["max_retries"]))

        if workload.startswith("chaos.vision"):
            if resolved_provider == "kimi":
                resolved_model = getattr(settings, "kimi_vision_model", "") or resolved_model
        if resolved_provider == "deepseek" and workload.startswith("chaos.vision"):
            raise ValueError(
                "DeepSeek is not configured for chaos.vision workloads because the current official docs do not expose image/video chat input."
            )

        api_key = cls._api_key_for_provider(settings, resolved_provider)
        if "api_key" in applied_override:
            api_key = str(applied_override["api_key"])

        return ResolvedLLMRoute(
            workload=workload,
            provider=resolved_provider,
            model=resolved_model,
            base_url=resolved_base_url,
            api_key=api_key,
            thinking=resolved_thinking,
            max_concurrency=resolved_concurrency,
            min_interval_seconds=resolved_interval,
            timeout=resolved_timeout,
            max_retries=resolved_max_retries,
        )

    @classmethod
    def build_client(cls, route: ResolvedLLMRoute) -> LLMClient:
        if route.provider == "kimi":
            return cls.route_kimi(
                api_key=route.api_key,
                model=route.model,
                thinking=route.thinking,
                max_concurrency=route.max_concurrency,
                min_interval_seconds=route.min_interval_seconds,
                timeout=route.timeout,
                max_retries=route.max_retries,
            )
        if route.provider == "deepseek":
            return cls.route_deepseek(
                api_key=route.api_key,
                model=route.model,
                thinking=route.thinking,
                max_concurrency=route.max_concurrency,
                min_interval_seconds=route.min_interval_seconds,
                timeout=route.timeout,
                max_retries=route.max_retries,
            )
        raise ValueError(f"Unsupported route provider={route.provider!r}")

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        workload: str = "default",
        thinking: bool | None = None,
    ) -> LLMClient:
        route = cls.resolve_route(settings, workload=workload, thinking=thinking)
        return cls.build_client(route)

    @classmethod
    def build_settings_resolver(cls, settings: Any) -> Any:
        """Return a cached workload->client resolver for a settings object.

        The cache key includes the resolved route so runtime settings changes
        made through Aily-Copilot settings take effect for new requests.
        """
        cache: dict[tuple[Any, ...], LLMClient] = {}

        def _resolve(workload: str, thinking: bool | None = None) -> LLMClient:
            route = cls.resolve_route(settings, workload=str(workload or "default"), thinking=thinking)
            api_key_fingerprint = hashlib.sha256(route.api_key.encode("utf-8")).hexdigest() if route.api_key else ""
            key = (
                route.workload,
                thinking,
                route.provider,
                route.model,
                route.base_url,
                api_key_fingerprint,
                route.thinking,
                route.max_concurrency,
                route.min_interval_seconds,
                route.timeout,
                route.max_retries,
            )
            if key not in cache:
                cache[key] = cls.build_client(route)
            return cache[key]

        return _resolve
