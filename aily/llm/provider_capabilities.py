from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    default_model: str
    text: bool
    vision: bool
    json_mode: bool
    long_context_tokens: int
    innovation_strength: str
    best_for: tuple[str, ...]
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["best_for"] = list(self.best_for)
        payload["caveats"] = list(self.caveats)
        return payload


PROVIDER_CAPABILITIES: dict[str, ProviderCapability] = {
    "kimi": ProviderCapability(
        provider="kimi",
        default_model="kimi-k2.6",
        text=True,
        vision=True,
        json_mode=True,
        long_context_tokens=128_000,
        innovation_strength="long-context extraction and grounded synthesis",
        best_for=("dikiwi.DATA", "dikiwi.INFORMATION", "chaos.vision", "bulk source normalization"),
        caveats=("Use pacing for large batches.",),
    ),
    "deepseek": ProviderCapability(
        provider="deepseek",
        default_model="deepseek-v4-pro",
        text=True,
        vision=False,
        json_mode=True,
        long_context_tokens=128_000,
        innovation_strength="upper-layer reasoning, proposals, entrepreneur review",
        best_for=("dikiwi.KNOWLEDGE", "dikiwi.INSIGHT", "dikiwi.WISDOM", "dikiwi.IMPACT", "reactor", "entrepreneur", "guru"),
        caveats=("Do not route vision workloads here without a verified multimodal endpoint.",),
    ),
}


def provider_capability_matrix() -> dict[str, dict[str, Any]]:
    return {name: capability.to_dict() for name, capability in sorted(PROVIDER_CAPABILITIES.items())}


def workload_route_matrix(default_routes: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workload, route in sorted(default_routes.items()):
        provider = route.get("provider", "")
        capability = PROVIDER_CAPABILITIES.get(provider)
        rows.append(
            {
                "workload": workload,
                "provider": provider,
                "model": route.get("model") or (capability.default_model if capability else ""),
                "capability": capability.innovation_strength if capability else "unknown",
                "best_for": list(capability.best_for) if capability else [],
            }
        )
    return rows
