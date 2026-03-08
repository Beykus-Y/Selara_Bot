from __future__ import annotations

import json
from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.domain.economy_entities import EconomyAccount, EconomyScope, FarmState


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_scope_or_error(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
) -> tuple[EconomyScope | None, str | None]:
    return await repo.resolve_scope(mode=economy_mode, chat_id=chat_id, user_id=user_id)


async def get_account_or_error(
    repo: EconomyRepository,
    *,
    scope: EconomyScope,
    user_id: int,
) -> tuple[EconomyAccount, FarmState]:
    return await repo.get_or_create_account(scope=scope, user_id=user_id)


def scope_label(scope: EconomyScope) -> str:
    if scope.scope_type == "global":
        return "global"
    return f"chat:{scope.chat_id}"


def to_meta_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)
