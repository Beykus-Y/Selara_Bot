"""Spontaneous pet events, driven by chat activity.

A group message may schedule a check (at most once per chat per
``PET_EVENT_CHECK_SECONDS``, with ``PET_EVENT_CHANCE``, outside quiet hours).
The check runs in its own task and DB session so the message handler is never
slowed down; a claimed event is journaled before the model is asked, and the
line is paid from the pet owner's ``pet_daily`` pool (never from the chat).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import Message

from selara.application.ai_pets import events as ev
from selara.application.feature_access import FeatureAccessService, QuotaScope
from selara.core.config import Settings
from selara.infrastructure.db.ai_pets import AiPetService
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError
from selara.presentation.auth import resolve_owner_private_exemption
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers.pet_billing import pet_reserve_units, settle_pet_line

log = logging.getLogger(__name__)

_GROUP_TYPES = {"group", "supergroup"}
_next_check: dict[int, float] = {}
_running: set[asyncio.Task] = set()


def _zone(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.bot_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def should_check(chat_id: int, *, settings: Settings, now_utc: datetime, rng: random.Random | None = None) -> bool:
    """Cheap in-process gate: throttle per chat, roll the dice, respect quiet hours."""
    monotonic = time.monotonic()
    if _next_check.get(chat_id, 0.0) > monotonic:
        return False
    _next_check[chat_id] = monotonic + settings.pet_event_check_seconds
    if (rng or random).random() >= settings.pet_event_chance:
        return False
    hour = now_utc.astimezone(_zone(settings)).hour
    return not ev.in_quiet_hours(hour, start=settings.pet_event_quiet_start_hour, end=settings.pet_event_quiet_end_hour)


def maybe_schedule_spontaneous_event(
    message: Message,
    *,
    chat_settings,
    settings: Settings,
    session_factory,
    personal_config,
    llm_client: LlmClient | None,
) -> asyncio.Task | None:
    if message.chat.type not in _GROUP_TYPES or session_factory is None:
        return None
    if not getattr(chat_settings, "pets_enabled", False) or not getattr(chat_settings, "pets_spontaneous_enabled", False):
        return None
    user = message.from_user
    if user is None or user.is_bot:
        return None
    if not should_check(message.chat.id, settings=settings, now_utc=datetime.now(timezone.utc)):
        return None
    fallback_name = " ".join(part for part in (user.first_name, user.last_name) if part) or (user.username or "")
    task = asyncio.create_task(
        run_spontaneous_event(
            bot=message.bot,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            person_user_id=user.id,
            person_fallback_name=fallback_name,
            settings=settings,
            session_factory=session_factory,
            personal_config=personal_config,
            llm_client=llm_client,
        ),
        name=f"ai-pet-event-{message.chat.id}",
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


async def run_spontaneous_event(
    *,
    bot,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    person_user_id: int | None,
    person_fallback_name: str,
    settings: Settings,
    session_factory,
    personal_config,
    llm_client: LlmClient | None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> str | None:
    """Claim, phrase and post one event; returns the posted text (or ``None``)."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(_zone(settings))
    day_start = datetime.combine(local.date(), dt_time.min, tzinfo=local.tzinfo).astimezone(timezone.utc)
    try:
        async with session_factory() as session:
            service = AiPetService(session, admin_user_id=settings.admin_user_id)
            claim = await service.claim_spontaneous_event(
                chat_id=chat_id,
                person_user_id=person_user_id,
                now=now,
                day_start=day_start,
                daily_limit=settings.pet_event_daily_limit,
                chat_interval=timedelta(minutes=settings.pet_event_chat_interval_minutes),
                rng=rng,
            )
            person_name = person_fallback_name
            if claim is not None and person_user_id is not None:
                try:
                    person_name = (
                        await SqlAlchemyActivityRepository(session).get_chat_display_name(
                            chat_id=chat_id, user_id=person_user_id
                        )
                        or person_fallback_name
                    )
                except Exception:  # a name is decoration only
                    person_name = person_fallback_name
            await session.commit()
            if claim is None:
                return None

            pet = claim.pet
            idea = ev.pick_event(
                mood=pet.mood,
                satiety=pet.satiety,
                energy=pet.energy,
                person=person_name or None,
                affinity=claim.person_affinity,
                rng=rng,
            )
            owner_has_personal = await service.has_active_personal(user_id=pet.owner_user_id, now=now)
            line = await _phrase(
                owner_has_personal=owner_has_personal,
                claim_event_id=claim.event_id,
                pet=pet,
                idea=idea,
                chat_id=chat_id,
                chat_type=chat_type,
                chat_title=chat_title,
                settings=settings,
                session_factory=session_factory,
                personal_config=personal_config,
                llm_client=llm_client,
            )
            if line is False:
                await service.finish_spontaneous_event(event_id=claim.event_id, status="skipped_quota")
                await session.commit()
                return None
            text = line or ev.template_line(name=pet.name, species_key=pet.species_key, idea=idea)
            await bot.send_message(chat_id, escape(text), parse_mode="HTML", disable_notification=True)
            await service.finish_spontaneous_event(event_id=claim.event_id, status="posted", text=text)
            await session.commit()
            return text
    except Exception:
        log.exception("ai pet spontaneous event failed chat_id=%s", chat_id)
        return None


