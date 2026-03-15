from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_app_dir() -> str:
    env_value = os.getenv("GACHA_APP_DIR")
    if env_value:
        return env_value

    current_file = Path(__file__).resolve()
    candidates = [
        current_file.parents[2],
        Path("/app"),
    ]
    for candidate in candidates:
        if (candidate / "config" / "banners").exists():
            return str(candidate)
    return str(candidates[0])


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://gacha:gacha@127.0.0.1:5432/gacha"
    default_banner: str = "genshin"
    admin_token: str = ""
    pg_dump_path: str = "pg_dump"
    app_dir: str = _default_app_dir()

    model_config = SettingsConfigDict(env_prefix="GACHA_", env_file=".env", extra="ignore")

    @property
    def app_path(self) -> Path:
        return Path(self.app_dir).resolve()

    @property
    def images_dir(self) -> Path:
        return self.app_path / "images"

    @property
    def banners_dir(self) -> Path:
        return self.app_path / "config" / "banners"


settings = Settings()
