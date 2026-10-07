"""Talking to an AI pet in a group: «Мурка, как дела?» or a reply to the pet's line.

Who pays: always the pet's owner (pool ``pet_daily`` of their Selara Personal),
never the chat. Other people get a capped share of that pool, counted under the
pet row lock. The model call happens after the admission transaction commits,
so no connection or row lock is held while the provider answers.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import Message

from selara.application.ai_pets import dialogue as d
from selara.application.ai_pets import mechanics as m
from selara.application.feature_access import (
    AccessReason,
    FeatureAccessService,
    QuotaScope,
    message_idempotency_key,
)
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.infrastructure.db.ai_pet_dialogue import AiPetDialogueRepository
from selara.infrastructure.db.ai_pets import AiPetService, PetView
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.ai_pets import extract_notes, generate_pet_reply
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import has_command_access, resolve_owner_private_exemption

log = logging.getLogger(__name__)

_GROUP_TYPES = {"group", "supergroup"}
_NAMES_TTL_SECONDS = 30.0
# chat_id -> (expires_at, [(pet_id, name)]): name matching runs on every message of a pets-enabled chat.
_names_cache: dict[int, tuple[float, list[tuple[int, str]]]] = {}


def invalidate_pet_names(chat_id: int | None) -> None:
    if chat_id is not None:
        _names_cache.pop(chat_id, None)


async def _chat_pet_names(service: AiPetService, chat_id: int) -> list[tuple[int, str]]:
    cached = _names_cache.get(chat_id)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    names = [(pet.id, pet.name) for pet in await service.list_chat_pets(chat_id=chat_id)]
    _names_cache[chat_id] = (now + _NAMES_TTL_SECONDS, names)
    return names


async def resolve_talk_target(
    message: Message, *, chat_settings: ChatSettings, db_session, economy_repo
) -> tuple[int, str] | None:
    """Return ``(pet_id, text_for_the_pet)`` when this group message speaks to a pet."""
    if message.chat.type not in _GROUP_TYPES or not getattr(chat_settings, "pets_enabled", False):
        return None
    user = message.from_user
    text = (message.text or "").strip()
    if user is None or user.is_bot or not text or text.startswith("/"):
        return None
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and reply.from_user.is_bot:
        pet_id = await AiPetDialogueRepository(db_session).pet_for_reply(
            chat_id=message.chat.id, telegram_message_id=reply.message_id
        )
        if pet_id is not None:
            return pet_id, text
    names = await _chat_pet_names(AiPetService(db_session, economy_repo), message.chat.id)
    if not names:
        return None
    return d.find_addressed_pet(text, names)


async def talk_allowed(message: Message, activity_repo) -> bool:
    """The «pet» rank rule, checked silently: talking is natural text, not a command to refuse loudly."""
    user = message.from_user
    allowed, _, _, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        command_key="pet",
        bootstrap_if_missing_owner=False,
    )
    return allowed


def _day_start(settings: Settings, now: datetime) -> datetime:
    try:
        zone = ZoneInfo(settings.bot_timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    return datetime.combine(local.date(), dt_time.min, tzinfo=zone).astimezone(timezone.utc)


def _plain_name(user) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part) or (user.username or "Собеседник")


async def _display_name(activity_repo, *, chat_id: int, user_id: int, fallback: str) -> str:
    try:
        name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    except Exception:  # names are decoration; never fail a talk over them
        name = None
    return name or fallback


async def _build_context(
    *, repo: AiPetDialogueRepository, activity_repo, pet: PetView, chat_id: int, speaker_id: int,
    speaker_name: str, affinity: int, now: datetime, outfit: tuple[str, ...] = (),
) -> d.PetContext:
    names: dict[int, str] = {speaker_id: speaker_name}

    async def name_of(user_id: int | None) -> str:
        if user_id is None:
            return "Кто-то"
        if user_id not in names:
            names[user_id] = await _display_name(activity_repo, chat_id=chat_id, user_id=user_id, fallback="Кто-то")
        return names[user_id]

    aggregates = [
        (await name_of(actor), event_type, count)
        for actor, event_type, count in await repo.weekly_aggregates(pet_id=pet.id, chat_id=chat_id, now=now)
    ]
    recent = [
        d.DialogueTurn(
            speaker=pet.name if row.role == "assistant" else await name_of(row.author_user_id),
            role=row.role,
            content=row.content,
        )
        for row in await repo.recent(pet_id=pet.id, chat_id=chat_id, limit=d.RECENT_MESSAGES)
    ]
    return d.PetContext(
        persona=d.PetPersona(
            name=pet.name,
            species_title=pet.species_title,
            traits=pet.traits,
            character_custom=pet.character_custom,
            level=pet.level,
            mood=pet.mood,
            satiety=pet.satiety,
            energy=pet.energy,
            outfit=outfit,
        ),
        speaker_name=speaker_name,
        speaker_is_owner=speaker_id == pet.owner_user_id,
        speaker_attitude=m.affinity_label(affinity),
        aggregates=d.aggregate_lines(aggregates),
        notes=await repo.notes(pet_id=pet.id, chat_id=chat_id),
        recent=recent,
    )


async def handle_pet_talk(
    message: Message,
    *,
    pet_id: int,
    talk_text: str,
    activity_repo,
    db_session,
    economy_repo,
    settings: Settings,
    session_factory,
    personal_config,
    llm_client: LlmClient | None,
) -> None:
    user = message.from_user
    chat_id = message.chat.id
    now = datetime.now(timezone.utc)
    service = AiPetService(db_session, economy_repo, admin_user_id=settings.admin_user_id)
    repo = AiPetDialogueRepository(db_session)

    pet = await service.current_view(pet_id=pet_id, now=now)
    if pet is None or pet.status != "active" or pet.current_chat_id != chat_id:
        return
    if len(talk_text) > d.MAX_TALK_TEXT_LEN:
        await message.reply(f"{pet.emoji} {escape(pet.name)} не дослушал(а): слишком длинно. До {d.MAX_TALK_TEXT_LEN} символов.")
        return

    admission = await repo.admit_talk(
        pet_id=pet.id,
        chat_id=chat_id,
        author_user_id=user.id,
        content=talk_text,
        idempotency_key=f"ai_pet_talk:{chat_id}:{message.message_id}",
        telegram_message_id=message.message_id,
        day_start=_day_start(settings, now),
        guests_limit=settings.pet_talk_guests_daily_limit,
        guest_limit=settings.pet_talk_guest_daily_limit,
        now=now,
    )
    if admission.status in ("duplicate", "unavailable", "cooldown"):
        # A cooldown is silent on purpose: answering it would let anyone spam the chat through the pet.
        return
    if admission.status in ("guests_exhausted", "guest_exhausted"):
        await message.reply(
            f"{pet.emoji} {escape(pet.name)} устал(а) болтать с гостями сегодня. Можно погладить или угостить — это без лимита.",
            parse_mode="HTML",
        )
        return
    message_row_id = admission.message_id
    owner_id = int(admission.owner_user_id)

    if llm_client is None or not await service.has_active_personal(user_id=owner_id, now=now):
        await repo.set_status(message_id=message_row_id, status="failed")
        await repo.commit()
        text = (
            d.offline_reply(name=pet.name, species_key=pet.species_key)
            if llm_client is not None
            else f"{pet.emoji} {pet.name} сейчас не может разговаривать."
        )
        await message.reply(escape(text), parse_mode="HTML")
        return

    affinity = await service.relation_affinity(pet_id=pet.id, chat_id=chat_id, user_id=user.id)
    speaker_name = await _display_name(activity_repo, chat_id=chat_id, user_id=user.id, fallback=_plain_name(user))
    context = await _build_context(
        repo=repo, activity_repo=activity_repo, pet=pet, chat_id=chat_id, speaker_id=user.id,
        speaker_name=speaker_name, affinity=affinity, now=now, outfit=tuple(await service.outfit(pet_id=pet.id)),
    )
    # Admission and context are settled: release the pet row lock before quota and the provider call.
    await repo.commit()

    access_service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(
            session_factory, personal_config, pet_daily_limit=settings.pet_talk_daily_limit
        ),
        personal_config=personal_config,
    )
    try:
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.PET_TALK,
            chat_id=chat_id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            scope=QuotaScope.user(owner_id),
            owner_exempt=resolve_owner_private_exemption(user_id=owner_id, admin_user_id=settings.admin_user_id),
            actor_user_id=user.id,
            actor_is_bot=False,
            trigger="telegram_message",
            timezone_name=settings.bot_timezone,
            idempotency_key=message_idempotency_key(
                feature=AiFeature.PET_TALK, chat_id=chat_id, source_message_id=message.message_id
            ),
            source_message_id=message.message_id,
        )
    except Exception:
        log.exception("Pet talk quota reservation failed message_id=%s", message.message_id)
        await repo.set_status(message_id=message_row_id, status="failed")
        await repo.commit()
        return

    if decision.reused:
        return
    if not decision.allowed:
        await repo.set_status(message_id=message_row_id, status="failed")
        await repo.commit()
        if decision.reason == AccessReason.QUOTA_EXHAUSTED and decision.access_tier.value == "paid":
            text = f"{pet.emoji} {pet.name} наговорился(ась) на сегодня и сладко зевает. Завтра поболтаем!"
        else:
            text = d.offline_reply(name=pet.name, species_key=pet.species_key)
        await message.reply(escape(text), parse_mode="HTML")
        return

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id

    def _context(feature: AiFeature, stage: str) -> LlmAccountingContext | None:
        if invocation_id is None:
            return None
        return LlmAccountingContext(
            invocation_id=invocation_id,
            feature=feature,
            stage=stage,
            chat_id=chat_id,
            actor_user_id=user.id,
            telegram_message_id=message.message_id,
        )

    outcome = {"status": "failed", "error_category": "handler_error"}
    try:
        try:
            answer = await generate_pet_reply(
                llm_client=llm_client,
                messages=d.build_pet_messages(context, user_text=talk_text),
                accounting_context=_context(AiFeature.PET_TALK, "pet_talk"),
            )
        except LlmClientError as exc:
            outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
            answer = ""
        except Exception:
            log.exception("pet talk: LLM request failed before reaching the provider")
            outcome["error_category"] = "accounting_unavailable"
            answer = ""

        if not answer:
            if outcome["error_category"] == "handler_error":
                outcome["error_category"] = "empty_answer"
            await repo.set_status(message_id=message_row_id, status="failed")
            await repo.commit()
            await message.reply(escape(f"{pet.emoji} {pet.name} задумчиво молчит."), parse_mode="HTML")
            return

        await repo.set_status(message_id=message_row_id, status="ok")
        talks_total = await repo.record_talk(
            pet_id=pet.id, chat_id=chat_id, author_user_id=user.id,
            idempotency_key=f"ai_pet_talk_done:{chat_id}:{message.message_id}", now=now,
        )
        reply_row_id = await repo.add_reply(pet_id=pet.id, chat_id=chat_id, content=answer, now=datetime.now(timezone.utc))
        await repo.prune_history(pet_id=pet.id, chat_id=chat_id, now=now)
        await repo.commit()
        outcome["status"] = "succeeded"
        outcome["error_category"] = None

        sent = await message.reply(f"{pet.emoji} <b>{escape(pet.name)}</b>: {escape(answer)}", parse_mode="HTML")
        await repo.set_reply_message_id(row_id=reply_row_id, telegram_message_id=sent.message_id)
        await repo.commit()

        try:
            if talks_total % d.EXTRACT_EVERY_TALKS == 0:
                turns = [
                    *context.recent,
                    d.DialogueTurn(speaker=speaker_name, role="user", content=talk_text),
                    d.DialogueTurn(speaker=pet.name, role="assistant", content=answer),
                ]
                await repo.commit()
                notes = await extract_notes(
                    llm_client=llm_client,
                    turns=turns,
                    accounting_context=_context(AiFeature.PET_MEMORY_EXTRACT, "pet_memory_extract"),
                )
                if notes:
                    await repo.add_notes(pet_id=pet.id, chat_id=chat_id, notes=notes, now=datetime.now(timezone.utc))
                    await repo.commit()
        except Exception:
            log.exception("pet talk: note extraction crashed pet_id=%s", pet.id)
    finally:
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused pet talk quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id,
                    status=outcome["status"],
                    error_category=outcome["error_category"],
                )
            except Exception:
                log.exception("Could not finalize pet_talk invocation id=%s", invocation_id)
