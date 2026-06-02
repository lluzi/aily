from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings

from aily.thinking.config import ThinkingConfig


@dataclass
class MindsConfig:
    """Configuration for Aily Three-Mind System.

    Controls the DIKIWI Mind (continuous), Innovation Mind (8am daily),
    and Entrepreneur Mind (9am daily).
    """

    # Feature toggles
    dikiwi_enabled: bool = True
    innovation_enabled: bool = True
    entrepreneur_enabled: bool = True
    mac_enabled: bool = True  # On by default: MAC loop drives Reactor -> Residual -> Entrepreneur pipeline

    # Schedule times (24-hour format)
    innovation_time: time = field(default_factory=lambda: time(8, 0))
    entrepreneur_time: time = field(default_factory=lambda: time(9, 0))

    # Quality thresholds
    proposal_min_confidence: float = 0.7  # Raised from 0.5 per eng review
    proposal_max_per_session: int = 10

    # Circuit breaker settings
    circuit_breaker_threshold: int = 3
    circuit_breaker_recovery_minutes: int = 30

    # LLM batching/caching
    enable_caching: bool = True
    cache_ttl_seconds: int = 3600
    batch_proposals: bool = True

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "MindsConfig":
        """Create MindsConfig from settings dict (e.g., from env vars)."""
        config = cls()

        def value(name: str, default: str) -> str:
            normalized = name.removeprefix("aily_")
            return settings.get(name, settings.get(normalized, default))

        # Parse boolean flags
        config.dikiwi_enabled = value("aily_dikiwi_enabled", "true").lower() == "true"
        config.innovation_enabled = value("aily_innovation_enabled", "true").lower() == "true"
        config.entrepreneur_enabled = value("aily_entrepreneur_enabled", "true").lower() == "true"
        config.mac_enabled = value("aily_mac_enabled", "true").lower() == "true"

        # Parse times
        innovation_time_str = value("aily_innovation_time", "08:00")
        entrepreneur_time_str = value("aily_entrepreneur_time", "09:00")
        config.innovation_time = cls._parse_time(innovation_time_str)
        config.entrepreneur_time = cls._parse_time(entrepreneur_time_str)

        # Parse floats/ints
        config.proposal_min_confidence = float(value("aily_proposal_min_confidence", "0.7"))
        config.proposal_max_per_session = int(value("aily_proposal_max_per_session", "10"))
        config.circuit_breaker_threshold = int(value("aily_circuit_breaker_threshold", "3"))

        return config

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse time string like '08:00' or '8:30'."""
        try:
            parts = time_str.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return time(hour, minute)
        except (ValueError, IndexError):
            return time(8, 0)  # Default fallback

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors (empty if valid)."""
        errors = []

        if not 0.0 <= self.proposal_min_confidence <= 1.0:
            errors.append(f"proposal_min_confidence must be between 0 and 1, got {self.proposal_min_confidence}")

        if self.proposal_max_per_session < 1:
            errors.append(f"proposal_max_per_session must be >= 1, got {self.proposal_max_per_session}")

        if self.circuit_breaker_threshold < 1:
            errors.append(f"circuit_breaker_threshold must be >= 1, got {self.circuit_breaker_threshold}")

        # Check that innovation and entrepreneur times don't overlap
        innovation_end = self.innovation_time.replace(minute=self.innovation_time.minute + 30)
        if self.innovation_enabled and self.entrepreneur_enabled:
            if innovation_end.hour > self.entrepreneur_time.hour or \
               (innovation_end.hour == self.entrepreneur_time.hour and innovation_end.minute >= self.entrepreneur_time.minute):
                errors.append("Innovation and Entrepreneur times may overlap (need 30 min gap)")

        return errors


