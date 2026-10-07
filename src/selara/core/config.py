from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
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
    error_alert_chat_id: int | None = Field(default=None, validation_alias="ERROR_ALERT_CHAT_ID")
    db_pool_size: int = Field(default=10, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, validation_alias="DB_MAX_OVERFLOW")
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    game_state_ttl_hours: int = Field(default=24, validation_alias="GAME_STATE_TTL_HOURS")
    activity_batch_flush_seconds: int = Field(default=5, validation_alias="ACTIVITY_BATCH_FLUSH_SECONDS")
    activity_batch_max_events: int = Field(default=1000, validation_alias="ACTIVITY_BATCH_MAX_EVENTS")
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
    cleanup_leave_service_messages: bool = Field(default=False, validation_alias="CLEANUP_LEAVE_SERVICE_MESSAGES")
    entry_captcha_enabled: bool = Field(default=False, validation_alias="ENTRY_CAPTCHA_ENABLED")
    entry_captcha_timeout_seconds: int = Field(default=180, validation_alias="ENTRY_CAPTCHA_TIMEOUT_SECONDS")
    entry_captcha_kick_on_fail: bool = Field(default=True, validation_alias="ENTRY_CAPTCHA_KICK_ON_FAIL")
    custom_rp_enabled: bool = Field(default=True, validation_alias="CUSTOM_RP_ENABLED")
    family_tree_enabled: bool = Field(default=True, validation_alias="FAMILY_TREE_ENABLED")
    persona_enabled: bool = Field(default=True, validation_alias="PERSONA_ENABLED")
    persona_display_mode: str = Field(default="image_name", validation_alias="PERSONA_DISPLAY_MODE")
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
    gacha_base_url: str = Field(default="", validation_alias="GACHA_BASE_URL")
    gacha_genshin_base_url: str = Field(default="", validation_alias="GACHA_GENSHIN_BASE_URL")
    gacha_hsr_base_url: str = Field(default="", validation_alias="GACHA_HSR_BASE_URL")
    gacha_timeout_seconds: float = Field(default=10.0, validation_alias="GACHA_TIMEOUT_SECONDS")
    gacha_admin_user_id: int | None = Field(default=None, validation_alias="GACHA_ADMIN_USER_ID")
    gacha_admin_token: str = Field(default="", validation_alias="GACHA_ADMIN_TOKEN")
    gacha_service_token: str = Field(default="", validation_alias="GACHA_SERVICE_TOKEN")
    gacha_reel_cache_dir: str = Field(default="var/gacha_reel_cache", validation_alias="GACHA_REEL_CACHE_DIR")
    backup_timeout_seconds: float = Field(default=300.0, validation_alias="BACKUP_TIMEOUT_SECONDS")
    backup_pg_dump_path: str = Field(default="pg_dump", validation_alias="BACKUP_PG_DUMP_PATH")
    backup_pg_restore_path: str = Field(default="pg_restore", validation_alias="BACKUP_PG_RESTORE_PATH")
    # Optional disposable PostgreSQL database for full backup restore drills;
    # when unset, only the cheap pg_restore --list archive check runs.
    backup_restore_database_url: str | None = Field(
        default=None, validation_alias="BACKUP_RESTORE_DATABASE_URL"
    )
    web_auth_secret: str | None = Field(default=None, validation_alias="WEB_AUTH_SECRET")
    web_login_code_ttl_minutes: int = Field(default=5, validation_alias="WEB_LOGIN_CODE_TTL_MINUTES")
    web_session_ttl_hours: int = Field(default=168, validation_alias="WEB_SESSION_TTL_HOURS")
    web_session_cookie_name: str = Field(default="selara_session", validation_alias="WEB_SESSION_COOKIE_NAME")
    web_session_cookie_secure: bool = Field(default=False, validation_alias="WEB_SESSION_COOKIE_SECURE")
    web_login_attempt_limit: int = Field(default=8, validation_alias="WEB_LOGIN_ATTEMPT_LIMIT")
    web_login_attempt_window_minutes: int = Field(default=5, validation_alias="WEB_LOGIN_ATTEMPT_WINDOW_MINUTES")

    stt_enabled: bool = Field(default=False, validation_alias="STT_ENABLED")
    stt_api_key: str = Field(default="", validation_alias="STT_API_KEY")
    stt_base_url: str = Field(default="https://api.groq.com/openai/v1", validation_alias="STT_BASE_URL")
    stt_model: str = Field(default="whisper-large-v3", validation_alias="STT_MODEL")
    stt_language: str = Field(default="ru", validation_alias="STT_LANGUAGE")
    stt_timeout_seconds: float = Field(default=30.0, validation_alias="STT_TIMEOUT_SECONDS")
    # #3: voice.py has no permission gate at all (any chat member, any chat
    # type), so this per-(chat, user) cooldown is the only thing standing
    # between a careless/malicious user and unlimited paid Whisper calls.
    stt_cooldown_seconds: float = Field(default=8.0, validation_alias="STT_COOLDOWN_SECONDS")
    # Guardrails for the daily summary voice/video-note transcription queue (see
    # docs/DAILY_SUMMARY_TODO.md) -- separate from the instant-reply transcription
    # above, which has no such cap since it's one call per user request.
    daily_summary_stt_concurrency: int = Field(default=2, validation_alias="DAILY_SUMMARY_STT_CONCURRENCY")
    daily_summary_max_transcription_seconds_per_chat_per_day: int = Field(
        default=1800, validation_alias="DAILY_SUMMARY_MAX_TRANSCRIPTION_SECONDS_PER_CHAT_PER_DAY"
    )

    artifact_renderer_url: str = Field(default="http://artifact-renderer:8090", validation_alias="ARTIFACT_RENDERER_URL")

    llm_enabled: bool = Field(default=False, validation_alias="LLM_ENABLED")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", validation_alias="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")
    llm_summary_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_SUMMARY_MODEL")
    llm_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    # Whether the configured provider/summary_model actually supports native
    # response_format={"type": "json_schema", ...} structured output. Not every
    # OpenAI-compatible endpoint implements this the same way, so it's an explicit
    # opt-in rather than guessed from the base_url/model name -- see
    # LlmClient.chat_structured (used by the daily summary pipeline's segment/merge
    # stages, docs/DAILY_SUMMARY_TODO.md).
    llm_supports_structured_output: bool = Field(default=False, validation_alias="LLM_SUPPORTS_STRUCTURED_OUTPUT")
    # Ask the provider for the real cost of each request (OpenRouter ``usage: {include: true}``).
    # ``None`` detects it from the base URL; the real cost is what Personal AIL billing settles against.
    llm_include_usage_cost: bool | None = Field(default=None, validation_alias="LLM_INCLUDE_USAGE_COST")
    # Optional OpenRouter ``provider`` preferences as a JSON object, e.g. {"max_price": {"prompt": 0.5}}.
    # Empty keeps provider routing untouched.
    llm_provider_preferences_json: str = Field(default="", validation_alias="LLM_PROVIDER_PREFERENCES_JSON")
    # #3: the `?`/`??` assistant is gated on moderate_users, but nothing
    # stops the same admin repeating it immediately -- a single invocation
    # can already fan out to ~10 billed calls (up to 8 tool rounds + DM
    # summary + compression).
    llm_cooldown_seconds: float = Field(default=5.0, validation_alias="LLM_COOLDOWN_SECONDS")

    # Web search tools (web_search / fetch_page) for the ?/?? assistant. The
    # default "auto" uses SearXNG (compose) then DuckDuckGo, no key needed;
    # web_search_api_key is for tavily/brave, web_search_base_url is the
    # DuckDuckGo gateway only, web_search_searxng_url is the SearXNG address.
    web_search_enabled: bool = Field(default=True, validation_alias="WEB_SEARCH_ENABLED")
    web_search_provider: str = Field(default="auto", validation_alias="WEB_SEARCH_PROVIDER")
    web_search_searxng_url: str = Field(default="http://searxng:8080", validation_alias="WEB_SEARCH_SEARXNG_URL")
    web_search_api_key: str = Field(default="", validation_alias="WEB_SEARCH_API_KEY")
    web_search_base_url: str = Field(default="", validation_alias="WEB_SEARCH_BASE_URL")
    web_search_timeout_seconds: float = Field(
        default=15.0, gt=0, validation_alias="WEB_SEARCH_TIMEOUT_SECONDS"
    )
    web_search_max_results: int = Field(default=5, ge=1, le=10, validation_alias="WEB_SEARCH_MAX_RESULTS")
    web_search_max_page_chars: int = Field(default=8000, ge=1, validation_alias="WEB_SEARCH_MAX_PAGE_CHARS")
    web_search_max_calls_per_invocation: int = Field(
        default=4, ge=0, validation_alias="WEB_SEARCH_MAX_CALLS_PER_INVOCATION"
    )

    admin_password: str | None = Field(default=None, validation_alias="ADMIN_PASSWORD")
    admin_user_id: int | None = Field(default=None, validation_alias="ADMIN_USER_ID")
    # Checkout stays disabled until the owner selects an explicit Stars price.
    selara_ai_price_stars: int | None = Field(default=None, gt=0, validation_alias="SELARA_AI_PRICE_STARS")
    # Selara Personal (a per-user subscription) stays hidden until its own price is set.
    selara_personal_price_stars: int | None = Field(
        default=None, gt=0, le=10_000, validation_alias="SELARA_PERSONAL_PRICE_STARS"
    )
    selara_personal_duration_days: int = Field(
        default=30, gt=0, le=365, validation_alias="SELARA_PERSONAL_DURATION_DAYS"
    )
    # Personal pool limits: one request = one unit. Fixed until a deliberate switch to AI Limits.
    personal_free_daily_limit: int = Field(default=5, gt=0, le=10_000, validation_alias="PERSONAL_FREE_DAILY_LIMIT")
    personal_paid_daily_limit: int = Field(default=150, gt=0, le=10_000, validation_alias="PERSONAL_PAID_DAILY_LIMIT")
    # Personal memory: fact limits per tier (technical guard against prompt bloat) and optional auto-extraction.
    # Extraction is off by default and only runs for Selara Personal users who also switched it on for themselves.
    # AIL billing: "actual" settles each Personal request at its real cost / PERSONAL_AIL_USD_VALUE;
    # "fixed" keeps charging the model profile multiplier.
    personal_ail_billing: Literal["actual", "fixed"] = Field(default="actual", validation_alias="PERSONAL_AIL_BILLING")
    personal_ail_usd_value: Decimal = Field(
        default=Decimal("0.0005"), gt=0, le=Decimal("100"), validation_alias="PERSONAL_AIL_USD_VALUE"
    )
    personal_memory_free_limit: int = Field(default=20, gt=0, le=1000, validation_alias="PERSONAL_MEMORY_FREE_LIMIT")
    personal_memory_paid_limit: int = Field(default=200, gt=0, le=1000, validation_alias="PERSONAL_MEMORY_PAID_LIMIT")
    personal_memory_auto_extract: bool = Field(default=False, validation_alias="PERSONAL_MEMORY_AUTO_EXTRACT")
    personal_memory_extract_every: int = Field(default=10, ge=2, le=40, validation_alias="PERSONAL_MEMORY_EXTRACT_EVERY")
    # AI pet talk, paid by the owner's Selara Personal: total per day, and the share other people may use.
    pet_talk_daily_limit: int = Field(default=60, gt=0, le=10_000, validation_alias="PET_TALK_DAILY_LIMIT")
    pet_talk_guests_daily_limit: int = Field(default=20, ge=0, le=10_000, validation_alias="PET_TALK_GUESTS_DAILY_LIMIT")
    pet_talk_guest_daily_limit: int = Field(default=5, ge=0, le=10_000, validation_alias="PET_TALK_GUEST_DAILY_LIMIT")
    # Spontaneous pet events: per pet per day, minimum gap per chat, quiet hours in BOT_TIMEZONE,
    # how often an active chat is considered and the chance an eligible check produces an event.
    pet_event_daily_limit: int = Field(default=6, ge=0, le=100, validation_alias="PET_EVENT_DAILY_LIMIT")
    pet_event_chat_interval_minutes: int = Field(
        default=120, ge=1, le=7 * 24 * 60, validation_alias="PET_EVENT_CHAT_INTERVAL_MINUTES"
    )
    pet_event_quiet_start_hour: int = Field(default=23, ge=0, le=23, validation_alias="PET_EVENT_QUIET_START_HOUR")
    pet_event_quiet_end_hour: int = Field(default=8, ge=0, le=23, validation_alias="PET_EVENT_QUIET_END_HOUR")
    pet_event_check_seconds: int = Field(default=300, ge=10, le=86_400, validation_alias="PET_EVENT_CHECK_SECONDS")
    pet_event_chance: float = Field(default=0.3, ge=0.0, le=1.0, validation_alias="PET_EVENT_CHANCE")
    # Member mode in groups («Селя, ...»): free for every chat, raised by Selara AI. Per day, per chat and per member.
    group_member_free_daily_limit: int = Field(default=30, gt=0, le=10_000, validation_alias="GROUP_MEMBER_FREE_DAILY_LIMIT")
    group_member_free_per_user_daily_limit: int = Field(
        default=5, gt=0, le=10_000, validation_alias="GROUP_MEMBER_FREE_PER_USER_DAILY_LIMIT"
    )
    group_member_paid_daily_limit: int = Field(default=100, gt=0, le=10_000, validation_alias="GROUP_MEMBER_PAID_DAILY_LIMIT")
    group_member_paid_per_user_daily_limit: int = Field(
        default=30, gt=0, le=10_000, validation_alias="GROUP_MEMBER_PAID_PER_USER_DAILY_LIMIT"
    )
    # Model turns (tool rounds incl. the last, tool-free answer round) for «?»/«??» and the group nickname.
    group_tool_rounds_free: int = Field(default=4, ge=1, le=20, validation_alias="GROUP_TOOL_ROUNDS_FREE")
    group_tool_rounds_paid: int = Field(default=8, ge=1, le=20, validation_alias="GROUP_TOOL_ROUNDS_PAID")
    llm_admin_max_tokens: int = Field(default=800, ge=64, le=8_000, validation_alias="LLM_ADMIN_MAX_TOKENS")
    group_member_max_tokens: int = Field(default=500, ge=64, le=8_000, validation_alias="GROUP_MEMBER_MAX_TOKENS")
    # OpenRouter `provider` object for group features (?, nickname), e.g. {"order": ["DeepInfra"], "allow_fallbacks": false}.
    llm_group_provider_preferences_json: str = Field(default="", validation_alias="LLM_GROUP_PROVIDER_PREFERENCES_JSON")
    admin_session_ttl_hours: int = Field(default=24, validation_alias="ADMIN_SESSION_TTL_HOURS")
    admin_session_cookie_name: str = Field(default="selara_admin_session", validation_alias="ADMIN_SESSION_COOKIE_NAME")
    admin_session_cookie_secure: bool = Field(default=False, validation_alias="ADMIN_SESSION_COOKIE_SECURE")

    @model_validator(mode="after")
    def _check_personal_limits(self):
        if self.personal_free_daily_limit >= self.personal_paid_daily_limit:
            raise ValueError("PERSONAL_FREE_DAILY_LIMIT must be lower than PERSONAL_PAID_DAILY_LIMIT")
        if self.personal_memory_free_limit > self.personal_memory_paid_limit:
            raise ValueError("PERSONAL_MEMORY_FREE_LIMIT must not exceed PERSONAL_MEMORY_PAID_LIMIT")
        if not self.pet_talk_guest_daily_limit <= self.pet_talk_guests_daily_limit <= self.pet_talk_daily_limit:
            raise ValueError("Pet talk limits must satisfy guest <= all guests <= daily")
        if not (
            self.group_member_free_per_user_daily_limit <= self.group_member_free_daily_limit
            and self.group_member_paid_per_user_daily_limit <= self.group_member_paid_daily_limit
            and self.group_member_free_daily_limit < self.group_member_paid_daily_limit
            and self.group_member_free_per_user_daily_limit <= self.group_member_paid_per_user_daily_limit
        ):
            raise ValueError(
                "Group member limits must satisfy per member <= per chat and free < paid (per member: free <= paid)"
            )
        if self.group_tool_rounds_paid < self.group_tool_rounds_free:
            raise ValueError("GROUP_TOOL_ROUNDS_PAID must not be lower than GROUP_TOOL_ROUNDS_FREE")
        return self

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

    def resolve_gacha_base_url(self, banner: str) -> str | None:
        normalized_banner = (banner or "").strip().lower()
        raw_value = {
            "genshin": self.gacha_genshin_base_url,
            "hsr": self.gacha_hsr_base_url,
        }.get(normalized_banner, "")
        if not raw_value:
            raw_value = self.gacha_base_url
        raw_value = raw_value.strip()
        if not raw_value:
            return None
        candidate = raw_value if "://" in raw_value else f"http://{raw_value}"
        return normalize_base_url(candidate)

    @property
    def resolved_achievements_catalog_path(self) -> Path:
        return Path(self.achievements_catalog_path).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
