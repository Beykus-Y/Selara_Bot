"""Mini App API of the gacha collection screen.

Per-user gacha reads (profile, collection) are token-gated on the gacha service, and the Mini App runs
in a browser that must never hold ``GACHA_SERVICE_TOKEN``. The page therefore calls this router, which
resolves the Telegram user id from the Mini App session, never from the query string, and lets the
server call the gacha service with the service token. The success body is the gacha service payload
unchanged, so the frontend keeps consuming the same fields; failures are mapped to
``{ok: false, message}`` instead of leaking transport internals.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.use_cases.gacha import (
    GachaUseCaseError,
    get_collection as get_collection_use_case,
    get_profile as get_profile_use_case,
)
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot

logger = logging.getLogger(__name__)

UserLoader = Callable[[AsyncSession, Request], Awaitable[UserSnapshot | None]]
GachaLoader = Callable[..., Awaitable[Any]]

# The gacha service ships exactly these banners; anything else is rejected before a network call.
SUPPORTED_BANNERS = ("genshin", "hsr")
DEFAULT_BANNER = "genshin"
DEFAULT_PROFILE_LIMIT = 5
_MIN_PROFILE_LIMIT = 1
_MAX_PROFILE_LIMIT = 10


class _ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(content={"ok": False, "message": message}, status_code=status_code)


def _json_errors(handler):
    """Turn ``_ApiError`` into the ``{ok: false, message}`` shape the Mini App client reads."""

    @wraps(handler)
    async def wrapper(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except _ApiError as exc:
            return _error_response(exc.status_code, exc.message)

    return wrapper


def _resolve_banner(banner: str) -> str:
    normalized = (banner or "").strip().lower() or DEFAULT_BANNER
    if normalized not in SUPPORTED_BANNERS:
        raise _ApiError(422, "Такого баннера нет.")
    return normalized


def _clamp_profile_limit(limit: int) -> int:
    # Same window as the gacha service profile endpoint: max(1, min(limit, 10)).
    return max(_MIN_PROFILE_LIMIT, min(limit, _MAX_PROFILE_LIMIT))


def build_miniapp_gacha_router(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    load_user: UserLoader,
    profile_loader: GachaLoader | None = None,
    collection_loader: GachaLoader | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/miniapp/gacha", tags=["miniapp-gacha"])
    load_profile = profile_loader or get_profile_use_case
    load_collection = collection_loader or get_collection_use_case

    async def _session_user(request: Request) -> UserSnapshot:
        async with session_factory() as session:
            user = await load_user(session, request)
            await session.commit()
        if user is None:
            raise _ApiError(401, "Mini App сессия истекла.")
        return user

    async def _forward(call: GachaLoader, **kwargs) -> Any:
        try:
            return await call(settings, **kwargs)
        except GachaUseCaseError as exc:
            logger.warning(
                "miniapp gacha: upstream read failed user_id=%s banner=%s error=%s",
                kwargs.get("user_id"), kwargs.get("banner"), exc.message,
            )
            status_code = 503 if exc.is_operational else 502
            raise _ApiError(status_code, "Гача-сервис сейчас недоступен. Попробуйте позже.") from exc

    @router.get("/profile")
    @_json_errors
    async def profile(request: Request, banner: str = "", limit: int = DEFAULT_PROFILE_LIMIT):
        # The user id comes from the session only: a client-supplied user_id is never even read.
        user = await _session_user(request)
        payload = await _forward(
            load_profile,
            user_id=user.telegram_user_id,
            banner=_resolve_banner(banner),
            limit=_clamp_profile_limit(limit),
        )
        return JSONResponse(content=payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    @router.get("/collection")
    @_json_errors
    async def collection(request: Request, banner: str = ""):
        user = await _session_user(request)
        payload = await _forward(
            load_collection,
            user_id=user.telegram_user_id,
            banner=_resolve_banner(banner),
        )
        return JSONResponse(content=payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    return router
