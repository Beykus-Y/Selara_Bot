from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from selara.core.web_auth import normalize_base_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(..., validation_alias="BOT_TOKEN")
    bot_name: str = Field(default="Selara", validation_alias="BOT_NAME")
    bot_username: str = Field(default="selara_ru_bot", validation_alias="BOT_USERNAME")

    app_env: str = Field(default="dev", validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    bot_timezone: str = Field(default="UTC", validation_alias="BOT_TIMEZONE")

    database_url: str = Field(..., validation_alias="DATABASE_URL")
    db_pool_size: int = Field(default=10, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, validation_alias="DB_MAX_OVERFLOW")
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    game_state_ttl_hours: int = Field(default=24, validation_alias="GAME_STATE_TTL_HOURS")
    achievements_catalog_path: str = Field(
        default="src/selara/core/achievements.json",
        validation_alias="ACHIEVEMENTS_CATALOG_PATH",
    )

    top_limit_default: int = Field(default=10, validation_alias="TOP_LIMIT_DEFAULT")
    top_limit_max: int = Field(default=50, validation_alias="TOP_LIMIT_MAX")
    vote_daily_limit: int = Field(default=20, validation_alias="VOTE_DAILY_LIMIT")
    leaderboard_hybrid_karma_weight: float = Field(default=0.7, validation_alias="LEADERBOARD_HYBRID_KARMA_WEIGHT")
    leaderboard_hybrid_activity_weight: float = Field(default=0.3, validation_alias="LEADERBOARD_HYBRID_ACTIVITY_WEIGHT")
    leaderboard_7d_days: int = Field(default=7, validation_alias="LEADERBOARD_7D_DAYS")
    leaderboard_week_start_weekday: int = Field(default=0, validation_alias="LEADERBOARD_WEEK_START_WEEKDAY")
    leaderboard_week_start_hour: int = Field(default=0, validation_alias="LEADERBOARD_WEEK_START_HOUR")
    mafia_night_seconds: int = Field(default=90, validation_alias="MAFIA_NIGHT_SECONDS")
    mafia_day_seconds: int = Field(default=120, validation_alias="MAFIA_DAY_SECONDS")
    mafia_vote_seconds: int = Field(default=60, validation_alias="MAFIA_VOTE_SECONDS")
    mafia_reveal_eliminated_role: bool = Field(default=True, validation_alias="MAFIA_REVEAL_ELIMINATED_ROLE")
    text_commands_enabled: bool = Field(default=True, validation_alias="TEXT_COMMANDS_ENABLED")
    text_commands_locale: str = Field(default="ru", validation_alias="TEXT_COMMANDS_LOCALE")
    actions_18_enabled: bool = Field(default=True, validation_alias="ACTIONS_18_ENABLED")
    smart_triggers_enabled: bool = Field(default=True, validation_alias="SMART_TRIGGERS_ENABLED")
    welcome_enabled: bool = Field(default=True, validation_alias="WELCOME_ENABLED")
    welcome_text: str = Field(
        default="Привет, {user}! Добро пожаловать в {chat}.",
        validation_alias="WELCOME_TEXT",
    )
    welcome_button_text: str = Field(default="", validation_alias="WELCOME_BUTTON_TEXT")
    welcome_button_url: str = Field(default="", validation_alias="WELCOME_BUTTON_URL")
    goodbye_enabled: bool = Field(default=False, validation_alias="GOODBYE_ENABLED")
    goodbye_text: str = Field(default="Пока, {user}.", validation_alias="GOODBYE_TEXT")
    welcome_cleanup_service_messages: bool = Field(default=True, validation_alias="WELCOME_CLEANUP_SERVICE_MESSAGES")
    entry_captcha_enabled: bool = Field(default=False, validation_alias="ENTRY_CAPTCHA_ENABLED")
    entry_captcha_timeout_seconds: int = Field(default=180, validation_alias="ENTRY_CAPTCHA_TIMEOUT_SECONDS")
    entry_captcha_kick_on_fail: bool = Field(default=True, validation_alias="ENTRY_CAPTCHA_KICK_ON_FAIL")
    custom_rp_enabled: bool = Field(default=True, validation_alias="CUSTOM_RP_ENABLED")
    family_tree_enabled: bool = Field(default=True, validation_alias="FAMILY_TREE_ENABLED")
    titles_enabled: bool = Field(default=True, validation_alias="TITLES_ENABLED")
    title_price: int = Field(default=50000, validation_alias="TITLE_PRICE")
    craft_enabled: bool = Field(default=True, validation_alias="CRAFT_ENABLED")
    auctions_enabled: bool = Field(default=True, validation_alias="AUCTIONS_ENABLED")
    auction_duration_minutes: int = Field(default=10, validation_alias="AUCTION_DURATION_MINUTES")
    auction_min_increment: int = Field(default=100, validation_alias="AUCTION_MIN_INCREMENT")

    economy_enabled: bool = Field(default=True, validation_alias="ECONOMY_ENABLED")
    economy_mode: str = Field(default="global", validation_alias="ECONOMY_MODE")
    economy_tap_cooldown_seconds: int = Field(default=45, validation_alias="ECONOMY_TAP_COOLDOWN_SECONDS")
    economy_daily_base_reward: int = Field(default=120, validation_alias="ECONOMY_DAILY_BASE_REWARD")
    economy_daily_streak_cap: int = Field(default=7, validation_alias="ECONOMY_DAILY_STREAK_CAP")
    economy_lottery_ticket_price: int = Field(default=150, validation_alias="ECONOMY_LOTTERY_TICKET_PRICE")
    economy_lottery_paid_daily_limit: int = Field(default=10, validation_alias="ECONOMY_LOTTERY_PAID_DAILY_LIMIT")
    economy_transfer_daily_limit: int = Field(default=5000, validation_alias="ECONOMY_TRANSFER_DAILY_LIMIT")
    economy_transfer_tax_percent: int = Field(default=5, validation_alias="ECONOMY_TRANSFER_TAX_PERCENT")
    economy_market_fee_percent: int = Field(default=2, validation_alias="ECONOMY_MARKET_FEE_PERCENT")
    economy_negative_event_chance_percent: int = Field(default=22, validation_alias="ECONOMY_NEGATIVE_EVENT_CHANCE_PERCENT")
    economy_negative_event_loss_percent: int = Field(default=30, validation_alias="ECONOMY_NEGATIVE_EVENT_LOSS_PERCENT")
    cleanup_economy_commands: bool = Field(default=False, validation_alias="CLEANUP_ECONOMY_COMMANDS")

    web_enabled: bool = Field(default=True, validation_alias="WEB_ENABLED")
    web_host: str = Field(default="0.0.0.0", validation_alias="WEB_HOST")
    web_port: int = Field(default=8080, validation_alias="WEB_PORT")
    web_domain: str | None = Field(default=None, validation_alias="WEB_DOMAIN")
    web_base_url: str = Field(default="http://127.0.0.1:8080", validation_alias="WEB_BASE_URL")
    web_auth_secret: str | None = Field(default=None, validation_alias="WEB_AUTH_SECRET")
    web_login_code_ttl_minutes: int = Field(default=5, validation_alias="WEB_LOGIN_CODE_TTL_MINUTES")
    web_session_ttl_hours: int = Field(default=168, validation_alias="WEB_SESSION_TTL_HOURS")
    web_session_cookie_name: str = Field(default="selara_session", validation_alias="WEB_SESSION_COOKIE_NAME")
    web_session_cookie_secure: bool = Field(default=False, validation_alias="WEB_SESSION_COOKIE_SECURE")
    web_login_attempt_limit: int = Field(default=8, validation_alias="WEB_LOGIN_ATTEMPT_LIMIT")
    web_login_attempt_window_minutes: int = Field(default=5, validation_alias="WEB_LOGIN_ATTEMPT_WINDOW_MINUTES")

    @property
    def supported_chat_types(self) -> set[str]:
        return {"private", "group", "supergroup"}

    @property
    def resolved_web_auth_secret(self) -> str:
        value = (self.web_auth_secret or "").strip()
        if value:
            return value
        return self.bot_token

    @property
    def resolved_web_base_url(self) -> str:
        domain = (self.web_domain or "").strip()
        if domain:
            candidate = domain if "://" in domain else f"https://{domain}"
            return normalize_base_url(candidate)
        return normalize_base_url(self.web_base_url)

    @property
    def resolved_achievements_catalog_path(self) -> Path:
        return Path(self.achievements_catalog_path).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
