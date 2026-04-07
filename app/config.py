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

    # Polymarket Wide Scan (Tier 1)
    pm_gamma_limit: int = 500                       # Gamma API limit（最大 500）
    pm_wide_volume_spike_ratio: float = 2.0          # 24h / 日均 volume 倍数
    pm_wide_price_velocity_1d: float = 0.05          # |oneDayPriceChange| 阈值（小数）
    pm_wide_price_velocity_1h: float = 0.03          # |oneHourPriceChange| 阈值（小数）
    pm_wide_spread_threshold: float = 0.10           # 价差异常阈值
    pm_wide_volume_floor: float = 1000.0             # 最低 24h volume（过滤噪音）
    pm_wide_breaking_threshold: float = 0.3           # 复合 breaking score 进入 Tier 2 的阈值
    pm_wide_max_tier2: int = 50                      # Tier 2 最大市场数上限

    # Feishu Webhook
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""

    # Social Media — Corroboration
    sm_corroboration_enabled: bool = True
    sm_corroboration_timeout: float = 10.0
    sm_twitter_api_key: str = ""
    sm_hn_hours_back: int = 72
    sm_hn_min_points: int = 5

    # Hacker News — standalone source
    hn_front_page_min_points: int = 100
    hn_rising_hours_back: int = 6
    hn_rising_min_points: int = 30
    hn_max_stories_per_run: int = 15

    # Signal Stream (for poly_trader)
    signal_stream_key: str = "stream:polymarket:signals"

    # Alert storage
    alert_max_per_source: int = 100

    # 财联社 (CLS)
    cls_base_url: str = "https://www.cls.cn/nodeapi"
    cls_fetch_limit: int = 50

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "intel_sys/0.1"
    reddit_subreddits: list[str] = ["wallstreetbets", "investing", "stocks"]
    reddit_min_score: int = 50
    reddit_fetch_limit: int = 25

    # 雪球 (Xueqiu)
    xueqiu_cookie: str = ""
    xueqiu_fetch_limit: int = 20

    # Event Memory Pool
    memory_pool_key: str = "memory:events"
    memory_pool_ttl_days: int = 7
    memory_compress_max_chars: int = 100

    # Correlation Engine
    correlation_interval: int = 1800
    correlation_lookback_hours: int = 168
    correlation_min_events: int = 10
    correlation_context_max_chars: int = 12000

    # Insight Delivery (独立飞书 webhook)
    feishu_insight_webhook_url: str = ""
    feishu_insight_webhook_secret: str = ""

    # Defense News Scraping
    defense_rss_interval: int = 1800
    defense_rss_concurrency: int = 5
    defense_domain_min_interval: float = 10.0
    defense_rss_timeout: float = 15.0
    defense_topk: int = 200
    defense_dedup_ttl: int = 604800
    defense_max_entries_per_source: int = 30
    defense_cooldown_hours: int = 6
    defense_max_consecutive_failures: int = 3
    defense_disable_threshold: int = 10
    defense_alert_threshold: float = 0.3

    # PostgreSQL
    pg_dsn: str = ""
    pg_pool_min: int = 2
    pg_pool_max: int = 10

    # Defense Feishu Webhook (独立 bot)
    feishu_defense_webhook_url: str = ""
    feishu_defense_webhook_secret: str = ""

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        """Allow JSON strings for list fields from env vars."""
        return super().settings_customise_sources(settings_cls, **kwargs)


settings = Settings()
