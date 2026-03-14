from __future__ import annotations

from selara.core.config import Settings
from selara.infrastructure.http.gacha_client import (
    GachaClientError,
    GachaCooldownResetResponse,
    GachaProfileResponse,
    GachaPullResponse,
    HttpGachaClient,
)


class GachaUseCaseError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _build_client(settings: Settings, *, banner: str) -> HttpGachaClient:
    base_url = settings.resolve_gacha_base_url(banner)
    if base_url is None:
        raise GachaUseCaseError(
            f"Для баннера {banner} не настроен gacha API. Укажите GACHA_BASE_URL или отдельный URL для баннера."
        )
    return HttpGachaClient(base_url=base_url, timeout_seconds=settings.gacha_timeout_seconds)


async def pull_card(
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    banner: str,
) -> GachaPullResponse:
    client = _build_client(settings, banner=banner)
    try:
        return await client.pull(user_id=user_id, username=username, banner=banner)
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


async def get_profile(settings: Settings, *, user_id: int, banner: str) -> GachaProfileResponse:
    client = _build_client(settings, banner=banner)
    try:
        return await client.get_profile(user_id=user_id, banner=banner)
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


async def reset_cooldown(settings: Settings, *, user_id: int, banner: str) -> GachaCooldownResetResponse:
    client = _build_client(settings, banner=banner)
    admin_token = settings.gacha_admin_token.strip()
    if not admin_token:
        raise GachaUseCaseError("Не настроен GACHA_ADMIN_TOKEN для admin-команд.")

    try:
        return await client.reset_cooldown(
            user_id=user_id,
            banner=banner,
            admin_token=admin_token,
        )
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc
