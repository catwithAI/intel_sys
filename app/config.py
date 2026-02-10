from __future__ import annotations

import json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # OpenRouter
    openai_api_base: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-3-flash-preview"

    # GitHub
    github_token: str = ""
    github_topics: list[str] = ["ai", "web3", "infrastructure", "llm"]
    github_pool_size_per_topic: int = 250
    github_star_delta_threshold: int = 50
    github_gtrending_languages: list[str] = ["python", "typescript", "rust", "go"]
    github_gtrending_min_period_stars: int = 20

    # Polymarket thresholds
    pm_volume_spike_ratio: float = 3.0
    pm_book_imbalance_high: float = 0.7
    pm_book_imbalance_low: float = 0.3
    pm_price_velocity_pct: float = 5.0
    pm_price_velocity_window_min: int = 15

    # Polymarket fetch tuning
    pm_top_markets: int = 25          # 按 volume24hr 排序后取前 N 个市场
    pm_clob_concurrency: int = 8     # CLOB API 并发信号量上限

    # Feishu Webhook
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""

    # Alert storage
    alert_max_per_source: int = 100

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        """Allow JSON strings for list fields from env vars."""
        return super().settings_customise_sources(settings_cls, **kwargs)


settings = Settings()
