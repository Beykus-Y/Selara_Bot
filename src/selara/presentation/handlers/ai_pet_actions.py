"""Custom pet actions: ``/pet_do <what you do>``, open to everyone in the chat, paid by the pet owner's Selara Personal.

The model narrates and classifies; the effect, the cooldown and the daily cap are code (``custom_action.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape

from aiogram.types import Message

from selara.application.ai_pets import custom_action as ca
from selara.application.ai_pets import dialogue as d
from selara.application.ai_pets import mechanics as m
from selara.application.ai_pets import personality
from selara.application.feature_access import (
    AccessReason,
    FeatureAccessService,
    QuotaScope,
    message_idempotency_key,
)
from selara.core.config import Settings
from selara.infrastructure.db.ai_pets import AiPetService, PetView
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.ai_pets import generate_custom_action
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import resolve_owner_private_exemption
from selara.presentation.handlers.ai_pet_talk import _day_start, _display_name, _plain_name, _zone
from selara.presentation.handlers.pet_billing import pet_reserve_units, settle_pet_line

log = logging.getLogger(__name__)

NEEDS_PERSONAL_TEXT = (
    "Свои действия питомец понимает, когда у хозяина есть Selara Personal. "
    "Без неё доступны обычные: пет погладить, поиграть, покормить."
)
USAGE_TEXT = "Формат: <code>/pet_do чешу за ухом</code> или <code>/pet_do Мурка учу давать лапу</code>."


def pick_pet(pets: list[PetView], raw: str) -> tuple[PetView | None, str]:
    """The pet named at the start of the text, or the only pet in the chat; ``(None, text)`` if it is unclear."""
    text = (raw or "").strip()
    named = d.find_addressed_pet(text, [(pet.id, pet.name) for pet in pets]) if text else None
    if named is not None:
        pet = next(pet for pet in pets if pet.id == named[0])
        rest = named[1]
        # find_addressed_pet returns the whole text when nothing follows the name.
        return pet, "" if rest == text else rest
    if len(pets) == 1:
        return pets[0], text
    return None, text


async def handle_pet_custom_action(
    message: Message,
    *,
    raw_args: str,
    activity_repo,
    db_session,
    economy_repo,
    settings: Settings,
    session_factory,
    personal_config,
    llm_client: LlmClient | None,
) -> None:
    from selara.presentation.handlers import ai_pets  # lazy: ai_pets registers this handler

    user = message.from_user
    if user is None or user.is_bot:
        return
    chat_id = message.chat.id
    now = datetime.now(timezone.utc)
    service = AiPetService(db_session, economy_repo, admin_user_id=settings.admin_user_id)

    pets = await service.list_chat_pets(chat_id=chat_id)
    if not pets:
        await message.answer("В этом чате пока нет питомцев. Завести своего: <code>/pet_new кот Мурка</code>.", parse_mode="HTML")
        return
    pet, text = pick_pet(pets, raw_args)
    if pet is None:
        names = ", ".join(escape(p.name) for p in pets)
        await message.answer(f"В чате несколько питомцев ({names}). Укажите имя: <code>/pet_do Мурка чешу за ухом</code>.", parse_mode="HTML")
        return
    try:
        action_text = ca.validate_action_text(text)
    except m.PetValidationError as exc:
        await message.answer(f"{escape(str(exc))}\n{USAGE_TEXT}", parse_mode="HTML")
        return
    pet = await service.current_view(pet_id=pet.id, now=now) or pet
    owner_id = pet.owner_user_id
    if not await service.has_active_personal(user_id=owner_id, now=now):
        await message.reply(NEEDS_PERSONAL_TEXT)
        return
    if llm_client is None:
        await message.reply(escape(f"{pet.emoji} {pet.name} сейчас не может ничего придумать."), parse_mode="HTML")
        return

    daily_limit = (
        settings.pet_custom_actions_daily_limit
        if user.id == owner_id
        else settings.pet_custom_actions_guest_daily_limit
    )
    status, gate_message = await service.custom_action_gate(
        pet_id=pet.id, chat_id=chat_id, actor_user_id=user.id, now=now,
        day_start=_day_start(settings, now), daily_limit=daily_limit,
    )
    if status != "ok":
        await message.reply(escape(f"{pet.emoji} {gate_message}"), parse_mode="HTML")
        return

    affinity = await service.relation_affinity(pet_id=pet.id, chat_id=chat_id, user_id=user.id)
    actor_name = await _display_name(activity_repo, chat_id=chat_id, user_id=user.id, fallback=_plain_name(user))
    local_day = now.astimezone(_zone(settings)).date()
    messages = ca.build_action_messages(
        name=pet.name,
        species_title=pet.species_title,
        traits=pet.traits,
        character_custom=pet.character_custom,
        mood_label=m.mood_label(pet.mood),
        mood_of_day=personality.mood_of_the_day(pet_id=pet.id, day=local_day, mood=pet.mood, traits=pet.traits),
        actor_name=actor_name,
        attitude=m.affinity_label(affinity),
        action_text=action_text,
    )
    await db_session.commit()

    access_service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(
            session_factory, personal_config, pet_daily_limit=settings.pet_talk_daily_limit
        ),
        personal_config=personal_config,
    )
    try:
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.PET_ACTION,
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
                feature=AiFeature.PET_ACTION, chat_id=chat_id, source_message_id=message.message_id
            ),
            source_message_id=message.message_id,
            units=pet_reserve_units(settings),
        )
    except Exception:
        log.exception("Pet action quota reservation failed message_id=%s", message.message_id)
        return
    if decision.reused:
        return
    if not decision.allowed:
        if decision.reason == AccessReason.QUOTA_EXHAUSTED:
            await message.reply(escape(f"{pet.emoji} {pet.name} наигрался(ась) на сегодня. Завтра придумаем новое!"), parse_mode="HTML")
        else:
            await message.reply(NEEDS_PERSONAL_TEXT)
        return

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id
    context = (
        LlmAccountingContext(
            invocation_id=invocation_id, feature=AiFeature.PET_ACTION, stage="pet_action", chat_id=chat_id,
            actor_user_id=user.id, telegram_message_id=message.message_id,
        )
        if invocation_id is not None
        else None
    )
    outcome = {"status": "failed", "error_category": "handler_error"}
    line_usages: list = []
    try:
        try:
            raw = await generate_custom_action(
                llm_client=llm_client, messages=messages, accounting_context=context, usages_out=line_usages
            )
        except LlmClientError as exc:
            outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
            line_usages.extend(exc.usages)
            raw = ""
        except Exception:
            log.exception("pet action: LLM request failed before reaching the provider")
            outcome["error_category"] = "accounting_unavailable"
            raw = ""
        if not raw:
            if outcome["error_category"] == "handler_error":
                outcome["error_category"] = "empty_answer"
            await message.reply(escape(f"{pet.emoji} {pet.name} задумчиво молчит."), parse_mode="HTML")
            return
        outcome["status"] = "succeeded"
        outcome["error_category"] = None

        verdict = ca.parse_verdict(raw)
        result = await service.perform_custom_action(
            pet_id=pet.id,
            chat_id=chat_id,
            actor_user_id=user.id,
            class_key=verdict.class_key,
            narration=verdict.narration,
            action_text=action_text,
            idempotency_key=f"ai_pet_action:{chat_id}:{message.message_id}",
            today=local_day,
            now=now,
        )
        await db_session.commit()
        if result.status == "duplicate":
            return
        if result.status in ("blocked", "unavailable"):
            await message.reply(escape(f"{pet.emoji} {result.message}"), parse_mode="HTML")
            return
        if result.status == "refused":
            line = verdict.narration or ca.refusal_line(name=pet.name, species_key=pet.species_key)
            await message.reply(f"{pet.emoji} {escape(line)}" if verdict.narration else escape(line), parse_mode="HTML")
            return
        lines = [f"{ai_pets._actor_link(user)}: {pet.emoji} {escape(verdict.narration)}"]
        effects = ai_pets._effects_line(result.applied)
        if effects:
            lines.append(effects)
        if result.leveled_up_to is not None:
            lines.append(f"🎉 {escape(pet.name)} достигает {result.leveled_up_to} уровня!")
        await message.reply("\n".join(lines), parse_mode="HTML")
    finally:
        if line_usages and personal_config is not None:
            try:
                await settle_pet_line(
                    access_service,
                    config=await personal_config.get(),
                    decision=decision,
                    usages=line_usages,
                    failed=outcome["status"] != "succeeded",
                )
            except Exception:
                log.exception("Could not settle pet action AIL invocation_id=%s", invocation_id)
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused pet action quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id, status=outcome["status"], error_category=outcome["error_category"]
                )
            except Exception:
                log.exception("Could not finalize pet_action invocation id=%s", invocation_id)