async def _phrase(
    *,
    owner_has_personal: bool,
    claim_event_id: int,
    pet,
    idea: ev.EventIdea,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    settings: Settings,
    session_factory,
    personal_config,
    llm_client: LlmClient | None,
) -> str | None | bool:
    """The model's line, ``None`` to use the template, or ``False`` when the owner's pool is spent."""
    if llm_client is None or not owner_has_personal:
        return None  # free template: only an owner with Selara Personal pays for a model line
    access = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(
            session_factory, personal_config, pet_daily_limit=settings.pet_talk_daily_limit
        ),
        personal_config=personal_config,
    )
    decision = await access.reserve_feature_usage(
        feature=AiFeature.PET_EVENT_TEXT,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        scope=QuotaScope.user(pet.owner_user_id),
        owner_exempt=resolve_owner_private_exemption(user_id=pet.owner_user_id, admin_user_id=settings.admin_user_id),
        actor_user_id=None,
        trigger="spontaneous",
        timezone_name=settings.bot_timezone,
        idempotency_key=f"pet_event_text:{chat_id}:{claim_event_id}",
        units=pet_reserve_units(settings),
    )
    if not decision.allowed:
        return False
    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id
    context = (
        LlmAccountingContext(
            invocation_id=invocation_id, feature=AiFeature.PET_EVENT_TEXT, stage="pet_event_text", chat_id=chat_id
        )
        if invocation_id is not None
        else None
    )
    outcome = {"status": "failed", "error_category": "empty_answer"}
    line_usages: list = []
    try:
        kwargs: dict = {"max_tokens": ev.MAX_EVENT_TOKENS}
        if context is not None:
            kwargs["accounting_context"] = context
        result = await llm_client.chat_simple(
            ev.build_event_messages(
                name=pet.name,
                species_title=pet.species_title,
                traits=pet.traits,
                character_custom=pet.character_custom,
                idea=idea,
            ),
            **kwargs,
        )
        line_usages.extend(getattr(result, "usages", ()) or ())
        value = result.value if hasattr(result, "value") else result
        line = ev.clean_event_line(value or "")
        if line:
            outcome = {"status": "succeeded", "error_category": None}
            return line
        return None
    except LlmClientError as exc:
        outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
        line_usages.extend(exc.usages)
        return None
    finally:
        if line_usages and personal_config is not None:
            try:
                await settle_pet_line(
                    access,
                    config=await personal_config.get(),
                    decision=decision,
                    usages=line_usages,
                    failed=outcome["status"] != "succeeded",
                )
            except Exception:
                log.exception("Could not settle pet event AIL invocation_id=%s", invocation_id)
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused pet event quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id, status=outcome["status"], error_category=outcome["error_category"]
                )
            except Exception:
                log.exception("Could not finalize pet_event_text invocation id=%s", invocation_id)
