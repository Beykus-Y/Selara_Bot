"""Transactional AI-pet operations.

Every mutation runs inside the caller's session (one Telegram update = one
transaction): the pet row is locked first, then the relationship row, then the
economy account, so concurrent updates cannot double-feed or overdraw. An event
row keyed by ``idempotency_key`` makes a replayed update a no-op.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.ai_pets import mechanics as m
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetInventoryModel,
    AiPetItemModel,
    AiPetModel,
    AiPetRelationshipModel,
    ChatSettingsModel,
    UserEntitlementModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository, _lock_resources

logger = logging.getLogger(__name__)

ResultStatus = Literal[
    "ok",
    "duplicate",
    "cooldown",
    "blocked",
    "unavailable",
    "item_unavailable",
    "level_too_low",
    "insufficient_funds",
    "economy_unavailable",
]


class PetDomainError(Exception):
    """A user-facing refusal; ``str(exc)`` is shown as is."""


@dataclass(frozen=True, slots=True)
class PetView:
    id: int
    owner_user_id: int
    name: str
    species_key: str
    species_custom: str | None
    traits: tuple[str, ...]
    level: int
    xp: int
    mood: int
    satiety: int
    energy: int
    status: str
    dormant_reason: str | None
    current_chat_id: int | None
    home_chat_id: int | None
    travel_unlocked: bool
    character_custom: str | None = None

    @property
    def species_title(self) -> str:
        return m.species_title(self.species_key, self.species_custom)

    @property
    def emoji(self) -> str:
        return m.species_for(self.species_key).emoji


@dataclass(frozen=True, slots=True)
class CatalogItem:
    code: str
    title: str
    kind: str
    price: int
    effects: m.ItemEffects
    min_level: int
    slot: str | None = None


@dataclass(frozen=True, slots=True)
class BagEntry:
    item: CatalogItem
    quantity: int
    equipped: bool


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: ResultStatus
    pet: PetView | None = None
    message: str = ""
    applied: dict[str, int] = field(default_factory=dict)
    leveled_up_to: int | None = None
    affinity: int | None = None
    item: CatalogItem | None = None
    new_balance: int | None = None


@dataclass(frozen=True, slots=True)
class SpontaneousClaim:
    """A reserved spontaneous event: the journal row is written, the line is not posted yet."""

    event_id: int
    pet: PetView
    person_affinity: int


TravelStatus = Literal["ok", "no_pet", "locked", "no_personal", "asleep", "same_chat", "name_taken", "cooldown"]
TRAVEL_COOLDOWN = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class TravelResult:
    status: TravelStatus
    pet: PetView | None = None
    from_chat_id: int | None = None
    retry_after: timedelta | None = None


def _view(row: AiPetModel) -> PetView:
    return PetView(
        id=int(row.id),
        owner_user_id=int(row.owner_user_id),
        name=row.name,
        species_key=row.species_key,
        species_custom=row.species_custom,
        traits=tuple(row.traits or ()),
        level=int(row.level),
        xp=int(row.xp),
        mood=int(row.mood),
        satiety=int(row.satiety),
        energy=int(row.energy),
        status=row.status,
        dormant_reason=row.dormant_reason,
        current_chat_id=row.current_chat_id,
        home_chat_id=row.home_chat_id,
        travel_unlocked=bool(row.travel_unlocked),
        character_custom=row.character_custom,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stats(row: AiPetModel) -> m.PetStats:
    return m.PetStats(
        level=int(row.level),
        xp=int(row.xp),
        mood=int(row.mood),
        satiety=int(row.satiety),
        energy=int(row.energy),
        last_tick_at=_as_utc(row.last_tick_at),
    )


def _store_stats(row: AiPetModel, stats: m.PetStats) -> None:
    row.level = stats.level
    row.xp = stats.xp
    row.mood = stats.mood
    row.satiety = stats.satiety
    row.energy = stats.energy
    row.last_tick_at = stats.last_tick_at
    if stats.level >= m.TRAVEL_UNLOCK_LEVEL:
        row.travel_unlocked = True


class AiPetService:
    def __init__(self, session: AsyncSession, economy_repo: SqlAlchemyEconomyRepository | None = None) -> None:
        self._session = session
        self._economy = economy_repo or SqlAlchemyEconomyRepository(session)

    # ----- reads -------------------------------------------------------------

    async def has_active_personal(self, *, user_id: int, now: datetime) -> bool:
        row_id = await self._session.scalar(
            select(UserEntitlementModel.id).where(
                UserEntitlementModel.user_id == user_id,
                UserEntitlementModel.product_key == SELARA_PERSONAL_PRODUCT_KEY,
                UserEntitlementModel.status == "active",
                UserEntitlementModel.valid_until > now,
            )
        )
        return row_id is not None

    async def get_pet(self, pet_id: int) -> PetView | None:
        row = await self._session.get(AiPetModel, pet_id)
        return _view(row) if row is not None else None

    async def get_owner_pet(self, *, owner_user_id: int, now: datetime | None = None) -> PetView | None:
        # Settling writes the tick back, so lock the row like any other mutation.
        row = await self._owner_row(owner_user_id, for_update=now is not None)
        if row is None:
            return None
        if now is not None:
            await self._settle(row, now=now)
        return _view(row)

    async def current_view(self, *, pet_id: int, now: datetime) -> PetView | None:
        """Up-to-date pet for display: applies the pending tick under the row lock."""
        row = await self._session.scalar(select(AiPetModel).where(AiPetModel.id == pet_id).with_for_update())
        if row is None:
            return None
        await self._settle(row, now=now)
        return _view(row)

    async def list_chat_pets(self, *, chat_id: int) -> list[PetView]:
        rows = await self._session.scalars(
            select(AiPetModel)
            .where(AiPetModel.current_chat_id == chat_id, AiPetModel.status == "active")
            .order_by(AiPetModel.level.desc(), AiPetModel.id)
        )
        return [_view(row) for row in rows]

    async def find_chat_pet(self, *, chat_id: int, actor_user_id: int, name: str | None) -> PetView | None:
        """Pick the target: by name, else the actor's own pet here, else the only pet here."""
        pets = await self.list_chat_pets(chat_id=chat_id)
        if name:
            wanted = m.normalize_name(name)
            return next((pet for pet in pets if m.normalize_name(pet.name) == wanted), None)
        own = next((pet for pet in pets if pet.owner_user_id == actor_user_id), None)
        if own is not None:
            return own
        return pets[0] if len(pets) == 1 else None

    async def relation_affinity(self, *, pet_id: int, chat_id: int, user_id: int) -> int:
        value = await self._session.scalar(
            select(AiPetRelationshipModel.affinity).where(
                AiPetRelationshipModel.pet_id == pet_id,
                AiPetRelationshipModel.chat_id == chat_id,
                AiPetRelationshipModel.user_id == user_id,
            )
        )
        return int(value or 0)

    async def top_relations(self, *, pet_id: int, chat_id: int, limit: int = 3) -> list[tuple[int, int]]:
        rows = await self._session.execute(
            select(AiPetRelationshipModel.user_id, AiPetRelationshipModel.affinity)
            .where(AiPetRelationshipModel.pet_id == pet_id, AiPetRelationshipModel.chat_id == chat_id)
            .order_by(AiPetRelationshipModel.affinity.desc())
            .limit(limit)
        )
        return [(int(user_id), int(affinity)) for user_id, affinity in rows]

    async def list_items(self, *, level: int | None = None) -> list[CatalogItem]:
        rows = await self._session.scalars(
            select(AiPetItemModel)
            .where(AiPetItemModel.enabled.is_(True))
            .order_by(AiPetItemModel.sort_order, AiPetItemModel.price, AiPetItemModel.code)
        )
        items: list[CatalogItem] = []
        for row in rows:
            item = self._catalog_item(row)
            if item is None:
                continue
            if level is not None and item.min_level > level:
                continue
            items.append(item)
        return items

    @staticmethod
    def _catalog_item(row: AiPetItemModel) -> CatalogItem | None:
        effects = m.parse_item_effects(row.effects, kind=row.kind)
        slot = getattr(row, "slot", None)
        bad_slot = (row.kind == "cosmetic") != (slot in m.COSMETIC_SLOTS)
        if effects is None or row.price < 0 or bad_slot:
            logger.warning("ai_pet_item_invalid code=%s kind=%s", row.code, row.kind)
            return None
        return CatalogItem(
            code=row.code,
            title=row.title,
            kind=row.kind,
            price=int(row.price),
            effects=effects,
            min_level=int(row.min_level),
            slot=slot if row.kind == "cosmetic" else None,
        )

    # ----- lifecycle ---------------------------------------------------------

    async def create_pet(
        self,
        *,
        owner: UserSnapshot,
        chat: ChatSnapshot,
        species_raw: str,
        name_raw: str,
        now: datetime,
    ) -> PetView:
        species_key, species_custom = m.resolve_species(species_raw)
        name = m.validate_name(name_raw)
        name_norm = m.normalize_name(name)
        if not await self.has_active_personal(user_id=owner.telegram_user_id, now=now):
            raise PetDomainError("Завести AI-питомца можно с подпиской Selara Personal: /premium в личке с ботом.")

        await _lock_resources(
            self._session,
            f"ai_pet:owner:{owner.telegram_user_id}",
            f"ai_pet:chat:{chat.telegram_chat_id}",
        )
        if await self._owner_row(owner.telegram_user_id) is not None:
            raise PetDomainError("У вас уже есть питомец. Сначала отпустите его: /pet_release.")
        if await self._name_taken(chat_id=chat.telegram_chat_id, name_norm=name_norm):
            raise PetDomainError("В этом чате уже есть питомец с таким именем.")

        await self._economy.ensure_chat_and_user(chat=chat, user=owner)
        row = AiPetModel(
            owner_user_id=owner.telegram_user_id,
            home_chat_id=chat.telegram_chat_id,
            current_chat_id=chat.telegram_chat_id,
            species_key=species_key,
            species_custom=species_custom,
            name=name,
            name_norm=name_norm,
            traits=[],
            level=1,
            xp=0,
            mood=70,
            satiety=70,
            energy=70,
            status="active",
            last_tick_at=now,
            version=0,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise PetDomainError("Не удалось завести питомца: имя или питомец уже заняты.") from exc
        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=chat.telegram_chat_id,
                actor_user_id=owner.telegram_user_id,
                event_type="created",
                effects={"species": species_key, "name": name},
                idempotency_key=f"ai_pet:created:{row.id}",
            )
        )
        await self._session.flush()
        return _view(row)

    async def set_traits(self, *, owner_user_id: int, traits: list[str]) -> PetView:
        row = await self._owner_row(owner_user_id, for_update=True)
        if row is None:
            raise PetDomainError("У вас нет питомца. Заведите его: /pet_new <вид> <имя>.")
        row.traits = list(traits)
        row.version = int(row.version) + 1
        await self._session.flush()
        return _view(row)

    async def set_character(self, *, owner_user_id: int, character: str | None) -> PetView:
        """Owner-written character; validated by the caller, used by the pet's dialogue as data."""
        row = await self._owner_row(owner_user_id, for_update=True)
        if row is None:
            raise PetDomainError("У вас нет питомца. Заведите его: /pet_new <вид> <имя>.")
        row.character_custom = character
        row.version = int(row.version) + 1
        await self._session.flush()
        return _view(row)

    async def claim_spontaneous_event(
        self,
        *,
        chat_id: int,
        person_user_id: int | None,
        now: datetime,
        day_start: datetime,
        daily_limit: int,
        chat_interval: timedelta,
        rng: random.Random | None = None,
    ) -> SpontaneousClaim | None:
        """Reserve one spontaneous event in this chat, or ``None`` when nothing may happen now.

        A chat-scoped advisory lock serialises concurrent checks, so the chat interval and
        each pet's daily cap hold even when several updates arrive at once. Only pets whose
        owner has an active Selara Personal qualify (§5.0).
        """
        await _lock_resources(self._session, f"ai_pet:events:{chat_id}")
        last_in_chat = await self._session.scalar(
            select(func.max(AiPetEventModel.created_at)).where(
                AiPetEventModel.chat_id == chat_id, AiPetEventModel.event_type == "spontaneous"
            )
        )
        if last_in_chat is not None and _as_utc(last_in_chat) + chat_interval > now:
            return None

        candidates: list[AiPetModel] = []
        for row in await self._session.scalars(
            select(AiPetModel).where(AiPetModel.current_chat_id == chat_id, AiPetModel.status == "active")
        ):
            today = await self._session.scalar(
                select(func.count()).where(
                    AiPetEventModel.pet_id == row.id,
                    AiPetEventModel.event_type == "spontaneous",
                    AiPetEventModel.created_at >= day_start,
                )
            )
            if int(today or 0) >= daily_limit:
                continue
            if not await self.has_active_personal(user_id=int(row.owner_user_id), now=now):
                continue
            candidates.append(row)
        if not candidates:
            return None

        chosen = (rng or random).choice(candidates)
        row = await self._session.scalar(select(AiPetModel).where(AiPetModel.id == chosen.id).with_for_update())
        await self._settle(row, now=now)
        if row.status != "active" or row.current_chat_id != chat_id:
            return None
        affinity = 0
        if person_user_id is not None:
            affinity = await self.relation_affinity(pet_id=int(row.id), chat_id=chat_id, user_id=person_user_id)
        event = AiPetEventModel(
            pet_id=row.id,
            chat_id=chat_id,
            actor_user_id=None,
            event_type="spontaneous",
            effects={"status": "claimed", "person": person_user_id},
            idempotency_key=f"ai_pet:spontaneous:{chat_id}:{row.id}:{int(now.timestamp() * 1000)}",
            created_at=now,
        )
        self._session.add(event)
        await self._session.flush()
        return SpontaneousClaim(event_id=int(event.id), pet=_view(row), person_affinity=affinity)

    async def finish_spontaneous_event(self, *, event_id: int, status: str, text: str | None = None) -> None:
        row = await self._session.get(AiPetEventModel, event_id)
        if row is None:
            return
        row.effects = {**(row.effects or {}), "status": status, **({"text": text} if text else {})}
        await self._session.flush()

    async def travel(
        self, *, owner_user_id: int, chat: ChatSnapshot, now: datetime, make_home: bool
    ) -> TravelResult:
        """Move the owner's pet into ``chat`` (the caller checked membership and pets_enabled).

        ``make_home`` also makes the chat the pet's home. Relationships and memory stay keyed
        by the chat they came from.
        """
        # The same chat lock as pet creation, so a new pet cannot take the name meanwhile.
        await _lock_resources(self._session, f"ai_pet:chat:{chat.telegram_chat_id}")
        row = await self._owner_row(owner_user_id, for_update=True)
        if row is None:
            return TravelResult(status="no_pet")
        await self._settle(row, now=now)
        if row.status == "dormant" and row.dormant_reason == "admin_sleep":
            return TravelResult(status="asleep", pet=_view(row))
        target = chat.telegram_chat_id
        if row.status == "active" and row.current_chat_id == target and (not make_home or row.home_chat_id == target):
            return TravelResult(status="same_chat", pet=_view(row))
        # Travel rights are needed only to take an awake pet into a chat that is neither where it
        # lives now nor its home. Going home, adopting the current chat as home and settling a pet
        # that lost its home are always allowed.
        needs_travel_rights = row.status == "active" and target not in (row.current_chat_id, row.home_chat_id)
        if needs_travel_rights:
            if not row.travel_unlocked:
                return TravelResult(status="locked", pet=_view(row))
            if not await self.has_active_personal(user_id=owner_user_id, now=now):
                return TravelResult(status="no_personal", pet=_view(row))
            left = m.cooldown_left(
                await self._last_event_at(pet_id=int(row.id), actor_user_id=owner_user_id, event_type="travel"),
                TRAVEL_COOLDOWN,
                now,
            )
            if left is not None:
                return TravelResult(status="cooldown", pet=_view(row), retry_after=left)
        # Any pet that becomes active here needs a free name, including a dormant one already here
        # (e.g. put to sleep by a name clash on a group upgrade). It is not active, so never counts itself.
        becomes_active_here = row.status != "active" or row.current_chat_id != target
        if becomes_active_here and await self._name_taken(chat_id=target, name_norm=row.name_norm):
            return TravelResult(status="name_taken", pet=_view(row))

        await self._economy.ensure_chat_and_user(
            chat=chat,
            user=UserSnapshot(telegram_user_id=owner_user_id, username=None, first_name=None, last_name=None, is_bot=False),
        )
        from_chat_id = row.current_chat_id
        row.current_chat_id = target
        if make_home:
            row.home_chat_id = target
        row.status = "active"
        row.dormant_reason = None
        row.version = int(row.version) + 1
        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=target,
                actor_user_id=owner_user_id,
                # A move into a foreign chat is travel whichever command did it, so it starts the cooldown.
                event_type="travel" if needs_travel_rights else "rehome",
                effects={"from_chat_id": from_chat_id, "home": make_home},
                idempotency_key=f"ai_pet:move:{row.id}:{row.version}",
                created_at=now,
            )
        )
        await self._session.flush()
        return TravelResult(status="ok", pet=_view(row), from_chat_id=from_chat_id)

    async def release(self, *, owner_user_id: int, chat_id: int | None) -> PetView:
        row = await self._owner_row(owner_user_id, for_update=True)
        if row is None:
            raise PetDomainError("У вас нет питомца.")
        row.status = "released"
        row.dormant_reason = None
        row.version = int(row.version) + 1
        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=chat_id,
                actor_user_id=owner_user_id,
                event_type="released",
                effects={},
                idempotency_key=f"ai_pet:released:{row.id}",
            )
        )
        await self._session.flush()
        return _view(row)

    async def set_sleep(self, *, chat_id: int, name: str, actor_user_id: int, asleep: bool) -> PetView:
        """Chat admin puts a pet living here to sleep, or wakes it up."""
        wanted = m.normalize_name(name)
        row = await self._session.scalar(
            select(AiPetModel)
            .where(
                AiPetModel.current_chat_id == chat_id,
                AiPetModel.name_norm == wanted,
                AiPetModel.status == ("active" if asleep else "dormant"),
            )
            .order_by(AiPetModel.id)
            .with_for_update()
        )
        if row is None:
            raise PetDomainError("Питомец с таким именем в этом чате не найден.")
        if asleep:
            row.status = "dormant"
            row.dormant_reason = "admin_sleep"
        else:
            if await self._name_taken(chat_id=chat_id, name_norm=row.name_norm):
                raise PetDomainError("Имя уже занято другим питомцем в этом чате.")
            row.status = "active"
            row.dormant_reason = None
        row.version = int(row.version) + 1
        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=chat_id,
                actor_user_id=actor_user_id,
                event_type="sleep" if asleep else "wake",
                effects={},
                idempotency_key=f"ai_pet:{'sleep' if asleep else 'wake'}:{row.id}:{row.version}",
            )
        )
        await self._session.flush()
        return _view(row)

    # ----- mechanics ---------------------------------------------------------

    async def perform_action(
        self,
        *,
        pet_id: int,
        chat_id: int,
        actor_user_id: int,
        action_key: str,
        idempotency_key: str,
        today: date,
        now: datetime,
    ) -> ActionResult:
        action = m.ACTIONS[action_key]
        row = await self._locked_pet_here(pet_id=pet_id, chat_id=chat_id, now=now)
        if row is None:
            return ActionResult(status="unavailable", message="Этого питомца здесь нет или он спит.")
        if await self._event_exists(idempotency_key):
            return ActionResult(status="duplicate", pet=_view(row))

        left = m.cooldown_left(
            await self._last_event_at(pet_id=row.id, actor_user_id=actor_user_id, event_type=action.key),
            action.cooldown,
            now,
        )
        if left is not None:
            return ActionResult(
                status="cooldown",
                pet=_view(row),
                message=f"{row.name} ещё не готов(а) к этому. Попробуйте через {m.format_duration(left)}.",
            )
        stats = _stats(row)
        blocked = m.action_block_reason(action, stats)
        if blocked is not None:
            return ActionResult(status="blocked", pet=_view(row), message=f"{row.name} {blocked}.")

        return await self._apply(
            row,
            chat_id=chat_id,
            actor_user_id=actor_user_id,
            event_type=action.key,
            effect=m.action_effect(action),
            idempotency_key=idempotency_key,
            today=today,
            now=now,
            extra={},
        )

    async def use_item(
        self,
        *,
        pet_id: int,
        chat_id: int,
        actor: UserSnapshot,
        item_code: str | None,
        kind: str | None,
        idempotency_key: str,
        economy_mode: str,
        today: date,
        now: datetime,
    ) -> ActionResult:
        """Buy food or a toy and apply it at once (cosmetics go to the bag via ``buy_to_bag``)."""
        row = await self._locked_pet_here(pet_id=pet_id, chat_id=chat_id, now=now)
        if row is None:
            return ActionResult(status="unavailable", message="Этого питомца здесь нет или он спит.")
        if await self._event_exists(idempotency_key):
            return ActionResult(status="duplicate", pet=_view(row))

        item = await self._pick_item(item_code=item_code, kind=kind, level=int(row.level))
        if item is None:
            return ActionResult(status="item_unavailable", pet=_view(row), message="Такого товара сейчас нет.")
        if item.kind not in m.ITEM_EVENT_TYPES:
            return ActionResult(
                status="item_unavailable", pet=_view(row), item=item, message="Эту вещь можно только положить в рюкзак."
            )
        if item.min_level > int(row.level):
            return ActionResult(
                status="level_too_low",
                pet=_view(row),
                item=item,
                message=f"«{item.title}» откроется на {item.min_level} уровне питомца.",
            )
        event_type = m.ITEM_EVENT_TYPES[item.kind]
        left = m.cooldown_left(
            await self._last_event_at(pet_id=row.id, actor_user_id=actor.telegram_user_id, event_type=event_type),
            m.ITEM_COOLDOWNS[item.kind],
            now,
        )
        if left is not None:
            return ActionResult(
                status="cooldown",
                pet=_view(row),
                item=item,
                message=f"{row.name} пока не хочет. Попробуйте через {m.format_duration(left)}.",
            )
        stats = _stats(row)
        blocked = m.item_block_reason(item.kind, stats)
        if blocked is not None:
            return ActionResult(status="blocked", pet=_view(row), item=item, message=f"{row.name} {blocked}.")

        new_balance, refusal = await self._charge(
            row, actor=actor, item=item, chat_id=chat_id, economy_mode=economy_mode, reason="ai_pet_item"
        )
        if refusal is not None:
            return refusal

        result = await self._apply(
            row,
            chat_id=chat_id,
            actor_user_id=actor.telegram_user_id,
            event_type=event_type,
            effect=m.item_effect(item.kind, item.effects),
            idempotency_key=idempotency_key,
            today=today,
            now=now,
            # The price is frozen in the event so later catalog edits never rewrite history.
            extra={"item": item.code, "price": item.price},
        )
        return ActionResult(
            status=result.status,
            pet=result.pet,
            applied=result.applied,
            leveled_up_to=result.leveled_up_to,
            affinity=result.affinity,
            item=item,
            new_balance=new_balance,
        )

    # ----- bag and wardrobe ----------------------------------------------------

    async def bag(self, *, pet_id: int) -> list[BagEntry]:
        rows = await self._session.execute(
            select(AiPetInventoryModel, AiPetItemModel)
            .join(AiPetItemModel, AiPetItemModel.code == AiPetInventoryModel.item_code)
            .where(AiPetInventoryModel.pet_id == pet_id, AiPetInventoryModel.quantity > 0)
            .order_by(AiPetItemModel.kind, AiPetItemModel.sort_order, AiPetItemModel.code)
        )
        entries: list[BagEntry] = []
        for owned, item_row in rows:
            item = self._catalog_item(item_row)
            if item is not None:
                entries.append(BagEntry(item=item, quantity=int(owned.quantity), equipped=bool(owned.equipped)))
        return entries

    async def outfit(self, *, pet_id: int) -> list[str]:
        """Titles of the cosmetics the pet wears, for the card and its dialogue."""
        return [entry.item.title for entry in await self.bag(pet_id=pet_id) if entry.equipped]

    async def buy_to_bag(
        self,
        *,
        pet_id: int,
        chat_id: int,
        actor: UserSnapshot,
        item_code: str,
        idempotency_key: str,
        economy_mode: str,
        now: datetime,
    ) -> ActionResult:
        """Buy an item into the pet's bag; anyone may, so it doubles as a gift to the pet."""
        row = await self._locked_pet_here(pet_id=pet_id, chat_id=chat_id, now=now)
        if row is None:
            return ActionResult(status="unavailable", message="Этого питомца здесь нет или он спит.")
        if await self._event_exists(idempotency_key):
            return ActionResult(status="duplicate", pet=_view(row))
        item = await self._pick_item(item_code=item_code, kind=None, level=int(row.level))
        if item is None:
            return ActionResult(status="item_unavailable", pet=_view(row), message="Такого товара сейчас нет.")
        if item.min_level > int(row.level):
            return ActionResult(
                status="level_too_low",
                pet=_view(row),
                item=item,
                message=f"«{item.title}» откроется на {item.min_level} уровне питомца.",
            )
        owned = await self._session.get(
            AiPetInventoryModel, {"pet_id": int(row.id), "item_code": item.code}, with_for_update=True
        )
        quantity = int(owned.quantity) if owned is not None else 0
        if item.kind == "cosmetic" and quantity > 0:
            return ActionResult(status="blocked", pet=_view(row), item=item, message=f"У {row.name} уже есть «{item.title}».")
        if item.kind != "cosmetic" and quantity >= m.BAG_STACK_LIMIT:
            return ActionResult(
                status="blocked", pet=_view(row), item=item, message=f"В рюкзаке уже {m.BAG_STACK_LIMIT} шт. «{item.title}»."
            )

        new_balance, refusal = await self._charge(
            row, actor=actor, item=item, chat_id=chat_id, economy_mode=economy_mode, reason="ai_pet_bag"
        )
        if refusal is not None:
            return refusal
        if owned is None:
            owned = AiPetInventoryModel(pet_id=int(row.id), item_code=item.code, quantity=0, equipped=False, acquired_at=now)
            self._session.add(owned)
        owned.quantity = quantity + 1
        owned.updated_at = now
        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=chat_id,
                actor_user_id=actor.telegram_user_id,
                event_type="bag_add",
                effects={"item": item.code, "price": item.price, "gift": actor.telegram_user_id != row.owner_user_id},
                idempotency_key=idempotency_key,
                created_at=now,
            )
        )
        await self._session.flush()
        return ActionResult(status="ok", pet=_view(row), item=item, new_balance=new_balance)

    async def use_from_bag(
        self,
        *,
        pet_id: int,
        chat_id: int,
        owner_user_id: int,
        item_code: str,
        idempotency_key: str,
        today: date,
        now: datetime,
    ) -> ActionResult:
        """The owner gives the pet food or a toy from its bag: same rules as a purchase, no payment."""
        row = await self._locked_pet_here(pet_id=pet_id, chat_id=chat_id, now=now)
        if row is None:
            return ActionResult(status="unavailable", message="Этого питомца здесь нет или он спит.")
        if int(row.owner_user_id) != owner_user_id:
            return ActionResult(status="blocked", pet=_view(row), message="Рюкзаком пользуется только хозяин.")
        if await self._event_exists(idempotency_key):
            return ActionResult(status="duplicate", pet=_view(row))
        owned = await self._session.get(
            AiPetInventoryModel, {"pet_id": int(row.id), "item_code": item_code}, with_for_update=True
        )
        item_row = await self._session.get(AiPetItemModel, item_code)
        item = self._catalog_item(item_row) if item_row is not None else None
        if owned is None or owned.quantity <= 0 or item is None or item.kind not in m.ITEM_EVENT_TYPES:
            return ActionResult(status="item_unavailable", pet=_view(row), message="Этого нет в рюкзаке.")
        event_type = m.ITEM_EVENT_TYPES[item.kind]
        left = m.cooldown_left(
            await self._last_event_at(pet_id=row.id, actor_user_id=owner_user_id, event_type=event_type),
            m.ITEM_COOLDOWNS[item.kind],
            now,
        )
        if left is not None:
            return ActionResult(
                status="cooldown",
                pet=_view(row),
                item=item,
                message=f"{row.name} пока не хочет. Попробуйте через {m.format_duration(left)}.",
            )
        blocked = m.item_block_reason(item.kind, _stats(row))
        if blocked is not None:
            return ActionResult(status="blocked", pet=_view(row), item=item, message=f"{row.name} {blocked}.")
        owned.quantity = int(owned.quantity) - 1
        owned.updated_at = now
        result = await self._apply(
            row,
            chat_id=chat_id,
            actor_user_id=owner_user_id,
            event_type=event_type,
            effect=m.item_effect(item.kind, item.effects),
            idempotency_key=idempotency_key,
            today=today,
            now=now,
            extra={"item": item.code, "from_bag": True},
        )
        return ActionResult(
            status=result.status,
            pet=result.pet,
            applied=result.applied,
            leveled_up_to=result.leveled_up_to,
            affinity=result.affinity,
            item=item,
        )

    async def set_equipped(self, *, owner_user_id: int, item_code: str, equipped: bool) -> tuple[PetView, CatalogItem]:
        """Put on or take off a cosmetic; putting one on takes off whatever was in that slot."""
        row = await self._owner_row(owner_user_id, for_update=True)
        if row is None:
            raise PetDomainError("У вас нет питомца.")
        owned = await self._session.get(
            AiPetInventoryModel, {"pet_id": int(row.id), "item_code": item_code}, with_for_update=True
        )
        item_row = await self._session.get(AiPetItemModel, item_code)
        item = self._catalog_item(item_row) if item_row is not None else None
        if owned is None or owned.quantity <= 0 or item is None or item.kind != "cosmetic":
            raise PetDomainError("Этой вещи нет в гардеробе.")
        if equipped:
            same_slot = select(AiPetItemModel.code).where(AiPetItemModel.slot == item.slot)
            for other in await self._session.scalars(
                select(AiPetInventoryModel).where(
                    AiPetInventoryModel.pet_id == row.id,
                    AiPetInventoryModel.equipped.is_(True),
                    AiPetInventoryModel.item_code.in_(same_slot),
                )
            ):
                other.equipped = False
        owned.equipped = equipped
        row.version = int(row.version) + 1
        await self._session.flush()
        return _view(row), item

    # ----- internals ---------------------------------------------------------

    async def _charge(
        self, row: AiPetModel, *, actor: UserSnapshot, item: CatalogItem, chat_id: int, economy_mode: str, reason: str
    ) -> tuple[int | None, ActionResult | None]:
        """Debit the buyer through the economy ledger; returns ``(new_balance, refusal)``."""
        scope, error = await self._economy.resolve_scope(mode=economy_mode, chat_id=chat_id, user_id=actor.telegram_user_id)
        if scope is None:
            return None, ActionResult(status="economy_unavailable", pet=_view(row), item=item, message=error or "")
        account, _ = await self._economy.get_or_create_account(scope=scope, user_id=actor.telegram_user_id)
        if item.price <= 0:
            return account.balance, None
        if account.balance < item.price:
            return None, ActionResult(
                status="insufficient_funds",
                pet=_view(row),
                item=item,
                message=f"Не хватает монет: «{item.title}» стоит {item.price}, у вас {account.balance}.",
            )
        try:
            new_balance = await self._economy.add_balance(account_id=account.id, delta=-item.price)
        except ValueError:
            return None, ActionResult(
                status="insufficient_funds",
                pet=_view(row),
                item=item,
                message=f"Не хватает монет: «{item.title}» стоит {item.price}.",
            )
        await self._economy.add_ledger(
            account_id=account.id,
            direction="out",
            amount=item.price,
            reason=reason,
            meta_json=_json({"pet_id": row.id, "item": item.code, "price": item.price, "chat_id": chat_id}),
        )
        return new_balance, None

    # ----- internals (state) -------------------------------------------------

    async def _apply(
        self,
        row: AiPetModel,
        *,
        chat_id: int,
        actor_user_id: int,
        event_type: str,
        effect: m.Effect,
        idempotency_key: str,
        today: date,
        now: datetime,
        extra: dict,
    ) -> ActionResult:
        relation_row = await self._relation_row(pet_id=int(row.id), chat_id=chat_id, user_id=actor_user_id)
        relation = m.RelationState(
            affinity=int(relation_row.affinity),
            affinity_gained_today=int(relation_row.affinity_gained_today),
            xp_gained_today=int(relation_row.xp_gained_today),
            gained_day=relation_row.gained_day,
        )
        outcome = m.apply_effect(_stats(row), relation, effect, today=today)

        _store_stats(row, outcome.stats)
        row.version = int(row.version) + 1
        relation_row.affinity = outcome.relation.affinity
        relation_row.affinity_gained_today = outcome.relation.affinity_gained_today
        relation_row.xp_gained_today = outcome.relation.xp_gained_today
        relation_row.gained_day = outcome.relation.gained_day
        relation_row.interactions = int(relation_row.interactions or 0) + 1
        relation_row.last_interaction_at = now

        self._session.add(
            AiPetEventModel(
                pet_id=row.id,
                chat_id=chat_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                effects={**outcome.applied, **extra},
                idempotency_key=idempotency_key,
                created_at=now,
            )
        )
        if outcome.leveled_up_to is not None:
            self._session.add(
                AiPetEventModel(
                    pet_id=row.id,
                    chat_id=chat_id,
                    actor_user_id=actor_user_id,
                    event_type="level_up",
                    effects={"level": outcome.leveled_up_to},
                    idempotency_key=f"{idempotency_key}:level_up"[:128],
                    created_at=now,
                )
            )
        await self._session.flush()
        return ActionResult(
            status="ok",
            pet=_view(row),
            applied=outcome.applied,
            leveled_up_to=outcome.leveled_up_to,
            affinity=outcome.relation.affinity,
        )

    async def _owner_row(self, owner_user_id: int, *, for_update: bool = False) -> AiPetModel | None:
        stmt = select(AiPetModel).where(AiPetModel.owner_user_id == owner_user_id, AiPetModel.status != "released")
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def _name_taken(self, *, chat_id: int, name_norm: str) -> bool:
        found = await self._session.scalar(
            select(AiPetModel.id).where(
                AiPetModel.current_chat_id == chat_id,
                AiPetModel.name_norm == name_norm,
                AiPetModel.status == "active",
            )
        )
        return found is not None

    async def _locked_pet_here(self, *, pet_id: int, chat_id: int, now: datetime) -> AiPetModel | None:
        row = await self._session.scalar(select(AiPetModel).where(AiPetModel.id == pet_id).with_for_update())
        if row is None:
            return None
        await self._settle(row, now=now)
        if row.status != "active" or row.current_chat_id != chat_id:
            return None
        return row

    async def _settle(self, row: AiPetModel, *, now: datetime) -> None:
        """Lazy upkeep: apply the time tick and rehome a pet whose chat disappeared."""
        if row.status == "released":
            return
        ticked = m.apply_tick(_stats(row), now)
        if ticked != _stats(row):
            _store_stats(row, ticked)
        if row.status == "active" and row.current_chat_id is None:
            home = row.home_chat_id
            if home is not None and await self._pets_enabled(home) and not await self._name_taken(
                chat_id=home, name_norm=row.name_norm
            ):
                row.current_chat_id = home
            else:
                row.status = "dormant"
                row.dormant_reason = "no_home"
        if row.home_chat_id is None and row.current_chat_id is not None:
            row.home_chat_id = row.current_chat_id
        await self._session.flush()

    async def _pets_enabled(self, chat_id: int) -> bool:
        value = await self._session.scalar(
            select(ChatSettingsModel.pets_enabled).where(ChatSettingsModel.chat_id == chat_id)
        )
        return bool(value)

    async def _event_exists(self, idempotency_key: str) -> bool:
        found = await self._session.scalar(
            select(AiPetEventModel.id).where(AiPetEventModel.idempotency_key == idempotency_key)
        )
        return found is not None

    async def _last_event_at(self, *, pet_id: int, actor_user_id: int, event_type: str) -> datetime | None:
        value = await self._session.scalar(
            select(func.max(AiPetEventModel.created_at)).where(
                AiPetEventModel.pet_id == pet_id,
                AiPetEventModel.actor_user_id == actor_user_id,
                AiPetEventModel.event_type == event_type,
            )
        )
        return _as_utc(value) if value is not None else None

    async def _relation_row(self, *, pet_id: int, chat_id: int, user_id: int) -> AiPetRelationshipModel:
        # The pet row is already locked, so creating the relationship cannot race.
        row = await self._session.get(
            AiPetRelationshipModel, {"pet_id": pet_id, "chat_id": chat_id, "user_id": user_id}, with_for_update=True
        )
        if row is not None:
            return row
        await self._economy.ensure_chat_and_user(
            chat=ChatSnapshot(telegram_chat_id=chat_id, chat_type="group", title=None),
            user=UserSnapshot(telegram_user_id=user_id, username=None, first_name=None, last_name=None, is_bot=False),
        )
        row = AiPetRelationshipModel(
            pet_id=pet_id,
            chat_id=chat_id,
            user_id=user_id,
            affinity=0,
            interactions=0,
            affinity_gained_today=0,
            xp_gained_today=0,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _pick_item(self, *, item_code: str | None, kind: str | None, level: int) -> CatalogItem | None:
        if item_code is not None:
            row = await self._session.get(AiPetItemModel, item_code)
            if row is None or not row.enabled:
                return None
            return self._catalog_item(row)
        # No explicit item: the cheapest available one of that kind.
        candidates = [item for item in await self.list_items(level=level) if item.kind == kind]
        candidates.sort(key=lambda item: (item.price, item.code))
        return candidates[0] if candidates else None


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
