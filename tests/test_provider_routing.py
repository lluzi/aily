"""Workload→provider routing, incl. single-provider fallback."""

from types import SimpleNamespace

from aily.llm.provider_routes import PrimaryLLMRoute


def _settings(**overrides):
    base = dict(
        llm_provider="deepseek",
        kimi_api_key="",
        deepseek_api_key="ds-key",
        llm_api_key="",
        llm_workload_routes_json="",
        kimi_model="kimi-k2.6",
        deepseek_model="deepseek-chat",
        llm_model="",
        llm_base_url="",
        llm_max_concurrency=1,
        llm_min_interval_seconds=6.0,
        llm_timeout_seconds=120.0,
        llm_max_retries=2,
        kimi_vision_model="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_dikiwi_data_falls_back_to_configured_provider_without_kimi_key():
    # dikiwi.data prefers kimi by default, but with no kimi key it must use the
    # configured provider (deepseek) rather than 401 against moonshot.
    route = PrimaryLLMRoute.resolve_route(_settings(), workload="dikiwi.data")
    assert route.provider == "deepseek"
    assert route.api_key == "ds-key"
    assert "deepseek.com" in route.base_url


def test_kimi_workload_used_when_kimi_key_present():
    route = PrimaryLLMRoute.resolve_route(_settings(kimi_api_key="k-key"), workload="dikiwi.data")
    assert route.provider == "kimi"
    assert route.api_key == "k-key"


def test_knowledge_stage_uses_deepseek():
    route = PrimaryLLMRoute.resolve_route(_settings(), workload="dikiwi.knowledge")
    assert route.provider == "deepseek"


def test_default_workload_uses_configured_provider():
    route = PrimaryLLMRoute.resolve_route(_settings(), workload="default")
    assert route.provider == "deepseek"


def test_shared_llm_api_key_does_not_make_kimi_look_configured():
    # config.model_post_init copies the deepseek key into the shared llm_api_key.
    # A kimi-preferred workload must NOT pick kimi (and call moonshot with the
    # deepseek key) — it must fall back to the configured provider.
    settings = _settings(deepseek_api_key="ds-key", llm_api_key="ds-key", kimi_api_key="")
    route = PrimaryLLMRoute.resolve_route(settings, workload="dikiwi.data")
    assert route.provider == "deepseek"
    assert "deepseek.com" in route.base_url
    assert route.api_key == "ds-key"
