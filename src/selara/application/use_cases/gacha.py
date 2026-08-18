from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import (
    get_account_or_error,
    resolve_scope_or_error,
    to_meta_json,
)
from selara.core.config import Settings
from selara.infrastructure.http.gacha_client import (
    GachaBannerCardsResponse,
    GachaCardPayload,
    GachaClientError,
    GachaCooldownResetResponse,
    GachaCurrencyGrantResponse,
    GachaPlayerPayload,
    GachaProfileResponse,
    GachaPullResponse,
    GachaSellPullResponse,
    HttpGachaClient,
)

logger = logging.getLogger(__name__)

GACHA_CURRENCY_PER_COIN_RATE = 10
GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT = 160


class GachaUseCaseError(RuntimeError):
    def __init__(self, message: str, *, is_timeout: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.is_timeout = is_timeout


@dataclass(slots=True, frozen=True)
class GachaCurrencyPurchaseResult:
    banner: str
    currency_amount: int
    coin_price: int
    new_coin_balance: int
    gacha_balance: int
    message: str


def _build_client(settings: Settings, *, banner: str) -> HttpGachaClient:
    base_url = settings.resolve_gacha_base_url(banner)
    if base_url is None:
        raise GachaUseCaseError(
            f"Для баннера {banner} не настроен gacha API. Укажите GACHA_BASE_URL или отдельный URL для баннера."
        )
    service_token = settings.gacha_service_token.strip()
    if not service_token:
        raise GachaUseCaseError("Не настроен GACHA_SERVICE_TOKEN для запросов к gacha API.")
    return HttpGachaClient(
        base_url=base_url,
        timeout_seconds=settings.gacha_timeout_seconds,
        service_token=service_token,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _recover_pull_after_timeout(
    client: HttpGachaClient, *, user_id: int, banner: str, requested_at: datetime
) -> GachaPullResponse | None:
    """Best-effort recovery for the "response lost after the server already
    wrote the pull" case: if the most recent history entry for this
    user+banner was created at or after the request started, treat it as the
    real (already-committed) result instead of reporting a failure. Never
    invents a result — only recognizes one that the server already recorded.
    """
    try:
        history = await client.get_history(user_id=user_id, banner=banner, limit=1)
    except GachaClientError:
        logger.warning(
            "Gacha pull recovery: history fetch failed after timeout user=%s banner=%s", user_id, banner
        )
        return None
    if not history.entries:
        logger.warning(
            "Gacha pull recovery: no history entries after timeout user=%s banner=%s", user_id, banner
        )
        return None

    entry = history.entries[0]
    try:
        pulled_at = datetime.fromisoformat(entry.pulled_at)
    except ValueError:
        return None
    if pulled_at < requested_at:
        logger.warning(
            "Gacha pull recovery: latest history entry predates request, treating as unrecovered "
            "user=%s banner=%s pull_id=%s",
            user_id, banner, entry.pull_id,
        )
        return None

    try:
        profile = await client.get_profile(user_id=user_id, banner=banner)
    except GachaClientError:
        logger.warning(
            "Gacha pull recovery: profile fetch failed after timeout user=%s banner=%s pull_id=%s",
            user_id, banner, entry.pull_id,
        )
        return None

    logger.info(
        "Gacha pull recovered after timeout: user=%s banner=%s pull_id=%s card=%s",
        user_id, banner, entry.pull_id, entry.card_name,
    )
    return GachaPullResponse(
        status="ok",
        message=(
            f"⚠️ Соединение с гача-сервером прервалось, но крутка уже была засчитана.\n\n"
            f"Вам выпало: {entry.card_name}\n"
            f"Редкость: {entry.rarity_label}\n"
            f"🌟 Очки: +{entry.points}\n"
            f"💠 Валюта: +{entry.primogems}"
        ),
        card=GachaCardPayload(
            code="",
            name=entry.card_name,
            rarity=entry.rarity,
            rarity_label=entry.rarity_label,
            points=entry.points,
            primogems=entry.primogems,
            image_url=entry.image_url,
        ),
        player=profile.player,
        is_new=False,
        copies_owned=0,
        adventure_xp_gained=entry.adventure_xp_gained,
        pull_id=entry.pull_id,
        sell_offer=None,
    )


async def pull_card(
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    banner: str,
) -> GachaPullResponse:
    client = _build_client(settings, banner=banner)
    requested_at = _utcnow()
    try:
        response = await client.pull(user_id=user_id, username=username, banner=banner)
        logger.info(
            "Gacha pull ok: user=%s banner=%s pull_id=%s is_new=%s",
            user_id, banner, response.pull_id, response.is_new,
        )
        return response
    except GachaClientError as exc:
        if exc.is_timeout:
            logger.warning(
                "Gacha pull timed out, attempting recovery: user=%s banner=%s", user_id, banner
            )
            recovered = await _recover_pull_after_timeout(
                client, user_id=user_id, banner=banner, requested_at=requested_at
            )
            if recovered is not None:
                return recovered
        logger.warning(
            "Gacha pull failed: user=%s banner=%s error=%s", user_id, banner, exc.message
        )
        raise GachaUseCaseError(exc.message) from exc


async def get_banner_cards(
    settings: Settings, *, banner: str, if_none_match: str | None = None
) -> tuple[GachaBannerCardsResponse | None, str | None]:
    client = _build_client(settings, banner=banner)
    try:
        return await client.get_banner_cards(banner=banner, if_none_match=if_none_match)
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


async def get_profile(settings: Settings, *, user_id: int, banner: str) -> GachaProfileResponse:
    client = _build_client(settings, banner=banner)
    try:
        return await client.get_profile(user_id=user_id, banner=banner)
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


async def purchase_pull(
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    banner: str,
) -> GachaPullResponse:
    client = _build_client(settings, banner=banner)
    requested_at = _utcnow()
    try:
        return await client.purchase_pull(user_id=user_id, username=username, banner=banner)
    except GachaClientError as exc:
        if exc.is_timeout:
            recovered = await _recover_pull_after_timeout(
                client, user_id=user_id, banner=banner, requested_at=requested_at
            )
            if recovered is not None:
                return recovered
        raise GachaUseCaseError(exc.message) from exc


async def sell_pull(settings: Settings, *, user_id: int, pull_id: int, banner: str) -> GachaSellPullResponse:
    client = _build_client(settings, banner=banner)
    try:
        return await client.sell_pull(user_id=user_id, pull_id=pull_id)
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


async def give_card(settings: Settings, *, user_id: int, banner: str | None, code: str) -> GachaPullResponse:
    client = _build_client(settings, banner=banner or "")
    admin_token = settings.gacha_admin_token.strip()
    if not admin_token:
        raise GachaUseCaseError("Не настроен GACHA_ADMIN_TOKEN для admin-команд.")

    try:
        return await client.give_card(user_id=user_id, banner=banner, code=code, admin_token=admin_token)
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


async def grant_currency(
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    banner: str,
    amount: int,
    idempotency_key: str | None = None,
) -> GachaCurrencyGrantResponse:
    client = _build_client(settings, banner=banner)
    admin_token = settings.gacha_admin_token.strip()
    if not admin_token:
        raise GachaUseCaseError("Не настроен GACHA_ADMIN_TOKEN для admin-команд.")

    try:
        return await client.grant_currency(
            user_id=user_id,
            username=username,
            banner=banner,
            amount=amount,
            admin_token=admin_token,
            idempotency_key=idempotency_key,
        )
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message, is_timeout=exc.is_timeout) from exc


def _gacha_currency_label(banner: str) -> str:
    if (banner or "").strip().lower() == "hsr":
        return "звездного нефрита"
    return "примогемов"


async def buy_currency_with_coins(
    settings: Settings,
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    username: str | None,
    banner: str,
    currency_amount: int = GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT,
) -> GachaCurrencyPurchaseResult:
    normalized_amount = int(currency_amount)
    if normalized_amount <= 0:
        raise GachaUseCaseError("Количество валюты должно быть больше нуля.")

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        raise GachaUseCaseError(error or "Не удалось определить режим экономики.")

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)
    coin_price = normalized_amount * GACHA_CURRENCY_PER_COIN_RATE
    if account.balance < coin_price:
        raise GachaUseCaseError(f"Недостаточно монет. Нужно {coin_price}, у вас {account.balance}.")

    try:
        new_coin_balance = await repo.add_balance(account_id=account.id, delta=-coin_price)
    except ValueError as exc:
        raise GachaUseCaseError(f"Недостаточно монет. Нужно {coin_price}, у вас {account.balance}.") from exc

    idempotency_key = str(uuid.uuid4())
    try:
        topup = await grant_currency(
            settings,
            user_id=user_id,
            username=username,
            banner=banner,
            amount=normalized_amount,
            idempotency_key=idempotency_key,
        )
    except GachaUseCaseError as exc:
        if not exc.is_timeout:
            await repo.add_balance(account_id=account.id, delta=coin_price)
            raise
        # Ambiguous timeout: the grant may have already been applied server-side.
        # A single retry with the same idempotency_key is safe — the server
        # dedupes on it instead of crediting the currency twice.
        try:
            topup = await grant_currency(
                settings,
                user_id=user_id,
                username=username,
                banner=banner,
                amount=normalized_amount,
                idempotency_key=idempotency_key,
            )
        except GachaUseCaseError as retry_exc:
            if not retry_exc.is_timeout:
                # Unambiguous failure (e.g. rejected request) — the grant
                # definitely did not apply, safe to refund.
                await repo.add_balance(account_id=account.id, delta=coin_price)
                raise
            # Second consecutive *ambiguous* timeout on the same
            # idempotency_key: we genuinely don't know whether the grant
            # landed. Refunding here would risk crediting currency for free
            # if it actually did land server-side, so we deliberately do
            # NOT touch the coin balance and instead surface a distinct,
            # loudly-logged error for manual reconciliation.
            logger.error(
                "Gacha currency purchase: double ambiguous timeout, balance left as-is pending "
                "manual reconciliation. user=%s banner=%s amount=%s coin_price=%s idempotency_key=%s",
                user_id, banner, normalized_amount, coin_price, idempotency_key,
            )
            raise GachaUseCaseError(
                "Не удалось подтвердить обмен валюты из-за повторного таймаута. "
                "Монеты не возвращены автоматически — свяжитесь с администратором для проверки.",
                is_timeout=True,
            ) from retry_exc

    await repo.add_ledger(
        account_id=account.id,
        direction="out",
        amount=coin_price,
        reason="gacha_currency_purchase",
        meta_json=to_meta_json(
            {
                "banner": banner,
                "currency_amount": normalized_amount,
                "coin_price": coin_price,
            }
        ),
    )
    return GachaCurrencyPurchaseResult(
        banner=banner,
        currency_amount=normalized_amount,
        coin_price=coin_price,
        new_coin_balance=new_coin_balance,
        gacha_balance=topup.player.total_primogems,
        message=(
            f"Обмен: -{coin_price} монет, +{normalized_amount} {_gacha_currency_label(banner)}. "
            f"Баланс монет: {new_coin_balance}."
        ),
    )
