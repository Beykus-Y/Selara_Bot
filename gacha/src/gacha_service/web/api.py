from __future__ import annotations

from datetime import datetime
from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gacha_service.application.catalog import get_banner_config
from gacha_service.application.service import GachaService
from gacha_service.config import settings
from gacha_service.domain.models import CardRarity, RARITY_LABELS, resolve_rank
from gacha_service.infrastructure.backup import BackupError, cleanup_backup_artifact, create_database_backup
from gacha_service.infrastructure.db import session_dependency
from gacha_service.infrastructure.repository import GachaRepository


class PullRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    username: str | None = Field(default=None, max_length=64)
    banner: str = Field(default=settings.default_banner, min_length=1, max_length=32)


class CardPayload(BaseModel):
    code: str
    name: str
    rarity: str
    rarity_label: str
    points: int
    primogems: int
    image_url: str


class PlayerPayload(BaseModel):
    user_id: int
    adventure_rank: int
    adventure_xp: int
    xp_into_rank: int
    xp_for_next_rank: int
    total_points: int
    total_primogems: int


class PullResponse(BaseModel):
    status: str
    message: str
    card: CardPayload | None
    player: PlayerPayload
    cooldown_until: datetime
    is_new: bool
    copies_owned: int
    adventure_xp_gained: int


class HistoryEntryPayload(BaseModel):
    pulled_at: datetime
    card_name: str
    rarity: str
    rarity_label: str
    points: int
    primogems: int
    adventure_xp_gained: int
    image_url: str


class ProfileResponse(BaseModel):
    status: str
    banner: str
    message: str
    player: PlayerPayload
    unique_cards: int
    total_copies: int
    recent_pulls: list[HistoryEntryPayload]


class HistoryResponse(BaseModel):
    status: str
    banner: str
    user_id: int
    entries: list[HistoryEntryPayload]


class CooldownResetRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    banner: str = Field(default=settings.default_banner, min_length=1, max_length=32)


class CooldownResetResponse(BaseModel):
    status: str
    banner: str
    user_id: int
    message: str


class CollectionCardPayload(BaseModel):
    code: str
    name: str
    rarity: str
    rarity_label: str
    copies_owned: int
    image_url: str


class CollectionResponse(BaseModel):
    status: str
    banner: str
    user_id: int
    cards: list[CollectionCardPayload]
    total_unique: int
    total_copies: int


def _to_player_payload(*, player, user_id: int) -> PlayerPayload:
    rank, xp_into_rank, xp_for_next_rank = resolve_rank(player.adventure_xp)
    return PlayerPayload(
        user_id=user_id,
        adventure_rank=rank,
        adventure_xp=player.adventure_xp,
        xp_into_rank=xp_into_rank,
        xp_for_next_rank=xp_for_next_rank,
        total_points=player.total_points,
        total_primogems=player.total_primogems,
    )


def _to_history_payload(entry) -> HistoryEntryPayload:
    rarity = CardRarity(entry.rarity)
    return HistoryEntryPayload(
        pulled_at=entry.pulled_at,
        card_name=entry.character_name,
        rarity=rarity.value,
        rarity_label=RARITY_LABELS[rarity],
        points=entry.points,
        primogems=entry.primogems,
        adventure_xp_gained=entry.adventure_xp,
        image_url=entry.image_url,
    )


def _resolve_public_image_url(request: Request, image_url: str) -> str:
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/{image_url.lstrip('/')}"


def _render_profile_message(
    *,
    banner: str,
    banner_title: str,
    player_payload: PlayerPayload,
    unique_cards: int,
    total_copies: int,
    recent_pulls: list[HistoryEntryPayload],
) -> str:
    if banner == "hsr":
        rank_label = "Уровень освоения"
        currency_label = "Звездный нефрит"
        xp_suffix = "опыта освоения"
    else:
        rank_label = "Ранг приключений"
        currency_label = "Примогемы"
        xp_suffix = "XP"
    lines = [
        f"📒 Статистика гачи: {banner_title}",
        "",
        f"🧭 {rank_label}: {player_payload.adventure_rank} ({player_payload.xp_into_rank}/{player_payload.xp_for_next_rank})",
        f"🌟 Очки: {player_payload.total_points}",
        f"💠 {currency_label}: {player_payload.total_primogems}",
        f"🗂 Уникальных карт: {unique_cards}",
        f"📦 Всего копий: {total_copies}",
        "",
        "🕘 Последние крутки:",
    ]
    if not recent_pulls:
        lines.append("Пока пусто.")
    else:
        for entry in recent_pulls:
            lines.append(
                f"{entry.rarity_label} {entry.card_name} | +{entry.adventure_xp_gained} {xp_suffix} | {entry.pulled_at:%Y-%m-%d %H:%M}"
            )
    return "\n".join(lines)


router = APIRouter(prefix="/v1/gacha", tags=["gacha"])


def _require_admin_token(x_gacha_admin_token: str | None) -> None:
    expected_token = settings.admin_token.strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="Admin token is not configured on gacha server.")
    if not compare_digest(x_gacha_admin_token or "", expected_token):
        raise HTTPException(status_code=403, detail="Invalid admin token.")


