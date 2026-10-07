"""Owner-granted subscriptions: rules, service, owner commands and the Mini App API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application import entitlement_grants as eg
from selara.application.feature_access import AccessTier, PersonalQuotaLimits
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider
from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY, SELARA_PERSONAL_PRODUCT_KEY
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.entitlement_grants import EntitlementGrantService
from selara.infrastructure.db.models import (
    ChatEntitlementModel,
    ChatModel,
    EntitlementGrantModel,
    SelaraAiPaymentModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers import admin_grants as handlers
from selara.web.miniapp_admin import build_miniapp_admin_router

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
OWNER = 77
PERSON = 4101
CHAT = -1004101


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


# ----- rules and grammar ---------------------------------------------------------------------------


def test_limits_reason_key_and_targets_are_validated() -> None:
    assert eg.validate_days(365) == 365
    for bad in (0, 366, -1, "30", True, 1.5):
        with pytest.raises(eg.GrantError):
            eg.validate_days(bad)
    assert eg.validate_reason("  подарок   за помощь ") == "подарок за помощь"
    for bad in ("", "   ", "x" * 301, "api_key=sk-123456", "token: abcdef"):
        with pytest.raises(eg.GrantError):
            eg.validate_reason(bad)
    assert eg.validate_key("cmd:1:2") == "cmd:1:2"
    for bad in ("", "a b", "x" * 65):
        with pytest.raises(eg.GrantError):
            eg.validate_key(bad)
    assert eg.validate_target("user", 5, admin_user_id=1) == ("user", 5)
    assert eg.validate_target("chat", -5) == ("chat", -5)
    for scope, target in (("user", 0), ("user", -3), ("chat", 5), ("team", 1), ("user", "5"), ("user", 1)):
        with pytest.raises(eg.GrantError):
            eg.validate_target(scope, target, admin_user_id=1)


def test_the_end_date_cannot_be_more_than_two_years_ahead() -> None:
    assert eg.validate_ahead(base_until=NOW, delta=timedelta(days=365), now=NOW) == NOW + timedelta(days=365)
    with pytest.raises(eg.GrantError) as caught:
        eg.validate_ahead(base_until=NOW + timedelta(days=400), delta=timedelta(days=365), now=NOW)
    assert caught.value.code == "too_far"


def test_command_grammar() -> None:
    command = eg.parse_grant_command("personal @vasya 30 за помощь с баг-репортом")
    assert (command.scope, command.target, command.days, command.reason) == (
        "user", "@vasya", 30, "за помощь с баг-репортом",
    )
    assert eg.parse_grant_command("group -1001 7").reason == eg.DEFAULT_COMMAND_REASON
    for bad in ("", "personal", "personal 5", "group -1001 abc", "team 5 5", "personal 5 400"):
        with pytest.raises(eg.GrantError):
            eg.parse_grant_command(bad)
    assert eg.parse_revoke_command("personal 5").days is None
    shorten = eg.parse_revoke_command("group -1001 10 ошибка")
    assert (shorten.scope, shorten.days, shorten.reason) == ("chat", 10, "ошибка")
    with pytest.raises(eg.GrantError):
        eg.parse_revoke_command("personal 5 x")
    assert eg.target_id_from_text("-1001") == -1001 and eg.target_id_from_text("@vasya") is None


def test_notices_do_not_talk_about_payment() -> None:
    until = NOW + timedelta(days=30)
    for text in (
        eg.grant_notice(scope="user", valid_until=until, timezone_name="UTC"),
        eg.grant_notice(scope="chat", valid_until=until, timezone_name="UTC"),
        eg.revoke_notice(scope="user", mode="cancel_all", valid_until=None, timezone_name="UTC"),
        eg.revoke_notice(scope="chat", mode="shorten", valid_until=until, timezone_name="UTC"),
    ):
        assert "оплат" not in text.casefold()
    assert "05.11.2026" in eg.grant_notice(scope="user", valid_until=until, timezone_name="UTC")


# ----- service ------------------------------------------------------------------------------------------


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(ChatModel(telegram_chat_id=CHAT, type="supergroup", title="Клуб"))
        session.add(ChatModel(telegram_chat_id=-5, type="private", title=None))
        session.add(UserModel(telegram_user_id=900, is_bot=True, first_name="bot"))
        session.add(UserModel(telegram_user_id=PERSON, username="Vasya", first_name="Вася"))
        await session.commit()
    yield factory, EntitlementGrantService(factory, admin_user_id=OWNER)
    await engine.dispose()


_n = 0


def _args(**overrides):
    global _n
    _n += 1
    values = dict(
        scope="user", target_id=PERSON, days=30, reason="подарок", idempotency_key=f"k{_n}",
        actor_user_id=OWNER, source="command", now=NOW,
    )
    values.update(overrides)
    return values


async def _user_row(factory):
    async with factory() as session:
        return await session.scalar(select(UserEntitlementModel).where(UserEntitlementModel.user_id == PERSON))


async def test_grant_creates_access_for_a_user_the_bot_never_saw(env) -> None:
    factory, service = env
    outcome = await service.grant(**_args(target_id=8123))
    assert (outcome.action, outcome.status) == ("grant", "active")
    assert _as_utc(outcome.valid_until) == NOW + timedelta(days=30)
    async with factory() as session:
        assert await session.get(UserModel, 8123) is not None
        row = await session.scalar(select(UserEntitlementModel).where(UserEntitlementModel.user_id == 8123))
        assert row.product_key == SELARA_PERSONAL_PRODUCT_KEY and row.paid_daily_limit is None
        journal = (await session.scalars(select(EntitlementGrantModel))).one()
        assert journal.reason == "подарок" and journal.status_before is None and journal.status_after == "active"
        assert journal.actor_user_id == OWNER and journal.source == "command"


async def test_a_chat_grant_needs_a_chat_the_bot_knows(env) -> None:
    factory, service = env
    outcome = await service.grant(**_args(scope="chat", target_id=CHAT, days=10))
    assert outcome.scope == "chat"
    async with factory() as session:
        assert (await session.scalar(select(ChatEntitlementModel))).product_key == SELARA_AI_PRODUCT_KEY
    for target in (-999, -5):  # unknown, and a private chat
        with pytest.raises(eg.GrantError) as caught:
            await service.grant(**_args(scope="chat", target_id=target))
        assert caught.value.code == "chat_not_found"


async def test_targets_that_must_not_be_granted_are_refused(env) -> None:
    _, service = env
    with pytest.raises(eg.GrantError):
        await service.grant(**_args(target_id=OWNER))
    with pytest.raises(eg.GrantError) as caught:
        await service.grant(**_args(target_id=900))
    assert caught.value.code == "invalid_target"
    with pytest.raises(eg.GrantError):
        await service.grant(**_args(source="web"))


async def test_extending_adds_days_after_the_remaining_time_and_a_lapsed_one_restarts(env) -> None:
    factory, service = env
    async with factory() as session:
        session.add(
            UserEntitlementModel(
                user_id=PERSON, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=5), valid_until=NOW + timedelta(days=10), paid_daily_limit=150,
            )
        )
        await session.commit()
    extended = await service.grant(**_args(days=30))
    assert extended.action == "extend" and _as_utc(extended.valid_until) == NOW + timedelta(days=40)
    row = await _user_row(factory)
    assert row.paid_daily_limit == 150 and _as_utc(row.valid_from) == NOW - timedelta(days=5)

    later = NOW + timedelta(days=100)  # the subscription has lapsed by then
    restarted = await service.grant(**_args(days=7, now=later))
    assert restarted.action == "grant" and _as_utc(restarted.valid_until) == later + timedelta(days=7)
    assert _as_utc((await _user_row(factory)).valid_from) == later


async def test_the_total_cap_and_the_per_grant_cap_hold(env) -> None:
    _, service = env
    await service.grant(**_args(days=365))
    await service.grant(**_args(days=365))
    with pytest.raises(eg.GrantError) as caught:
        await service.grant(**_args(days=1))
    assert caught.value.code == "too_far"
    with pytest.raises(eg.GrantError):
        await service.grant(**_args(days=366, target_id=777))


async def test_replaying_a_key_changes_nothing_and_a_foreign_key_is_refused(env) -> None:
    factory, service = env
    first = await service.grant(**_args(idempotency_key="same"))
    again = await service.grant(**_args(idempotency_key="same"))
    assert again.duplicate and again.grant_id == first.grant_id and again.valid_until == first.valid_until
    async with factory() as session:
        assert len((await session.scalars(select(EntitlementGrantModel))).all()) == 1
    with pytest.raises(eg.GrantError) as caught:
        await service.grant(**_args(idempotency_key="same", target_id=4102))
    assert caught.value.code == "idempotency_conflict"


async def test_cancel_all_turns_access_off_and_a_new_grant_brings_it_back(env) -> None:
    factory, service = env
    await service.grant(**_args(days=30))
    revoked = await service.revoke(
        scope="user", target_id=PERSON, mode="cancel_all", days=None, reason="ошибка",
        idempotency_key="r1", actor_user_id=OWNER, source="command", now=NOW + timedelta(days=1),
    )
    assert revoked.action == "revoke" and revoked.status == "revoked"
    assert revoked.delta_seconds == 29 * 86_400
    resolver = SqlAlchemyUserEntitlementResolver(
        factory, StaticPersonalConfigProvider(PersonalConfig(None, 30, PersonalQuotaLimits(free_daily=5, paid_daily=150)))
    )
    assert (await resolver.resolve(user_id=PERSON, feature=AiFeature.PERSONAL_CHAT, trigger="t")).access_tier == AccessTier.FREE
    with pytest.raises(eg.GrantError) as caught:
        await service.revoke(
            scope="user", target_id=PERSON, mode="cancel_all", days=None, reason="ещё раз",
            idempotency_key="r2", actor_user_id=OWNER, source="command", now=NOW + timedelta(days=1),
        )
    assert caught.value.code == "not_active"
    back = await service.grant(**_args(days=5, now=NOW + timedelta(days=2)))
    assert back.action == "grant" and back.status == "active"
    assert (await resolver.resolve(user_id=PERSON, feature=AiFeature.PERSONAL_CHAT, trigger="t")).access_tier == AccessTier.PAID


async def test_shorten_removes_only_the_asked_days_and_never_goes_below_now(env) -> None:
    _, service = env
    await service.grant(**_args(days=30))
    shorter = await service.revoke(
        scope="user", target_id=PERSON, mode="shorten", days=10, reason="лишнее",
        idempotency_key="s1", actor_user_id=OWNER, source="command", now=NOW + timedelta(days=1),
    )
    assert shorter.action == "shorten" and shorter.status == "active"
    assert _as_utc(shorter.valid_until) == NOW + timedelta(days=20) and shorter.delta_seconds == 10 * 86_400
    gone = await service.revoke(
        scope="user", target_id=PERSON, mode="shorten", days=300, reason="всё",
        idempotency_key="s2", actor_user_id=OWNER, source="command", now=NOW + timedelta(days=1),
    )
    assert gone.status == "revoked" and gone.delta_seconds == 19 * 86_400
    with pytest.raises(eg.GrantError):
        await service.revoke(
            scope="user", target_id=PERSON, mode="shorten", days=None, reason="x",
            idempotency_key="s3", actor_user_id=OWNER, source="command", now=NOW,
        )
    with pytest.raises(eg.GrantError) as caught:
        await service.revoke(
            scope="user", target_id=123, mode="cancel_all", days=None, reason="x",
            idempotency_key="s4", actor_user_id=OWNER, source="command", now=NOW,
        )
    assert caught.value.code == "no_entitlement"


async def test_shorten_never_removes_paid_days(env) -> None:
    factory, service = env
    async with factory() as session:  # 30 paid days, as a payment would leave them
        session.add(
            UserEntitlementModel(
                user_id=PERSON, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW, valid_until=NOW + timedelta(days=30), paid_daily_limit=150,
            )
        )
        await session.commit()
    with pytest.raises(eg.GrantError) as caught:  # nothing was granted by hand yet
        await service.revoke(
            scope="user", target_id=PERSON, mode="shorten", days=5, reason="x",
            idempotency_key="p0", actor_user_id=OWNER, source="command", now=NOW,
        )
    assert caught.value.code == "exceeds_granted"
    await service.grant(**_args(days=7))
    taken = await service.revoke(
        scope="user", target_id=PERSON, mode="shorten", days=30, reason="ошибка",
        idempotency_key="p1", actor_user_id=OWNER, source="command", now=NOW,
    )
    assert taken.status == "active" and taken.delta_seconds == 7 * 86_400
    assert _as_utc(taken.valid_until) == NOW + timedelta(days=30)
    with pytest.raises(eg.GrantError):  # the granted days are used up, the paid ones stay
        await service.revoke(
            scope="user", target_id=PERSON, mode="shorten", days=1, reason="x",
            idempotency_key="p2", actor_user_id=OWNER, source="command", now=NOW,
        )


async def test_a_grant_from_a_lapsed_period_never_counts_against_later_paid_days(env) -> None:
    factory, service = env
    await service.grant(**_args(days=7))  # used up long ago
    async with factory() as session:  # the person paid 30 days after the gap: a new period starts
        row = await session.scalar(select(UserEntitlementModel).where(UserEntitlementModel.user_id == PERSON))
        later = datetime.now(timezone.utc) + timedelta(days=100)
        row.status, row.valid_from, row.valid_until = "active", later, later + timedelta(days=30)
        await session.commit()
    with pytest.raises(eg.GrantError) as caught:
        await service.revoke(
            scope="user", target_id=PERSON, mode="shorten", days=7, reason="x",
            idempotency_key="gap1", actor_user_id=OWNER, source="command", now=later + timedelta(days=1),
        )
    assert caught.value.code == "exceeds_granted"


async def test_an_idempotency_key_cannot_be_replayed_as_another_kind_of_operation(env) -> None:
    _, service = env
    await service.grant(**_args(days=5, idempotency_key="same"))
    with pytest.raises(eg.GrantError) as caught:
        await service.revoke(
            scope="user", target_id=PERSON, mode="cancel_all", days=None, reason="x",
            idempotency_key="same", actor_user_id=OWNER, source="command", now=NOW,
        )
    assert caught.value.code == "idempotency_conflict"


async def test_granted_mark_follows_the_last_way_the_time_was_added(env) -> None:
    factory, service = env
    assert not await service.granted_by_admin(scope="user", target_id=PERSON)
    async with factory() as session:
        session.add(
            SelaraAiPaymentModel(
                telegram_payment_charge_id="c1", invoice_payload="p", buyer_user_id=PERSON, target_scope="user",
                target_user_id=PERSON, product_key=SELARA_PERSONAL_PRODUCT_KEY, amount_stars=69, currency="XTR",
                payment_at=NOW, processing_state="applied", created_at=NOW - timedelta(hours=1),
            )
        )
        await session.commit()
    state = await service.target_state(scope="user", target_id=PERSON, now=NOW)
    assert state.paid_recently and not state.granted and not state.active
    outcome = await service.grant(**_args(now=NOW))
    assert outcome.paid_recently
    async with factory() as session:  # the journal row was written "now"; keep the order deterministic
        journal = await session.scalar(select(EntitlementGrantModel))
        journal.created_at = NOW
        await session.commit()
    assert await service.granted_by_admin(scope="user", target_id=PERSON)


async def test_lookup_recent_and_personal_list(env) -> None:
    _, service = env
    found = await service.lookup("@vasya")
    assert [user["id"] for user in found["users"]] == [PERSON]
    assert [chat["id"] for chat in (await service.lookup("Клуб"))["chats"]] == [CHAT]
    assert [chat["id"] for chat in (await service.lookup(str(CHAT)))["chats"]] == [CHAT]
    assert await service.lookup("") == {"users": [], "chats": []}
    await service.grant(**_args(now=datetime.now(timezone.utc)))
    rows = await service.recent(limit=5)
    assert rows[0]["scope"] == "user" and rows[0]["delta_days"] == 30
    personal = await service.active_personal()
    assert personal[0]["user_id"] == PERSON and personal[0]["granted_by_admin"] is True


async def test_a_failed_notice_never_undoes_the_grant_and_is_recorded(env) -> None:
    factory, service = env
    sent = []

    async def broken(chat_id: int, text: str) -> bool:
        sent.append((chat_id, text))
        raise RuntimeError("blocked by user")

    outcome, notified = await service.grant_and_notify(
        notify=True, send_notice=broken, timezone_name="UTC", **_args()
    )
    assert notified is False and outcome.status == "active" and sent[0][0] == PERSON
    async with factory() as session:
        assert (await session.scalar(select(EntitlementGrantModel))).notified is False
    _, quiet = await service.grant_and_notify(notify=False, send_notice=broken, timezone_name="UTC", **_args(days=1))
    assert quiet is None and len(sent) == 1

    ok = AsyncMock(return_value=True)
    revoked, delivered = await service.revoke_and_notify(
        notify=True, send_notice=ok, timezone_name="UTC", scope="user", target_id=PERSON, mode="cancel_all",
        days=None, reason="x", idempotency_key="rv", actor_user_id=OWNER, source="command", now=NOW,
    )
    assert delivered is True and "отключил" in ok.await_args.args[1]


# ----- owner commands ------------------------------------------------------------------------------------


def _message(text: str, *, user_id: int = OWNER, chat_type: str = "private", message_id: int = 5):
    return SimpleNamespace(
        chat=SimpleNamespace(id=user_id, type=chat_type),
        from_user=SimpleNamespace(id=user_id),
        text=text,
        message_id=message_id,
        answer=AsyncMock(),
    )


def _settings():
    return SimpleNamespace(admin_user_id=OWNER, bot_timezone="UTC")


async def test_commands_are_owner_only_and_private_only(env) -> None:
    factory, _ = env
    bot = SimpleNamespace(send_message=AsyncMock())
    for command in (handlers.grant_subscription_command, handlers.revoke_subscription_command):
        stranger = _message("/x personal 4101 5", user_id=5)
        await command(stranger, bot, factory, _settings())
        assert stranger.answer.await_args.args[0] == handlers.OWNER_ONLY_TEXT
        in_group = _message("/x personal 4101 5", chat_type="supergroup")
        await command(in_group, bot, factory, _settings())
        in_group.answer.assert_not_awaited()
    stranger = _message("/grants", user_id=5)
    await handlers.list_grants_command(stranger, factory, _settings())
    assert stranger.answer.await_args.args[0] == handlers.OWNER_ONLY_TEXT
    bot.send_message.assert_not_awaited()
    async with factory() as session:
        assert not (await session.scalars(select(EntitlementGrantModel))).all()


async def test_grant_command_grants_notifies_and_lists(env) -> None:
    factory, _ = env
    bot = SimpleNamespace(send_message=AsyncMock())
    message = _message("/grant_sub personal @Vasya 30 за помощь")
    await handlers.grant_subscription_command(message, bot, factory, _settings())
    answer = message.answer.await_args.args[0]
    assert "Выдано" in answer and "Получатель уведомлён" in answer
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == PERSON
    replay = _message("/grant_sub personal @Vasya 30 за помощь")  # the same message id: an idempotent replay
    await handlers.grant_subscription_command(replay, bot, factory, _settings())
    assert "уже выполнена" in replay.answer.await_args.args[0] and bot.send_message.await_count == 1

    listing = _message("/grants")
    await handlers.list_grants_command(listing, factory, _settings())
    assert "за помощь" in listing.answer.await_args.args[0]


async def test_revoke_command_and_usage_texts(env) -> None:
    factory, _ = env
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("403")))
    await handlers.grant_subscription_command(_message("/grant_sub personal 4101 20", message_id=1), bot, factory, _settings())
    revoke = _message("/revoke_sub personal 4101", message_id=2)
    await handlers.revoke_subscription_command(revoke, bot, factory, _settings())
    text = revoke.answer.await_args.args[0]
    assert "Отключено" in text and "не возвращаются" in text and "Уведомить получателя не удалось" in text
    for command, line in (
        (handlers.grant_subscription_command, "/grant_sub"),
        (handlers.revoke_subscription_command, "/revoke_sub"),
    ):
        bad = _message("/x", message_id=9)
        await command(bad, bot, factory, _settings())
        assert line in bad.answer.await_args.args[0]
    unknown = _message("/grant_sub personal @nobody 5", message_id=10)
    await handlers.grant_subscription_command(unknown, bot, factory, _settings())
    assert "не найден" in unknown.answer.await_args.args[0]


# ----- Mini App API ----------------------------------------------------------------------------------------


@pytest.fixture
async def api(env):
    factory, _ = env
    actor = SimpleNamespace(id=OWNER)

    async def load_user(session, request):
        return None if actor.id is None else SimpleNamespace(telegram_user_id=actor.id)

    notices = AsyncMock(return_value=True)
    settings = Settings(BOT_TOKEN="123456:test", DATABASE_URL="sqlite+aiosqlite:///:memory:", ADMIN_USER_ID=OWNER)
    app = FastAPI()
    app.include_router(
        build_miniapp_admin_router(
            settings=settings, session_factory=factory, load_user=load_user,
            broadcast_preview_handler=AsyncMock(), broadcast_start_handler=AsyncMock(),
            broadcast_status_handler=AsyncMock(), telegram_bot_probe=AsyncMock(), send_notice=notices,
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, actor, notices


PREFIX = "/api/miniapp/admin/monetization"
BODY = {"scope": "user", "target_id": PERSON, "days": 30, "reason": "подарок", "idempotency_key": "ui-1"}


@pytest.mark.parametrize("actor_id,status", [(None, 401), (42, 403), (OWNER, 200)])
async def test_every_grant_endpoint_is_owner_only(api, actor_id, status) -> None:
    client, actor, _ = api
    actor.id = actor_id
    for path in ("/grants", "/lookup?q=1", "/personal-entitlements", f"/target?scope=user&target_id={PERSON}"):
        assert (await client.get(PREFIX + path)).status_code == status
    if status != 200:
        assert (await client.post(f"{PREFIX}/grants", json=BODY)).status_code == status
        revoke = {**BODY, "mode": "cancel_all"}
        assert (await client.post(f"{PREFIX}/grants/revoke", json=revoke)).status_code == status


async def test_grant_notify_and_revoke_through_the_api(api) -> None:
    client, _, notices = api
    first = await client.post(f"{PREFIX}/grants", json=BODY)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["action"] == "grant" and data["notified"] is True and data["delta_days"] == 30
    assert notices.await_args.args[0] == PERSON
    replay = await client.post(f"{PREFIX}/grants", json=BODY)
    assert replay.json()["duplicate"] is True and notices.await_count == 1

    target = (await client.get(f"{PREFIX}/target?scope=user&target_id={PERSON}")).json()
    assert target["active"] is True and target["granted_by_admin"] is True
    assert [row["user_id"] for row in (await client.get(f"{PREFIX}/personal-entitlements")).json()["items"]] == [PERSON]

    revoke = await client.post(
        f"{PREFIX}/grants/revoke",
        json={"scope": "user", "target_id": PERSON, "mode": "shorten", "days": 5, "reason": "лишнее",
              "idempotency_key": "ui-2", "notify": True},
    )
    assert revoke.status_code == 200 and revoke.json()["action"] == "shorten" and notices.await_count == 2
    items = (await client.get(f"{PREFIX}/grants")).json()["items"]
    assert [row["action"] for row in items] == ["shorten", "grant"]


async def test_the_api_rejects_bad_input_with_readable_errors(api) -> None:
    client, _, notices = api
    for index, (patch, status) in enumerate((
        ({"days": 400}, 422),
        ({"days": 0}, 422),
        ({"reason": ""}, 422),
        ({"scope": "team"}, 422),
        ({"target_id": OWNER}, 422),
        ({"scope": "chat", "target_id": -42}, 422),
        ({"unknown": 1}, 422),
        ({"days": "30"}, 422),
    )):
        response = await client.post(f"{PREFIX}/grants", json={**BODY, **patch, "idempotency_key": f"bad-{index}"})
        assert response.status_code == status, patch
    assert (await client.post(f"{PREFIX}/grants", json={**BODY, "idempotency_key": "dup"})).status_code == 200
    conflict = await client.post(f"{PREFIX}/grants", json={**BODY, "target_id": 4102, "idempotency_key": "dup"})
    assert conflict.status_code == 409
    missing = await client.post(
        f"{PREFIX}/grants/revoke",
        json={"scope": "user", "target_id": 31337, "mode": "cancel_all", "reason": "x", "idempotency_key": "none"},
    )
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "no_entitlement"
    assert notices.await_count == 1


async def test_lookup_finds_users_and_chats(api) -> None:
    client, _, _ = api
    body = (await client.get(f"{PREFIX}/lookup", params={"q": "@vasya"})).json()
    assert [user["id"] for user in body["users"]] == [PERSON]
    assert [chat["id"] for chat in (await client.get(f"{PREFIX}/lookup", params={"q": "Клуб"})).json()["chats"]] == [CHAT]


# ----- supergroup migration -------------------------------------------------------------------------


async def test_the_journal_and_the_access_follow_a_group_into_its_supergroup(env) -> None:
    from selara.infrastructure.db.chat_migration import _migrate_selara_ai_purchases

    factory, service = env
    await service.grant(**_args(scope="chat", target_id=CHAT, days=10))
    new_chat = -1009999
    async with factory() as session:
        session.add(ChatModel(telegram_chat_id=new_chat, type="supergroup", title="Клуб"))
        await session.commit()
    async with factory() as session:
        async with session.begin():
            await _migrate_selara_ai_purchases(session, old_chat_id=CHAT, new_chat_id=new_chat)
    async with factory() as session:
        assert (await session.scalar(select(ChatEntitlementModel))).chat_id == new_chat
        assert (await session.scalar(select(EntitlementGrantModel))).target_chat_id == new_chat
