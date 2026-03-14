from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./gacha.db"
    default_banner: str = "genshin"

    model_config = SettingsConfigDict(env_prefix="GACHA_", env_file=".env", extra="ignore")


settings = Settings()
