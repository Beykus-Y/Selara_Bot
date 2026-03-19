from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict


class GachaCardPayload(BaseModel):
    code: str
    name: str
    rarity: str
    rarity_label: str
    points: int
    primogems: int
    image_url: str


class GachaSellOfferPayload(BaseModel):
    sale_price: int


class GachaPlayerPayload(BaseModel):
    user_id: int
    adventure_rank: int
    adventure_xp: int
    xp_into_rank: int
    xp_for_next_rank: int
    total_points: int
    total_primogems: int


class GachaPullResponse(BaseModel):
    status: str
    message: str
    card: GachaCardPayload | None
    player: GachaPlayerPayload
    is_new: bool = False
    copies_owned: int = 0
    adventure_xp_gained: int = 0
    pull_id: int | None = None
    sell_offer: GachaSellOfferPayload | None = None


class GachaHistoryEntryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pulled_at: str
    card_name: str
    rarity: str
    rarity_label: str
    points: int
    primogems: int
    adventure_xp_gained: int
    image_url: str


class GachaProfileResponse(BaseModel):
    status: str
    banner: str
    message: str
    player: GachaPlayerPayload
    unique_cards: int
    total_copies: int
    recent_pulls: list[GachaHistoryEntryPayload]


class GachaCooldownResetResponse(BaseModel):
    status: str
    banner: str
    user_id: int
    message: str


class GachaSellPullResponse(BaseModel):
    status: str
    message: str
    pull_id: int
    banner: str
    sale_price: int
    sold_at: str
    player: GachaPlayerPayload


class GachaCurrencyGrantResponse(BaseModel):
    status: str
    message: str
    banner: str
    user_id: int
    amount: int
    player: GachaPlayerPayload


@dataclass(slots=True)
class GachaBackupFile:
    filename: str
    content: bytes


class GachaClientError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HttpGachaClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    async def pull(self, *, user_id: int, username: str | None, banner: str) -> GachaPullResponse:
        payload = await self._request(
            "POST",
            "/v1/gacha/pull",
            json={
                "user_id": user_id,
                "username": username,
                "banner": banner,
            },
        )
        return GachaPullResponse.model_validate(payload)

    async def get_profile(self, *, user_id: int, banner: str) -> GachaProfileResponse:
        payload = await self._request(
            "GET",
            f"/v1/gacha/users/{user_id}/profile",
            params={"banner": banner},
        )
        return GachaProfileResponse.model_validate(payload)

    async def purchase_pull(self, *, user_id: int, username: str | None, banner: str) -> GachaPullResponse:
        payload = await self._request(
            "POST",
            "/v1/gacha/pull/purchase",
            json={
                "user_id": user_id,
                "username": username,
                "banner": banner,
            },
        )
        return GachaPullResponse.model_validate(payload)

    async def sell_pull(self, *, user_id: int, pull_id: int) -> GachaSellPullResponse:
        payload = await self._request(
            "POST",
            f"/v1/gacha/pulls/{pull_id}/sell",
            json={"user_id": user_id},
        )
        return GachaSellPullResponse.model_validate(payload)

    async def reset_cooldown(
        self,
        *,
        user_id: int,
        banner: str,
        admin_token: str,
    ) -> GachaCooldownResetResponse:
        payload = await self._request(
            "POST",
            "/v1/gacha/admin/cooldowns/reset",
            headers={"X-Gacha-Admin-Token": admin_token},
            json={
                "user_id": user_id,
                "banner": banner,
            },
        )
        return GachaCooldownResetResponse.model_validate(payload)

    async def give_card(self, *, user_id: int, banner: str | None, code: str, admin_token: str) -> GachaPullResponse:
        payload = await self._request(
            "POST",
            "/v1/gacha/admin/give",
            headers={"X-Gacha-Admin-Token": admin_token},
            json={
                "user_id": user_id,
                "banner": banner,
                "code": code,
            },
        )
        return GachaPullResponse.model_validate(payload)

    async def grant_currency(
        self,
        *,
        user_id: int,
        username: str | None,
        banner: str,
        amount: int,
        admin_token: str,
    ) -> GachaCurrencyGrantResponse:
        payload = await self._request(
            "POST",
            "/v1/gacha/admin/currency/grant",
            headers={"X-Gacha-Admin-Token": admin_token},
            json={
                "user_id": user_id,
                "username": username,
                "banner": banner,
                "amount": amount,
            },
        )
        return GachaCurrencyGrantResponse.model_validate(payload)

    async def download_backup(self, *, admin_token: str) -> GachaBackupFile:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout_seconds) as client:
                response = await client.post(
                    "/v1/gacha/admin/backup",
                    headers={"X-Gacha-Admin-Token": admin_token},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GachaClientError("Гача-сервер не отдал backup вовремя.") from exc
        except httpx.HTTPStatusError as exc:
            raise GachaClientError(_extract_error_message(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise GachaClientError("Не удалось скачать backup с гача-сервера.") from exc

        return GachaBackupFile(
            filename=_extract_filename(response),
            content=response.content,
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout_seconds) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GachaClientError("Гача-сервер не ответил вовремя.") from exc
        except httpx.HTTPStatusError as exc:
            raise GachaClientError(_extract_error_message(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise GachaClientError("Не удалось связаться с гача-сервером.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GachaClientError("Гача-сервер вернул некорректный ответ.") from exc
        if not isinstance(payload, dict):
            raise GachaClientError("Гача-сервер вернул неожиданный формат ответа.")
        return payload


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    if response.status_code >= 500:
        return "Гача-сервер вернул ошибку."
    return f"Гача-сервер отклонил запрос: HTTP {response.status_code}."


def _extract_filename(response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        candidate = part.strip()
        if not candidate.startswith("filename="):
            continue
        value = candidate.split("=", 1)[1].strip().strip('"')
        if value:
            return value
    return "gacha_pg_dump.dump"