class Settings(BaseSettings):
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    obsidian_rest_api_key: str = ""
    obsidian_vault_path: str = ""
    dikiwi_vault_path: str = "/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily"
    obsidian_rest_api_port: int = 27123
    llm_provider: str = "kimi"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.moonshot.cn/v1"
    llm_model: str = "kimi-k2.6"
    llm_workload_routes_json: str = ""
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_max_concurrency: int = 1
    llm_min_interval_seconds: float = 6.0
    llm_trace_log_path: Path | None = None
    dikiwi_foundation_only_ingestion: bool = True
    dikiwi_max_llm_calls_per_source: int = 30
    dikiwi_stage_round_limit: int = 4
    dikiwi_stage_timeout_seconds: float = 600.0
    dikiwi_wisdom_review_enabled: bool = False
    dikiwi_batch_stage_concurrency: int = 4
    reactor_method_timeout_seconds: float = 180.0
    dikiwi_incremental_trigger_ratio: float = 0.05
    dikiwi_network_min_nodes: int = 3
    dikiwi_network_trigger_score: float = 4.0
    dikiwi_network_max_candidate_nodes: int = 18
    dikiwi_higher_order_max_contexts: int = 3
    mineru_batch_extract_concurrency: int = 4
    entrepreneur_evaluation_timeout_minutes: int = 3
    kimi_api_key: str = ""
    kimi_model: str = "kimi-k2.6"
    kimi_vision_model: str = "kimi-k2.6"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-pro"
    zhipu_api_key: str = ""
    zhipu_model: str = "glm-5.1"
    zhipu_vision_model: str = "glm-4.5v"

    # Tavily search API
    tavily_api_key: str = ""
    tavily_search_depth: str = "basic"  # "basic" or "advanced"

    # Browser Use commercial API
    browser_use_api_key: str = ""

    aily_digest_enabled: bool = True
    aily_digest_hour: int = 9
    aily_digest_minute: int = 0
    aily_digest_feishu_open_id: str = ""
    aily_data_dir: Path = Path.home() / ".aily"
    dikiwi_batch_lock_path: Path = Path.home() / ".aily" / "dikiwi_batch.lock"

    # V1 orchestration settings
    orchestrator_enabled: bool = False
    orchestrator_shadow_mode: bool = True
    inbox_path: Path = Path.home() / "Aily" / "Inbox"
    inbox_watcher_enabled: bool = False
    inbox_poll_interval_seconds: float = 5.0
    inbox_file_stable_seconds: float = 2.0
    research_daily_budget: int = 10
    email_delivery_enabled: bool = False

    # Voice memo settings
    feishu_voice_enabled: bool = False  # Disabled by default until configured
    whisper_api_key: str = ""  # Falls back to llm_api_key if empty
    whisper_model: str = "whisper-1"
    voice_temp_dir: Path = Path("/tmp/aily_voice")

    # File processing limits (bytes)
    max_file_size: int = 50 * 1024 * 1024  # 50MB default limit
    max_image_size: int = 10 * 1024 * 1024  # 10MB for images (OCR memory limit)
    ui_max_upload_files: int = 8
    ui_max_active_uploads: int = 16
    ui_upload_concurrency: int = 2
    source_worker_count: int = 1
    source_job_max_pending: int = 500
    source_max_retry_attempts: int = 5
    source_retry_base_delay_seconds: float = 300.0
    source_retry_max_delay_seconds: float = 3600.0
    source_job_stale_lock_seconds: float = 1800.0
    follow_external_links_for_uploads: bool = False
    url_intake_allow_private_network: bool = False
    ui_event_trace_limit: int = 200
    ui_auth_enabled: bool = False
    ui_auth_token: str = ""
    trusted_proxy_headers: bool = False
    hosted_mode: bool = False
    ui_rate_limit_requests: int = 20
    ui_rate_limit_window_seconds: float = 60.0
    audit_log_path: Path | None = None
    evidence_runs_dir: Path = Path.home() / ".aily" / "runs"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def queue_db_path(self) -> Path:
        return self.aily_data_dir / "aily_queue.db"

    @property
    def graph_db_path(self) -> Path:
        return self.aily_data_dir / "aily_graph.db"

    @property
    def source_store_db_path(self) -> Path:
        return self.aily_data_dir / "source_store.db"

    @property
    def source_object_dir(self) -> Path:
        return self.aily_data_dir / "sources"

    @property
    def canonical_markdown_dir(self) -> Path:
        return self.aily_data_dir / "markdown_packages"

    @property
    def ui_event_log_path(self) -> Path:
        return self.aily_data_dir / "ui-events.jsonl"

    @property
    def langgraph_checkpoint_db_path(self) -> Path:
        return self.aily_data_dir / "langgraph_checkpoints.sqlite"

    @property
    def workflow_runs_db_path(self) -> Path:
        return self.aily_data_dir / "workflow_runs.db"

    @property
    def chat_store_db_path(self) -> Path:
        return self.aily_data_dir / "chat_store.db"

    @property
    def research_store_db_path(self) -> Path:
        return self.aily_data_dir / "research_store.db"

    @property
    def business_plan_store_db_path(self) -> Path:
        return self.aily_data_dir / "business_plan_store.db"

    @property
    def resolved_audit_log_path(self) -> Path:
        return self.audit_log_path or (self.aily_data_dir / "audit.jsonl")

    # Thinking system configuration
    thinking: ThinkingConfig = ThinkingConfig()

    # Three-Mind System configuration
    minds: MindsConfig = field(default_factory=MindsConfig)

    def model_post_init(self, __context: Any) -> None:
        """Initialize minds config from environment after main init."""
        import os

        provider = self.llm_provider.lower()
        moonshot_api_key = os.getenv("MOONSHOT_API_KEY", "")

        if provider == "kimi":
            if not self.kimi_api_key:
                self.kimi_api_key = self.llm_api_key or moonshot_api_key
            if not self.llm_api_key:
                self.llm_api_key = self.kimi_api_key or moonshot_api_key
            self.llm_base_url = "https://api.moonshot.cn/v1"
            self.llm_model = self.kimi_model or self.llm_model

        if provider == "zhipu":
            if not self.zhipu_api_key:
                self.zhipu_api_key = self.llm_api_key
            if not self.llm_api_key:
                self.llm_api_key = self.zhipu_api_key
            self.llm_base_url = "https://open.bigmodel.cn/api/paas/v4"
            self.llm_model = self.zhipu_model or self.llm_model

        if provider == "deepseek":
            if not self.deepseek_api_key:
                self.deepseek_api_key = self.llm_api_key
            if not self.llm_api_key:
                self.llm_api_key = self.deepseek_api_key
            self.llm_base_url = "https://api.deepseek.com"
            self.llm_model = self.deepseek_model or self.llm_model

        # Parse minds config from env vars
        env_vars = {
            k.lower().replace("aily_", ""): v
            for k, v in os.environ.items()
            if k.startswith("AILY_")
        }
        self.minds = MindsConfig.from_settings(env_vars)

        # Validate and log any errors
        errors = self.minds.validate()
        if errors:
            import logging
            for error in errors:
                logging.getLogger(__name__).warning("MindsConfig validation: %s", error)



    def validate_runtime_security(self) -> list[str]:
        errors: list[str] = []
        if self.hosted_mode or self.ui_auth_enabled:
            token = self.ui_auth_token.strip()
            placeholders = {"", "change-me", "changeme", "secret", "secret-token", "password", "token"}
            if token.lower() in placeholders or len(token) < 16:
                errors.append(
                    "UI_AUTH_TOKEN must be set to a non-placeholder value of at least 16 characters "
                    "when HOSTED_MODE or UI_AUTH_ENABLED is true"
                )

        if self.hosted_mode and self.minds.dikiwi_enabled:
            provider = self.llm_provider.lower().strip()
            provider_keys = {
                "kimi": self.kimi_api_key or self.llm_api_key,
                "moonshot": self.kimi_api_key or self.llm_api_key,
                "deepseek": self.deepseek_api_key or self.llm_api_key,
                "zhipu": self.zhipu_api_key or self.llm_api_key,
            }
            key = provider_keys.get(provider, self.llm_api_key)
            if not str(key or "").strip():
                errors.append(
                    "A real LLM provider key is required when HOSTED_MODE=true and AILY_DIKIWI_ENABLED=true; "
                    "set LLM_API_KEY or the provider-specific API key"
                )
            vault_path = str(self.dikiwi_vault_path or self.obsidian_vault_path or "").strip()
            if not vault_path:
                errors.append(
                    "DIKIWI_VAULT_PATH or OBSIDIAN_VAULT_PATH is required when HOSTED_MODE=true "
                    "and AILY_DIKIWI_ENABLED=true"
                )
        return errors


SETTINGS = Settings()
