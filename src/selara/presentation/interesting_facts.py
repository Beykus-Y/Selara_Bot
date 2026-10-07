from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatInterestingFactState, ChatSnapshot
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.game_state import GAME_STORE

logger = logging.getLogger(__name__)

_MISSING_FILE_SIGNATURE = (-1, -1)
_SCHEDULER_INTERVAL_SECONDS = 600
_CLAIM_LEASE_SECONDS = 300
# A claim cools the chat down unless Telegram definitively rejected it. Ambiguous outcomes
# (crash, DB error after send, lease expiry) therefore never re-send the same slot.
_COOLDOWN_CLAIM_STATUSES = ("claimed", "sent", "abandoned")
# Telegram answered without delivering the message, so the slot may be retried. Anything else
# (network error, timeout, 5xx) may have delivered it and must not be retried.
_DEFINITE_SEND_REJECTIONS = (TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat, TelegramRetryAfter)


@dataclass(frozen=True)
class InterestingFact:
    fact_id: str
    text: str


@dataclass(frozen=True)
class _FactClaim:
    claim_id: int
    chat: ChatSnapshot
    fact: InterestingFact
    claimed_at: datetime
    used_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class InterestingFactEligibility:
    eligible: bool
    reason: str


def _normalize_fact_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def build_interesting_fact_id(text: str) -> str:
    normalized = _normalize_fact_text(text).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"fact_{digest[:16]}"


def parse_interesting_facts(raw: object) -> tuple[InterestingFact, ...]:
    if not isinstance(raw, list):
        return ()

    result: list[InterestingFact] = []
    seen: set[str] = set()
    for item in raw:
        text = _normalize_fact_text(str(item or ""))
        if not text:
            continue
        dedupe_key = text.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(InterestingFact(fact_id=build_interesting_fact_id(text), text=text))
    return tuple(result)


class InterestingFactCatalog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(__file__).with_name("interesting_facts.json")
        self._signature: tuple[int, int] | None = None
        self._facts: tuple[InterestingFact, ...] = ()

    def get_facts(self) -> tuple[InterestingFact, ...]:
        try:
            stat = self._path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            signature = _MISSING_FILE_SIGNATURE

        if signature == self._signature:
            return self._facts

        if signature == _MISSING_FILE_SIGNATURE:
            self._signature = signature
            self._facts = ()
            return self._facts

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            facts = parse_interesting_facts(raw)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to reload interesting facts catalog from %s", self._path)
            self._signature = signature
            return self._facts

        self._signature = signature
        self._facts = facts
        return self._facts


INTERESTING_FACT_CATALOG = InterestingFactCatalog()


