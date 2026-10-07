"""Owner-made subscription grants and revocations.

Every change runs in one transaction under the same advisory lock a Stars payment takes for that target, so a
grant and a payment arriving together are serialised and neither loses days. The journal row carries the
idempotency key: a replay returns the original outcome without a second effect.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.entitlement_grants import (
    PRODUCT_BY_SCOPE,
    SCOPES,
    grant_notice,
    revoke_notice,
    SOURCES,
    GrantError,
    validate_ahead,
    validate_days,
    validate_key,
    validate_reason,
    validate_target,
)
from selara.infrastructure.db.models import (
    ChatEntitlementModel,
    ChatModel,
    EntitlementGrantModel,
    SelaraAiPaymentModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.telegram_stars import (
    _advisory_xact_lock,
    entitlement_lock_key,
    user_entitlement_lock_key,
)

logger = logging.getLogger(__name__)

# (telegram chat id to write to, text) -> delivered
NoticeSender = Callable[[int, str], Awaitable[bool]]
RECENT_PAYMENT_WINDOW = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class GrantOutcome:
    grant_id: int
    action: str
    scope: str
    target_id: int
    status: str
    valid_until: datetime | None
    delta_seconds: int
    duplicate: bool = False
    paid_recently: bool = False


@dataclass(frozen=True, slots=True)
class TargetState:
    scope: str
    target_id: int
    title: str | None
    status: str | None
    valid_until: datetime | None
    active: bool
    paid_recently: bool
    granted: bool


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _lock_key(scope: str, target_id: int) -> int:
    product = PRODUCT_BY_SCOPE[scope]
    if scope == "chat":
        return entitlement_lock_key(chat_id=target_id, product_key=product)
    return user_entitlement_lock_key(user_id=target_id, product_key=product)


def _target_column(scope: str):
    return EntitlementGrantModel.target_chat_id if scope == "chat" else EntitlementGrantModel.target_user_id


def _outcome(row: EntitlementGrantModel, *, duplicate: bool, paid_recently: bool = False) -> GrantOutcome:
    target_id = int(row.target_chat_id if row.scope == "chat" else row.target_user_id)
    return GrantOutcome(
        grant_id=int(row.id),
        action=row.action,
        scope=row.scope,
        target_id=target_id,
        status=row.status_after,
        valid_until=_as_utc(row.valid_until_after) if row.valid_until_after is not None else None,
        delta_seconds=int(row.delta_seconds),
        duplicate=duplicate,
        paid_recently=paid_recently,
    )


# The journal timestamp comes from the database clock, the period start from the app clock.
_CLOCK_SLACK = timedelta(minutes=5)


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EntitlementGrantService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, admin_user_id: int | None = None) -> None:
        self._session_factory = session_factory
        self._admin_user_id = admin_user_id

    # ----- writes ------------------------------------------------------------------------------------

    async def grant(
        self,
        *,
        scope: str,
        target_id: int,
        days: int,
        reason: str,
        idempotency_key: str,
        actor_user_id: int,
        source: str,
        now: datetime | None = None,
    ) -> GrantOutcome:
        scope, target_id = validate_target(scope, target_id, admin_user_id=self._admin_user_id)
        days = validate_days(days)
        reason = validate_reason(reason)
        key = validate_key(idempotency_key)
        self._check_source(source)
        current = _as_utc(now or datetime.now(timezone.utc))
        delta = timedelta(days=days)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    if _is_postgres(session):
                        await _advisory_xact_lock(session, _lock_key(scope, target_id))
                    replay = await self._replay(
                        session, key, scope=scope, target_id=target_id, actions=("grant", "extend")
                    )
                    if replay is not None:
                        return replay
                    await self._require_known_target(session, scope, target_id)
                    entitlement = await self._entitlement_row(session, scope, target_id)
                    status_before = entitlement.status if entitlement is not None else None
                    until_before = _as_utc(entitlement.valid_until) if entitlement is not None else None
                    still_active = (
                        entitlement is not None and entitlement.status == "active" and until_before > current
                    )
                    base = until_before if still_active else current
                    new_until = validate_ahead(base_until=base, delta=delta, now=current)
                    if entitlement is None:
                        entitlement = self._new_row(scope, target_id, valid_from=current, valid_until=new_until)
                        session.add(entitlement)
                    else:
                        # Same rule as a payment: days are added after the remaining active time, otherwise
                        # the subscription restarts from now (this also reactivates a revoked one).
                        if not still_active:
                            entitlement.valid_from = current
                        entitlement.valid_until = new_until
                        entitlement.status = "active"
                        entitlement.updated_at = func.now()
                    paid_recently = await self._paid_recently(session, scope, target_id, current)
                    row = EntitlementGrantModel(
                        idempotency_key=key,
                        scope=scope,
                        target_chat_id=target_id if scope == "chat" else None,
                        target_user_id=target_id if scope == "user" else None,
                        product_key=PRODUCT_BY_SCOPE[scope],
                        action="extend" if still_active else "grant",
                        delta_seconds=int(delta.total_seconds()),
                        valid_until_before=until_before,
                        valid_until_after=new_until,
                        status_before=status_before,
                        status_after="active",
                        reason=reason,
                        actor_user_id=actor_user_id,
                        source=source,
                    )
                    session.add(row)
                    await session.flush()
                    return _outcome(row, duplicate=False, paid_recently=paid_recently)
        except IntegrityError as exc:
            raise GrantError("idempotency_conflict", "Этот ключ уже использован для другой операции.") from exc

    async def revoke(
        self,
        *,
        scope: str,
        target_id: int,
        mode: str,
        days: int | None,
        reason: str,
        idempotency_key: str,
        actor_user_id: int,
        source: str,
        now: datetime | None = None,
    ) -> GrantOutcome:
        scope, target_id = validate_target(scope, target_id)
        if mode not in ("cancel_all", "shorten"):
            raise GrantError("invalid_mode", "Режим: cancel_all (отключить полностью) или shorten (убрать дни).")
        if mode == "shorten":
            days = validate_days(days, label="Сколько дней убрать")
        reason = validate_reason(reason)
        key = validate_key(idempotency_key)
        self._check_source(source)
        current = _as_utc(now or datetime.now(timezone.utc))
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    if _is_postgres(session):
                        await _advisory_xact_lock(session, _lock_key(scope, target_id))
                    replay = await self._replay(
                        session, key, scope=scope, target_id=target_id, actions=("revoke", "shorten")
                    )
                    if replay is not None:
                        return replay
                    entitlement = await self._entitlement_row(session, scope, target_id)
                    if entitlement is None:
                        raise GrantError("no_entitlement", "У этой цели нет подписки.")
                    until_before = _as_utc(entitlement.valid_until)
                    if entitlement.status != "active" or until_before <= current:
                        raise GrantError("not_active", "Подписка уже не действует: отзывать нечего.")
                    status_before = entitlement.status
                    if mode == "cancel_all":
                        removed = until_before - current
                        entitlement.status = "revoked"
                        new_until = until_before
                        action = "revoke"
                    else:
                        # Only days the owner granted by hand and has not already taken back can be removed:
                        # paid time is never shortened here (use cancel_all to close a subscription).
                        removable = min(
                            await self._granted_days_left(
                                session, scope, target_id, since=_as_utc(entitlement.valid_from)
                            ),
                            until_before - current,
                        )
                        if removable <= timedelta(0):
                            raise GrantError(
                                "exceeds_granted",
                                "Выданных вручную дней, которые можно убрать, нет: оплаченный срок так не сокращается.",
                            )
                        proposed = until_before - min(timedelta(days=int(days)), removable)
                        removed = until_before - max(proposed, current)
                        if proposed <= current:
                            # Nothing left of the paid or granted time: closing it keeps the validity window valid.
                            entitlement.status = "revoked"
                            new_until = until_before
                        else:
                            entitlement.valid_until = proposed
                            new_until = proposed
                        action = "shorten"
                    entitlement.updated_at = func.now()
                    row = EntitlementGrantModel(
                        idempotency_key=key,
                        scope=scope,
                        target_chat_id=target_id if scope == "chat" else None,
                        target_user_id=target_id if scope == "user" else None,
                        product_key=PRODUCT_BY_SCOPE[scope],
                        action=action,
                        delta_seconds=max(0, int(removed.total_seconds())),
                        valid_until_before=until_before,
                        valid_until_after=new_until,
                        status_before=status_before,
                        status_after=entitlement.status,
                        reason=reason,
                        actor_user_id=actor_user_id,
                        source=source,
                    )
                    session.add(row)
                    await session.flush()
                    return _outcome(row, duplicate=False)
        except IntegrityError as exc:
            raise GrantError("idempotency_conflict", "Этот ключ уже использован для другой операции.") from exc

    async def grant_and_notify(
        self, *, notify: bool, send_notice: NoticeSender | None, timezone_name: str, **kwargs
    ) -> tuple[GrantOutcome, bool | None]:
        """Grant, then tell the recipient (best effort: a failed notice never undoes the grant)."""
        outcome = await self.grant(**kwargs)
        if outcome.duplicate or not notify or send_notice is None or outcome.valid_until is None:
            return outcome, None
        text = grant_notice(scope=outcome.scope, valid_until=outcome.valid_until, timezone_name=timezone_name)
        return outcome, await self._deliver(outcome, send_notice, text)

    async def revoke_and_notify(
        self, *, notify: bool, send_notice: NoticeSender | None, timezone_name: str, **kwargs
    ) -> tuple[GrantOutcome, bool | None]:
        outcome = await self.revoke(**kwargs)
        if outcome.duplicate or not notify or send_notice is None:
            return outcome, None
        text = revoke_notice(
            scope=outcome.scope,
            mode="shorten" if outcome.action == "shorten" and outcome.status == "active" else "cancel_all",
            valid_until=outcome.valid_until,
            timezone_name=timezone_name,
        )
        return outcome, await self._deliver(outcome, send_notice, text)

    async def _deliver(self, outcome: GrantOutcome, send_notice: NoticeSender, text: str) -> bool:
        try:
            delivered = bool(await send_notice(outcome.target_id, text))
        except Exception:
            logger.exception("Grant notice failed grant_id=%s", outcome.grant_id)
            delivered = False
        await self.mark_notified(grant_id=outcome.grant_id, delivered=delivered)
        return delivered

    async def mark_notified(self, *, grant_id: int, delivered: bool) -> None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        update(EntitlementGrantModel)
                        .where(EntitlementGrantModel.id == grant_id)
                        .values(notified=delivered)
                    )
        except Exception:
            logger.exception("Could not record the grant notification result grant_id=%s", grant_id)

    # ----- reads --------------------------------------------------------------------------------------

    async def recent(self, *, limit: int = 10) -> list[dict]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(EntitlementGrantModel)
                    .order_by(EntitlementGrantModel.created_at.desc(), EntitlementGrantModel.id.desc())
                    .limit(max(1, min(limit, 50)))
                )
            )
            chat_ids = {int(row.target_chat_id) for row in rows if row.target_chat_id is not None}
            titles = {}
            if chat_ids:
                titles = dict(
                    (await session.execute(select(ChatModel.telegram_chat_id, ChatModel.title).where(
                        ChatModel.telegram_chat_id.in_(chat_ids)
                    ))).all()
                )
            return [
                {
                    "id": int(row.id),
                    "scope": row.scope,
                    "target_id": int(row.target_chat_id if row.scope == "chat" else row.target_user_id),
                    "target_title": titles.get(row.target_chat_id) if row.scope == "chat" else None,
                    "action": row.action,
                    "delta_days": round(int(row.delta_seconds) / 86_400, 2),
                    "valid_until_after": _as_utc(row.valid_until_after).isoformat() if row.valid_until_after else None,
                    "status_after": row.status_after,
                    "reason": row.reason,
                    "source": row.source,
                    "notified": row.notified,
                    "created_at": _as_utc(row.created_at).isoformat(),
                }
                for row in rows
            ]

    async def target_state(self, *, scope: str, target_id: int, now: datetime | None = None) -> TargetState:
        scope, target_id = validate_target(scope, target_id)
        current = _as_utc(now or datetime.now(timezone.utc))
        async with self._session_factory() as session:
            entitlement = await self._entitlement_row(session, scope, target_id, for_update=False)
            title = None
            if scope == "chat":
                title = await session.scalar(select(ChatModel.title).where(ChatModel.telegram_chat_id == target_id))
            else:
                user = await session.get(UserModel, target_id)
                if user is not None:
                    title = user.username and f"@{user.username}" or user.first_name
            until = _as_utc(entitlement.valid_until) if entitlement is not None else None
            active = entitlement is not None and entitlement.status == "active" and until > current
            return TargetState(
                scope=scope,
                target_id=target_id,
                title=title,
                status=entitlement.status if entitlement is not None else None,
                valid_until=until,
                active=bool(active),
                paid_recently=await self._paid_recently(session, scope, target_id, current),
                granted=bool(active) and await self._granted_after_last_payment(session, scope, target_id),
            )

    async def active_personal(self, *, now: datetime | None = None, limit: int = 100) -> list[dict]:
        """Active Selara Personal subscriptions with the owner-granted mark (the chat list lives in analytics)."""
        current = _as_utc(now or datetime.now(timezone.utc))
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(UserEntitlementModel, UserModel.username, UserModel.first_name)
                    .outerjoin(UserModel, UserModel.telegram_user_id == UserEntitlementModel.user_id)
                    .where(
                        UserEntitlementModel.product_key == PRODUCT_BY_SCOPE["user"],
                        UserEntitlementModel.status == "active",
                        UserEntitlementModel.valid_until > current,
                    )
                    .order_by(UserEntitlementModel.valid_until.asc())
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
            items = []
            for entitlement, username, first_name in rows:
                until = _as_utc(entitlement.valid_until)
                items.append(
                    {
                        "user_id": int(entitlement.user_id),
                        "username": username,
                        "name": first_name,
                        "valid_until": until.isoformat(),
                        "days_left": max(0, -(-int((until - current).total_seconds()) // 86_400)),
                        "granted_by_admin": await self._granted_after_last_payment(
                            session, "user", int(entitlement.user_id)
                        ),
                    }
                )
            return items

    async def granted_by_admin(self, *, scope: str, target_id: int) -> bool:
        """True while the latest way the subscription was extended is an owner grant, not a payment."""
        async with self._session_factory() as session:
            return await self._granted_after_last_payment(session, scope, target_id)

    async def lookup(self, query: str, *, limit: int = 5) -> dict[str, list[dict]]:
        text = " ".join((query or "").split())
        if not text:
            return {"users": [], "chats": []}
        users: list[dict] = []
        chats: list[dict] = []
        async with self._session_factory() as session:
            number = int(text) if text.lstrip("-").isdigit() and len(text) <= 20 else None
            user_filter = []
            chat_filter = []
            if number is not None:
                user_filter.append(UserModel.telegram_user_id == number)
                chat_filter.append(ChatModel.telegram_chat_id == number)
            else:
                name = text.lstrip("@")
                user_filter.append(func.lower(UserModel.username) == name.lower())
                chat_filter.append(ChatModel.title.ilike(f"%{_like_escape(name)}%", escape="\\"))
            for user in await session.scalars(select(UserModel).where(or_(*user_filter)).limit(limit)):
                users.append(
                    {
                        "id": int(user.telegram_user_id),
                        "username": user.username,
                        "name": " ".join(part for part in (user.first_name, user.last_name) if part) or None,
                        "is_bot": bool(user.is_bot),
                    }
                )
            for chat in await session.scalars(
                select(ChatModel).where(or_(*chat_filter), ChatModel.type != "private").limit(limit)
            ):
                chats.append({"id": int(chat.telegram_chat_id), "title": chat.title, "type": chat.type})
        return {"users": users, "chats": chats}

    async def resolve_username(self, username: str) -> int | None:
        name = username.strip().lstrip("@").lower()
        if not name:
            return None
        async with self._session_factory() as session:
            return await session.scalar(
                select(UserModel.telegram_user_id).where(func.lower(UserModel.username) == name).limit(1)
            )

    # ----- internals ------------------------------------------------------------------------------------

    @staticmethod
    def _check_source(source: str) -> None:
        if source not in SOURCES:
            raise GrantError("invalid_source", "Неизвестный источник операции.")

    @staticmethod
    def _new_row(scope: str, target_id: int, *, valid_from: datetime, valid_until: datetime):
        if scope == "chat":
            return ChatEntitlementModel(
                chat_id=target_id, product_key=PRODUCT_BY_SCOPE[scope], status="active",
                valid_from=valid_from, valid_until=valid_until,
            )
        return UserEntitlementModel(
            user_id=target_id, product_key=PRODUCT_BY_SCOPE[scope], status="active",
            valid_from=valid_from, valid_until=valid_until,
        )

    @staticmethod
    async def _entitlement_row(session: AsyncSession, scope: str, target_id: int, *, for_update: bool = True):
        if scope == "chat":
            stmt = select(ChatEntitlementModel).where(
                ChatEntitlementModel.chat_id == target_id,
                ChatEntitlementModel.product_key == PRODUCT_BY_SCOPE[scope],
            )
        else:
            stmt = select(UserEntitlementModel).where(
                UserEntitlementModel.user_id == target_id,
                UserEntitlementModel.product_key == PRODUCT_BY_SCOPE[scope],
            )
        return await session.scalar(stmt.with_for_update() if for_update else stmt)

    @staticmethod
    async def _replay(
        session: AsyncSession, key: str, *, scope: str, target_id: int, actions: tuple[str, ...]
    ) -> GrantOutcome | None:
        row = await session.scalar(
            select(EntitlementGrantModel).where(EntitlementGrantModel.idempotency_key == key)
        )
        if row is None:
            return None
        same_target = row.scope == scope and (
            (row.target_chat_id if scope == "chat" else row.target_user_id) == target_id
        )
        if not same_target or row.action not in actions:
            raise GrantError("idempotency_conflict", "Этот ключ уже использован для другой операции.")
        return _outcome(row, duplicate=True)

    @staticmethod
    async def _require_known_target(session: AsyncSession, scope: str, target_id: int) -> None:
        if scope == "chat":
            chat = await session.get(ChatModel, target_id)
            if chat is None or chat.type == "private":
                raise GrantError("chat_not_found", "Чат не найден: бот его не знает. Проверьте id.")
            return
        user = await session.get(UserModel, target_id)
        if user is not None and user.is_bot:
            raise GrantError("invalid_target", "Ботам подписка не выдаётся.")
        if user is None:
            # A person who never opened the bot can still be granted access; the row keeps the FK valid.
            if _is_postgres(session):
                await session.execute(
                    pg_insert(UserModel)
                    .values(telegram_user_id=target_id, is_bot=False)
                    .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
                )
            else:
                session.add(UserModel(telegram_user_id=target_id, is_bot=False))
                await session.flush()

    @staticmethod
    async def _paid_recently(session: AsyncSession, scope: str, target_id: int, now: datetime) -> bool:
        column = SelaraAiPaymentModel.target_chat_id if scope == "chat" else SelaraAiPaymentModel.target_user_id
        found = await session.scalar(
            select(SelaraAiPaymentModel.id)
            .where(
                column == target_id,
                SelaraAiPaymentModel.processing_state == "applied",
                SelaraAiPaymentModel.payment_at >= now - RECENT_PAYMENT_WINDOW,
            )
            .limit(1)
        )
        return found is not None

    @staticmethod
    async def _granted_days_left(
        session: AsyncSession, scope: str, target_id: int, *, since: datetime
    ) -> timedelta:
        """Hand-granted time of the CURRENT period not yet taken back since the last full revoke.

        A grant made before the subscription lapsed (``created_at < valid_from`` of the period a later payment or
        grant started) is long used up and must never count against paid days.
        """
        column = _target_column(scope)
        rows = (
            await session.execute(
                select(EntitlementGrantModel.action, EntitlementGrantModel.delta_seconds, EntitlementGrantModel.created_at)
                .where(column == target_id)
                .order_by(EntitlementGrantModel.id.asc())
            )
        ).all()
        seconds = 0
        for action, delta, created_at in rows:
            if _as_utc(created_at) < since - _CLOCK_SLACK:  # a period starts when its first grant is written
                continue
            if action in ("grant", "extend"):
                seconds += int(delta)
            elif action == "shorten":
                seconds = max(0, seconds - int(delta))
            elif action == "revoke":
                seconds = 0
        return timedelta(seconds=seconds)

    @staticmethod
    async def _granted_after_last_payment(session: AsyncSession, scope: str, target_id: int) -> bool:
        column = _target_column(scope)
        last_grant = await session.scalar(
            select(func.max(EntitlementGrantModel.created_at)).where(
                column == target_id,
                EntitlementGrantModel.action.in_(("grant", "extend")),
            )
        )
        if last_grant is None:
            return False
        pay_column = SelaraAiPaymentModel.target_chat_id if scope == "chat" else SelaraAiPaymentModel.target_user_id
        last_payment = await session.scalar(
            select(func.max(SelaraAiPaymentModel.created_at)).where(
                pay_column == target_id, SelaraAiPaymentModel.processing_state == "applied"
            )
        )
        return last_payment is None or _as_utc(last_grant) > _as_utc(last_payment)


__all__ = ["EntitlementGrantService", "GrantOutcome", "TargetState", "SCOPES"]
