"""Application configuration using Pydantic settings."""

from functools import lru_cache
from typing import List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8080)

    database_url: str = Field(default="sqlite+aiosqlite:///./data/yapping.db")

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = Field(default="gpt-4o-mini")

    telegram_bot_token: str = Field(default="", repr=False)
    telegram_channel_id: str = Field(default="")
    telegram_owner_id: str = Field(default="")

    twitter_enabled: bool = Field(default=False)
    twitter_api_key: str = Field(default="", repr=False)
    twitter_api_secret: str = Field(default="", repr=False)
    twitter_access_token: str = Field(default="", repr=False)
    twitter_access_secret: str = Field(default="", repr=False)
    twitter_bearer_token: str = Field(default="", repr=False)
    twitter_user_id: str = Field(default="", repr=False)
    twitter_rate_max_calls: int = Field(default=12)
    twitter_rate_window_seconds: int = Field(default=900)  # 15 minutes
    twitter_min_interval_seconds: float = Field(default=75.0)
    twitter_search_monthly_cap: int = Field(default=15000)
    twitter_usage_reset_day: int = Field(default=20)
    twitter_usage_baseline_posts: int = Field(default=0)
    twitter_usage_baseline_requests: int = Field(default=0)

    post_random_delay_min_seconds: int = Field(default=15)
    post_random_delay_max_seconds: int = Field(default=120)
    queue_poll_interval_seconds: int = Field(default=60)
    queue_batch_size: int = Field(default=3)
    local_timezone: str = Field(default="Asia/Seoul")

    prompt_version: str = Field(default="PROMPT_KO_EN_V1")

    whitelist_keywords: List[str] = Field(
        default_factory=lambda: ["BTC", "ETH", "SOL", "Upbit", "listing", "funding", "kimchi premium"]
    )
    blacklist_keywords: List[str] = Field(
        default_factory=lambda: ["airdrop scam", "ref code", "guaranteed profit", "NSFW"]
    )
    manual_review_keywords: List[str] = Field(default_factory=lambda: ["rumor", "unverified", "leak"])

    rss_feeds: List[str] = Field(default_factory=list)
    rss_poll_interval_minutes: int = Field(default=10)
    rss_active_hours_local: List[int] = Field(default_factory=lambda: list(range(9, 24)))

    engagement_search_queries: List[str] = Field(default_factory=lambda: ["SOL", "BTC", "crypto alpha"])
    engagement_blacklist_keywords: List[str] = Field(default_factory=lambda: ["giveaway", "scam"])
    engagement_daily_like_limit: int = Field(default=150)
    engagement_daily_reply_limit: int = Field(default=40)
    engagement_score_weights: List[float] = Field(default_factory=lambda: [0.6, 0.3, 0.1])
    engagement_min_score: float = Field(default=5.0)
    engagement_poll_interval_seconds: int = Field(default=180)
    engagement_search_max_results: int = Field(default=10)
    engagement_max_dynamic_queries: int = Field(default=25)
    engagement_mindshare_query_limit: int = Field(default=40)
    engagement_auto_start: bool = Field(default=False)
    engagement_extra_queries: List[str] = Field(default_factory=list)
    engagement_include_inner_circle: bool = Field(default=True)
    engagement_inner_circle_multiplier: float = Field(default=1.5)
    engagement_priority_handles: List[str] = Field(default_factory=list)
    engagement_enable_project_bonus: bool = Field(default=False)
    engagement_keyword_boosts: List[str] = Field(
        default_factory=lambda: ["boost me", "boost us", "follow train", "ama recap", "리캡", "요약"]
    )
    engagement_keyword_bonus: float = Field(default=4.0)
    engagement_max_mentions: int = Field(default=2)
    engagement_reply_templates: List[str] = Field(
        default_factory=lambda: [
            "Appreciate the insights!",
            "Solid take — keeping an eye on this.",
            "Thanks for sharing, watching closely.",
        ]
    )
    engagement_new_handle_bonus: float = Field(default=3.0)
    engagement_handle_cooldown_completed: int = Field(default=4)
    engagement_handle_cooldown_hours: int = Field(default=12)
    engagement_handle_cooldown_penalty: float = Field(default=5.0)
    engagement_smart_follower_weight: float = Field(default=0.005)

    google_service_account_json: str = Field(default="")
    google_sheets_log_id: str = Field(default="")
    google_sheets_queue_id: str = Field(default="")

    @field_validator("rss_feeds", mode="before")
    @classmethod
    def parse_rss_feeds(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        return [item.strip() for item in cleaned.split(",") if item.strip()]

    @field_validator("rss_active_hours_local", mode="before")
    @classmethod
    def parse_active_hours(cls, value: Union[str, List[int], List[str]]) -> List[int]:
        if isinstance(value, list):
            if value and isinstance(value[0], int):
                return value
            return [int(item) for item in value]
        if not value:
            return []
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        return [int(item.strip()) for item in cleaned.split(",") if item.strip()]

    @field_validator("engagement_search_queries", "engagement_blacklist_keywords", mode="before")
    @classmethod
    def parse_str_list(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        return [item.strip().strip('"').strip("'") for item in cleaned.split(",") if item.strip()]

    @field_validator("engagement_score_weights", mode="before")
    @classmethod
    def parse_float_list(cls, value: Union[str, List[float]]) -> List[float]:
        if isinstance(value, list):
            return [float(item) for item in value]
        if not value:
            return [0.6, 0.3, 0.1]
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        return [float(item.strip()) for item in cleaned.split(",") if item.strip()]

@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()


settings = get_settings()