def select_next_interesting_fact(
    *,
    facts: tuple[InterestingFact, ...],
    state: ChatInterestingFactState | None,
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[InterestingFact | None, tuple[str, ...]]:
    if not facts:
        return None, ()

    current_rng = rng or random.SystemRandom()
    fact_ids = {item.fact_id for item in facts}
    used_ids = [fact_id for fact_id in (state.used_fact_ids if state is not None else ()) if fact_id in fact_ids]
    available = [fact for fact in facts if fact.fact_id not in used_ids]
    if not available:
        used_ids = []
        available = list(facts)
        last_fact_id = state.last_fact_id if state is not None else None
        if last_fact_id and len(available) > 1:
            available = [fact for fact in available if fact.fact_id != last_fact_id] or list(facts)

    selected = current_rng.choice(available)
    next_used_ids = tuple([*used_ids, selected.fact_id])
    return selected, next_used_ids


def evaluate_interesting_fact_eligibility(
    *,
    now: datetime,
    settings: ChatSettings,
    state: ChatInterestingFactState | None,
    has_facts: bool,
    has_active_game: bool,
    messages_since_reference: int,
    messages_in_interval: int,
    messages_in_sleep_cap: int,
) -> InterestingFactEligibility:
    if not has_facts:
        return InterestingFactEligibility(False, "catalog_empty")
    if has_active_game:
        return InterestingFactEligibility(False, "active_game")
    if settings.antiraid_enabled:
        return InterestingFactEligibility(False, "antiraid_enabled")
    if settings.chat_write_locked:
        return InterestingFactEligibility(False, "chat_write_locked")

    interval_delta = timedelta(minutes=settings.interesting_facts_interval_minutes)
    last_sent_at = state.last_sent_at if state is not None else None
    if last_sent_at is not None and now - last_sent_at < interval_delta:
        return InterestingFactEligibility(False, "cooldown")
    if messages_in_sleep_cap <= 0:
        return InterestingFactEligibility(False, "sleep_cap")
    if messages_since_reference >= settings.interesting_facts_target_messages:
        return InterestingFactEligibility(True, "target_messages")
    if messages_in_interval <= 0:
        return InterestingFactEligibility(True, "quiet_interval")
    return InterestingFactEligibility(False, "not_enough_messages")


def format_interesting_fact_message(text: str) -> str:
    return f"Интересный факт:\n\n{text}"


class InterestingFactsScheduler:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: InterestingFactCatalog | None = None,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._catalog = catalog or INTERESTING_FACT_CATALOG

    def get_facts(self) -> tuple[InterestingFact, ...]:
        return self._catalog.get_facts()

    async def run_once(self, *, now: datetime | None = None) -> int:
        facts = self.get_facts()
        if not facts:
            return 0

        normalized_now = now or datetime.now(timezone.utc)
        if normalized_now.tzinfo is None:
            normalized_now = normalized_now.replace(tzinfo=timezone.utc)
        else:
            normalized_now = normalized_now.astimezone(timezone.utc)

        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chats = await repo.list_chats_with_interesting_facts_enabled()

        sent_count = 0
        for chat in chats:
            try:
                if await self._process_chat(chat=chat, facts=facts, now=normalized_now):
                    sent_count += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Interesting fact dispatch failed", extra={"chat_id": chat.telegram_chat_id})
        return sent_count

    async def _process_chat(
        self,
        *,
        chat: ChatSnapshot,
        facts: tuple[InterestingFact, ...],
        now: datetime,
    ) -> bool:
        active_game = await GAME_STORE.get_active_game_for_chat(chat_id=chat.telegram_chat_id)
        claim = await self._claim_next_fact(
            chat=chat,
            facts=facts,
            now=now,
            has_active_game=active_game is not None,
        )
        if claim is None:
            return False

        try:
            sent_message = await self._bot.send_message(
                chat_id=chat.telegram_chat_id,
                text=format_interesting_fact_message(claim.fact.text),
                disable_web_page_preview=True,
            )
        except asyncio.CancelledError:
            # Left as "claimed": it cools the chat down and is abandoned once its lease expires.
            raise
        except Exception as exc:
            if isinstance(exc, _DEFINITE_SEND_REJECTIONS):
                logger.exception("Failed to send interesting fact", extra={"chat_id": chat.telegram_chat_id})
                await self._finish_claim(claim, status="failed", error_summary=type(exc).__name__)
            else:
                # Outcome unknown: the claim stays "claimed" and cools the chat down until it is abandoned.
                logger.exception(
                    "Interesting fact send outcome unknown; slot stays claimed",
                    extra={"chat_id": chat.telegram_chat_id, "claim_id": claim.claim_id},
                )
            return False

        message_id = getattr(sent_message, "message_id", None)
        await self._finish_claim(
            claim,
            status="sent",
            telegram_message_id=message_id if isinstance(message_id, int) else None,
        )
        return True

    async def _claim_next_fact(
        self,
        *,
        chat: ChatSnapshot,
        facts: tuple[InterestingFact, ...],
        now: datetime,
        has_active_game: bool,
    ) -> _FactClaim | None:
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            # Held until commit: concurrent replicas decide and claim one slot per chat in turn.
            await repo.lock_chat_interesting_fact_dispatch(chat_id=chat.telegram_chat_id)
            abandoned = await repo.abandon_expired_interesting_fact_claims(chat_id=chat.telegram_chat_id, now=now)
            if abandoned:
                logger.warning(
                    "Interesting fact claims expired without a recorded outcome",
                    extra={"chat_id": chat.telegram_chat_id, "abandoned_count": abandoned},
                )

            settings = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)
            if settings is None or not settings.interesting_facts_enabled:
                await session.commit()
                return None

            stored_state = await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id)
            last_claimed_at = await repo.get_latest_interesting_fact_claim_at(
                chat_id=chat.telegram_chat_id,
                statuses=_COOLDOWN_CLAIM_STATUSES,
            )
            state = _cooldown_state(stored_state, chat_id=chat.telegram_chat_id, last_claimed_at=last_claimed_at)

            interval_delta = timedelta(minutes=settings.interesting_facts_interval_minutes)
            sleep_cap_delta = timedelta(minutes=settings.interesting_facts_sleep_cap_minutes)
            interval_start = now - interval_delta
            sleep_cap_start = now - sleep_cap_delta
            reference_start = state.last_sent_at if state.last_sent_at is not None else interval_start

            messages_in_sleep_cap = await repo.count_human_messages_since(
                chat_id=chat.telegram_chat_id,
                since=sleep_cap_start,
            )
            messages_in_interval = await repo.count_human_messages_since(
                chat_id=chat.telegram_chat_id,
                since=interval_start,
            )
            messages_since_reference = (
                messages_in_interval
                if reference_start == interval_start
                else await repo.count_human_messages_since(chat_id=chat.telegram_chat_id, since=reference_start)
            )

            eligibility = evaluate_interesting_fact_eligibility(
                now=now,
                settings=settings,
                state=state,
                has_facts=bool(facts),
                has_active_game=has_active_game,
                messages_since_reference=messages_since_reference,
                messages_in_interval=messages_in_interval,
                messages_in_sleep_cap=messages_in_sleep_cap,
            )
            fact, next_used_ids = (None, ())
            if eligibility.eligible:
                fact, next_used_ids = select_next_interesting_fact(facts=facts, state=state)
            if fact is None:
                await session.commit()
                return None

            claim_id = await repo.create_interesting_fact_claim(
                chat_id=chat.telegram_chat_id,
                fact_id=fact.fact_id,
                claimed_at=now,
                lease_until=now + timedelta(seconds=_CLAIM_LEASE_SECONDS),
            )
            await session.commit()

        return _FactClaim(
            claim_id=claim_id,
            chat=chat,
            fact=fact,
            claimed_at=now,
            used_fact_ids=next_used_ids,
        )

    async def _finish_claim(
        self,
        claim: _FactClaim,
        *,
        status: str,
        telegram_message_id: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        try:
            async with self._session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                if status == "sent":
                    await repo.upsert_chat_interesting_fact_state(
                        chat=claim.chat,
                        last_sent_at=claim.claimed_at,
                        last_fact_id=claim.fact.fact_id,
                        used_fact_ids=claim.used_fact_ids,
                    )
                finished = await repo.finish_interesting_fact_claim(
                    claim_id=claim.claim_id,
                    status=status,
                    finished_at=datetime.now(timezone.utc),
                    telegram_message_id=telegram_message_id,
                    error_summary=error_summary,
                )
                await session.commit()
            if not finished:
                logger.warning(
                    "Interesting fact claim was no longer claimed when its outcome was recorded",
                    extra={"chat_id": claim.chat.telegram_chat_id, "claim_id": claim.claim_id},
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The claim stays "claimed" and cools the chat down until its lease expires, so it is not re-sent.
            logger.exception(
                "Failed to record interesting fact outcome",
                extra={"chat_id": claim.chat.telegram_chat_id, "claim_id": claim.claim_id, "status": status},
            )


def _cooldown_state(
    state: ChatInterestingFactState | None,
    *,
    chat_id: int,
    last_claimed_at: datetime | None,
) -> ChatInterestingFactState:
    last_sent_at = state.last_sent_at if state is not None else None
    if last_claimed_at is not None and (last_sent_at is None or last_claimed_at > last_sent_at):
        last_sent_at = last_claimed_at
    return ChatInterestingFactState(
        chat_id=chat_id,
        last_sent_at=last_sent_at,
        last_fact_id=state.last_fact_id if state is not None else None,
        used_fact_ids=state.used_fact_ids if state is not None else (),
    )


async def run_interesting_facts_scheduler(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scheduler = InterestingFactsScheduler(bot=bot, session_factory=session_factory)
    while True:
        await asyncio.sleep(_SCHEDULER_INTERVAL_SECONDS)
        try:
            await scheduler.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Interesting facts scheduler iteration failed")
