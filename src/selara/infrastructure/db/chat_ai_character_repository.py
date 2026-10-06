"""Storage of a group's Selara character, its call names and the member-mode dialogue.

A member turn is admitted under the row lock of the chat's ``chat_ai_characters``
row: the per-member cooldown and the ``pending`` history row are decided in one
transaction, so two quick messages from one member cannot both pass. Daily
limits are the feature quota's job (pool ``group_member_daily``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.ai_character.group import (
    DEFAULT_GROUP_PRESET,
    CallName,
    GroupCharacter,
    call_name_limit,
)
from selara.infrastructure.db.models import (
    ChatAiCallNameModel,
    ChatAiCharacterModel,
    ChatMemberAiMessageModel,
    UserModel,
)

AdmitStatus = Literal["ok", "duplicate", "cooldown", "disabled"]

# History older than this is pruned beyond the newest KEEP_RECENT rows.
HISTORY_RETENTION = timedelta(days=7)
KEEP_RECENT = 60


class GroupCharacterError(ValueError):
    """User-facing refusal; the message is safe to show in the chat."""


@dataclass(frozen=True, slots=True)
class MemberAdmission:
    status: AdmitStatus
    message_id: int | None = None
    retry_after: timedelta | None = None


@dataclass(frozen=True, slots=True)
class MemberHistoryRow:
    role: str
    author_user_id: int | None
    content: str


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ChatAiCharacterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    # ----- character -----------------------------------------------------------

    async def get_character(self, *, chat_id: int) -> GroupCharacter:
        row = await self._session.get(ChatAiCharacterModel, chat_id)
        if row is None:
            return GroupCharacter()
        return GroupCharacter(
            character_preset=row.character_preset,
            character_custom=row.character_custom,
            member_mode_enabled=bool(row.member_mode_enabled),
            member_history_access=bool(row.member_history_access),
        )

    async def update_character(self, *, chat_id: int, actor_user_id: int | None, **values) -> GroupCharacter:
        allowed = {"character_preset", "character_custom", "member_mode_enabled", "member_history_access"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown character fields: {sorted(unknown)}")
        row = await self._session.get(ChatAiCharacterModel, chat_id, with_for_update=True)
        if row is None:
            row = ChatAiCharacterModel(chat_id=chat_id, character_preset=DEFAULT_GROUP_PRESET)
            self._session.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_by_user_id = actor_user_id
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return await self.get_character(chat_id=chat_id)

    # ----- call names ----------------------------------------------------------

    async def list_names(self, *, chat_id: int) -> list[CallName]:
        rows = await self._session.scalars(
            select(ChatAiCallNameModel)
            .where(ChatAiCallNameModel.chat_id == chat_id)
            .order_by(ChatAiCallNameModel.is_primary.desc(), ChatAiCallNameModel.created_at, ChatAiCallNameModel.id)
        )
        return [CallName(row.name_display, row.name_norm, bool(row.is_primary)) for row in rows]

    async def add_name(
        self, *, chat_id: int, display: str, norm: str, actor_user_id: int | None, paid: bool
    ) -> CallName:
        # Serialize name edits of one chat on its character row.
        await self._lock_character(chat_id=chat_id)
        names = await self.list_names(chat_id=chat_id)
        if any(name.name_norm == norm for name in names):
            raise GroupCharacterError("Такая кличка уже есть.")
        limit = call_name_limit(paid)
        if len(names) >= limit:
            if paid:
                raise GroupCharacterError(f"Кличек уже {len(names)} — это максимум. Удалите одну, чтобы добавить новую.")
            raise GroupCharacterError(
                "В чате без Selara AI работает одна кличка. Удалите текущую или подключите Selara AI — до 5 кличек."
            )
        row = ChatAiCallNameModel(
            chat_id=chat_id,
            name_display=display,
            name_norm=norm,
            is_primary=not any(name.is_primary for name in names),
            created_by_user_id=actor_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        return CallName(row.name_display, row.name_norm, bool(row.is_primary))

    async def remove_name(self, *, chat_id: int, norm: str) -> CallName | None:
        await self._lock_character(chat_id=chat_id)
        row = await self._session.scalar(
            select(ChatAiCallNameModel).where(
                ChatAiCallNameModel.chat_id == chat_id, ChatAiCallNameModel.name_norm == norm
            )
        )
        if row is None:
            return None
        removed = CallName(row.name_display, row.name_norm, bool(row.is_primary))
        await self._session.delete(row)
        await self._session.flush()
        if removed.is_primary:
            successor = await self._session.scalar(
                select(ChatAiCallNameModel)
                .where(ChatAiCallNameModel.chat_id == chat_id)
                .order_by(ChatAiCallNameModel.created_at, ChatAiCallNameModel.id)
                .limit(1)
            )
            if successor is not None:
                successor.is_primary = True
                await self._session.flush()
        return removed

    async def set_primary(self, *, chat_id: int, norm: str) -> CallName | None:
        await self._lock_character(chat_id=chat_id)
        target = await self._session.scalar(
            select(ChatAiCallNameModel).where(
                ChatAiCallNameModel.chat_id == chat_id, ChatAiCallNameModel.name_norm == norm
            )
        )
        if target is None:
            return None
        # Clear first: the partial unique index allows one primary name per chat at any moment.
        await self._session.execute(
            update(ChatAiCallNameModel)
            .where(ChatAiCallNameModel.chat_id == chat_id, ChatAiCallNameModel.id != target.id)
            .values(is_primary=False)
        )
        await self._session.flush()
        target.is_primary = True
        await self._session.flush()
        return CallName(target.name_display, target.name_norm, True)

    async def _lock_character(self, *, chat_id: int) -> ChatAiCharacterModel:
        row = await self._session.get(ChatAiCharacterModel, chat_id, with_for_update=True)
        if row is None:
            row = ChatAiCharacterModel(chat_id=chat_id, character_preset=DEFAULT_GROUP_PRESET)
            self._session.add(row)
            await self._session.flush()
        return row

    # ----- member dialogue -----------------------------------------------------

    async def admit_turn(
        self,
        *,
        chat_id: int,
        author_user_id: int,
        content: str,
        idempotency_key: str,
        telegram_message_id: int | None,
        cooldown: timedelta,
        now: datetime,
    ) -> MemberAdmission:
        character = await self._session.get(ChatAiCharacterModel, chat_id, with_for_update=True)
        if character is None or not character.member_mode_enabled:
            return MemberAdmission(status="disabled")
        existing = await self._session.scalar(
            select(ChatMemberAiMessageModel.id).where(ChatMemberAiMessageModel.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return MemberAdmission(status="duplicate")
        # Every admitted turn counts, refused ones too: a member over quota still waits out the cooldown.
        if cooldown.total_seconds() > 0:
            last_at = await self._session.scalar(
                select(func.max(ChatMemberAiMessageModel.created_at)).where(
                    ChatMemberAiMessageModel.chat_id == chat_id,
                    ChatMemberAiMessageModel.author_user_id == author_user_id,
                    ChatMemberAiMessageModel.role == "user",
                )
            )
            if last_at is not None:
                left = _as_utc(last_at) + cooldown - now
                if left.total_seconds() > 0:
                    return MemberAdmission(status="cooldown", retry_after=left)
        # The member's first tracked message may be this call: the activity tracker adds users afterwards.
        bind = self._session.bind
        if bind is not None and bind.dialect.name == "postgresql":
            await self._session.execute(
                pg_insert(UserModel)
                .values(telegram_user_id=author_user_id, is_bot=False)
                .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
            )
        elif await self._session.get(UserModel, author_user_id) is None:
            self._session.add(UserModel(telegram_user_id=author_user_id, is_bot=False))
            await self._session.flush()
        row = ChatMemberAiMessageModel(
            chat_id=chat_id,
            author_user_id=author_user_id,
            role="user",
            content=content,
            status="pending",
            idempotency_key=idempotency_key,
            telegram_message_id=telegram_message_id,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return MemberAdmission(status="ok", message_id=int(row.id))

    async def set_status(self, *, message_id: int, status: str) -> None:
        await self._session.execute(
            update(ChatMemberAiMessageModel).where(ChatMemberAiMessageModel.id == message_id).values(status=status)
        )

    async def add_reply(self, *, chat_id: int, content: str, now: datetime) -> int:
        row = ChatMemberAiMessageModel(chat_id=chat_id, role="assistant", content=content, status="ok", created_at=now)
        self._session.add(row)
        await self._session.flush()
        return int(row.id)

    async def set_reply_message_id(self, *, row_id: int, telegram_message_id: int) -> None:
        await self._session.execute(
            update(ChatMemberAiMessageModel)
            .where(ChatMemberAiMessageModel.id == row_id)
            .values(telegram_message_id=telegram_message_id)
        )

    async def is_member_answer(self, *, chat_id: int, telegram_message_id: int) -> bool:
        """Whether the replied-to bot message is a member-mode answer in this chat."""
        found = await self._session.scalar(
            select(ChatMemberAiMessageModel.id)
            .where(
                ChatMemberAiMessageModel.chat_id == chat_id,
                ChatMemberAiMessageModel.telegram_message_id == telegram_message_id,
                ChatMemberAiMessageModel.role == "assistant",
            )
            .limit(1)
        )
        return found is not None

    async def recent(self, *, chat_id: int, limit: int) -> list[MemberHistoryRow]:
        rows = (
            await self._session.scalars(
                select(ChatMemberAiMessageModel)
                .where(ChatMemberAiMessageModel.chat_id == chat_id, ChatMemberAiMessageModel.status == "ok")
                .order_by(ChatMemberAiMessageModel.created_at.desc(), ChatMemberAiMessageModel.id.desc())
                .limit(limit)
            )
        ).all()
        return [MemberHistoryRow(row.role, row.author_user_id, row.content) for row in reversed(rows)]

    async def prune_history(self, *, chat_id: int, now: datetime) -> None:
        keep_ids = (
            select(ChatMemberAiMessageModel.id)
            .where(ChatMemberAiMessageModel.chat_id == chat_id)
            .order_by(ChatMemberAiMessageModel.created_at.desc(), ChatMemberAiMessageModel.id.desc())
            .limit(KEEP_RECENT)
        )
        await self._session.execute(
            delete(ChatMemberAiMessageModel).where(
                ChatMemberAiMessageModel.chat_id == chat_id,
                ChatMemberAiMessageModel.created_at < now - HISTORY_RETENTION,
                ChatMemberAiMessageModel.id.not_in(keep_ids.scalar_subquery()),
            )
        )

    async def reset_history(self, *, chat_id: int) -> int:
        result = await self._session.execute(
            delete(ChatMemberAiMessageModel).where(ChatMemberAiMessageModel.chat_id == chat_id)
        )
        return int(result.rowcount or 0)
