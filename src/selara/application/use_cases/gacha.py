from __future__ import annotations

from dataclasses import dataclass

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.core.config import Settings
from selara.infrastructure.http.gacha_client import (
    GachaClientError,
    GachaCooldownResetResponse,
    GachaCurrencyGrantResponse,
    GachaProfileResponse,
    GachaPullResponse,
    GachaSellPullResponse,
    HttpGachaClient,
)

GACHA_CURRENCY_PER_COIN_RATE = 10
GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT = 180


class GachaUseCaseError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


async def purchase_pull(
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    banner: str,
) -> GachaPullResponse:
    client = _build_client(settings, banner=banner)
    try:
        return await client.purchase_pull(user_id=user_id, username=username, banner=banner)
    except GachaClientError as exc:
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
        )
    except GachaClientError as exc:
        raise GachaUseCaseError(exc.message) from exc


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

    try:
        topup = await grant_currency(
            settings,
            user_id=user_id,
            username=username,
            banner=banner,
            amount=normalized_amount,
        )
    except GachaUseCaseError:
        await repo.add_balance(account_id=account.id, delta=coin_price)
        raise

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