def build_router(session_factory):
    async def get_session() -> AsyncSession:
        async for session in session_dependency(session_factory):
            yield session

    @router.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/pull", response_model=PullResponse)
    async def pull(
        payload: PullRequest,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> PullResponse:
        repo = GachaRepository(session)
        service = GachaService(repo)
        try:
            result = await service.pull(
                user_id=payload.user_id,
                username=payload.username,
                banner=payload.banner,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        card_payload = None
        if result.card is not None:
            card_payload = CardPayload(
                code=result.card.code,
                name=result.card.name,
                rarity=result.card.rarity.value,
                rarity_label=RARITY_LABELS[result.card.rarity],
                points=result.card.points,
                primogems=result.card.primogems,
                image_url=_resolve_public_image_url(request, result.card.image_url),
            )
        return PullResponse(
            status=result.status,
            message=result.message,
            card=card_payload,
            player=_to_player_payload(player=result.player, user_id=result.player.user_id),
            cooldown_until=result.cooldown_until,
            is_new=result.is_new,
            copies_owned=result.copies_owned,
            adventure_xp_gained=result.adventure_xp_gained,
        )

    @router.get("/users/{user_id}/profile", response_model=ProfileResponse)
    async def profile(
        user_id: int,
        banner: str = settings.default_banner,
        limit: int = 5,
        request: Request = None,
        session: AsyncSession = Depends(get_session),
    ) -> ProfileResponse:
        repo = GachaRepository(session)
        try:
            banner_config = get_banner_config(banner)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        player = await repo.get_or_create_player(user_id=user_id, username=None)
        unique_cards, total_copies = await repo.get_collection_stats(user_id=user_id, banner=banner)
        recent_pulls = await repo.get_recent_pulls_by_banner(user_id=user_id, banner=banner, limit=max(1, min(limit, 10)))
        player_payload = _to_player_payload(player=player, user_id=user_id)
        history_payload = [
            _to_history_payload(entry).model_copy(
                update={"image_url": _resolve_public_image_url(request, entry.image_url)}
            )
            for entry in recent_pulls
        ]
        return ProfileResponse(
            status="ok",
            banner=banner,
            message=_render_profile_message(
                banner=banner,
                banner_title=banner_config.title,
                player_payload=player_payload,
                unique_cards=unique_cards,
                total_copies=total_copies,
                recent_pulls=history_payload,
            ),
            player=player_payload,
            unique_cards=unique_cards,
            total_copies=total_copies,
            recent_pulls=history_payload,
        )

    @router.get("/users/{user_id}/history", response_model=HistoryResponse)
    async def history(
        user_id: int,
        banner: str = settings.default_banner,
        limit: int = 10,
        request: Request = None,
        session: AsyncSession = Depends(get_session),
    ) -> HistoryResponse:
        repo = GachaRepository(session)
        try:
            get_banner_config(banner)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        recent_pulls = await repo.get_recent_pulls_by_banner(user_id=user_id, banner=banner, limit=max(1, min(limit, 20)))
        return HistoryResponse(
            status="ok",
            banner=banner,
            user_id=user_id,
            entries=[
                _to_history_payload(entry).model_copy(
                    update={"image_url": _resolve_public_image_url(request, entry.image_url)}
                )
                for entry in recent_pulls
            ],
        )

    @router.get("/users/{user_id}/collection", response_model=CollectionResponse)
    async def collection(
        user_id: int,
        banner: str = settings.default_banner,
        request: Request = None,
        session: AsyncSession = Depends(get_session),
    ) -> CollectionResponse:
        repo = GachaRepository(session)
        try:
            banner_config = get_banner_config(banner)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        cards_collection = await repo.get_user_collection(user_id=user_id, banner=banner)
        card_info_by_code = {card.code: card for card in banner_config.cards}

        payload_cards = []
        total_copies = 0
        for card in cards_collection:
            if card.character_code in card_info_by_code:
                card_info = card_info_by_code[card.character_code]
                payload_cards.append(
                    CollectionCardPayload(
                        code=card.character_code,
                        name=card_info.name,
                        rarity=card_info.rarity.value,
                        rarity_label=RARITY_LABELS[card_info.rarity],
                        copies_owned=card.copies_owned,
                        image_url=_resolve_public_image_url(request, card_info.image_url),
                    )
                )
                total_copies += card.copies_owned

        return CollectionResponse(
            status="ok",
            banner=banner,
            user_id=user_id,
            cards=payload_cards,
            total_unique=len(payload_cards),
            total_copies=total_copies,
        )

    @router.post("/admin/cooldowns/reset", response_model=CooldownResetResponse)
    async def reset_cooldown(
        payload: CooldownResetRequest,
        x_gacha_admin_token: str | None = Header(default=None, alias="X-Gacha-Admin-Token"),
        session: AsyncSession = Depends(get_session),
    ) -> CooldownResetResponse:
        _require_admin_token(x_gacha_admin_token)

        repo = GachaRepository(session)
        try:
            banner_config = get_banner_config(payload.banner)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        reset = await repo.reset_banner_cooldown(user_id=payload.user_id, banner=payload.banner)
        if reset:
            message = f"Кулдаун баннера {banner_config.title} сброшен для пользователя {payload.user_id}."
        else:
            message = f"Для пользователя {payload.user_id} нет активного кулдауна на баннере {banner_config.title}."

        return CooldownResetResponse(
            status="ok",
            banner=payload.banner,
            user_id=payload.user_id,
            message=message,
        )

    @router.post("/admin/backup")
    async def backup_database(
        x_gacha_admin_token: str | None = Header(default=None, alias="X-Gacha-Admin-Token"),
    ) -> FileResponse:
        _require_admin_token(x_gacha_admin_token)

        try:
            artifact = await create_database_backup(settings=settings)
        except BackupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        return FileResponse(
            artifact.path,
            filename=artifact.filename,
            media_type=artifact.media_type,
            background=BackgroundTask(cleanup_backup_artifact, artifact),
            headers={
                "Cache-Control": "no-store",
                "X-Gacha-Backup-Format": artifact.path.suffix.lstrip("."),
            },
        )

    return router
