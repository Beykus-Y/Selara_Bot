from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.admin_broadcasts import (
    BroadcastFormatError,
    build_inline_keyboard,
    parse_broadcast_source,
    resolve_reaction_mode,
    validate_broadcast_photo,
)
from selara.domain.entities import AdminBroadcastTarget, ChatSnapshot, UserSnapshot
from selara.domain.reactions import TelegramReactionTotal, TelegramReactionValue
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


def test_reaction_block_is_compiled_into_shared_visible_footer() -> None:
    parsed = parse_broadcast_source(
        "<b>Важное объявление</b>\n\n[reactions]\n👍 = Всё понятно\n🤔 = Есть вопросы\n[/reactions]"
    )

    assert parsed.body == "<b>Важное объявление</b>"
    assert parsed.rendered_text == (
        "<b>Важное объявление</b>\n\n"
        "<b>Реакции:</b>\n"
        "👍 — Всё понятно\n"
        "🤔 — Есть вопросы"
    )
    assert [(option.key, option.emoji, option.label) for option in parsed.options] == [
        ("r1", "👍", "Всё понятно"),
        ("r2", "🤔", "Есть вопросы"),
    ]


def test_reaction_labels_are_html_escaped_but_message_html_is_preserved() -> None:
    parsed = parse_broadcast_source(
        "<i>Текст</i>\n[reactions]\n👍 = Да & точно\n👎 = <нет>\n[/reactions]"
    )

    assert parsed.rendered_text.startswith("<i>Текст</i>")
    assert "Да &amp; точно" in parsed.rendered_text
    assert "&lt;нет&gt;" in parsed.rendered_text


def test_message_without_reaction_block_remains_unchanged() -> None:
    parsed = parse_broadcast_source("  Обычное сообщение  ")

    assert parsed.body == "Обычное сообщение"
    assert parsed.rendered_text == "Обычное сообщение"
    assert parsed.options == ()


@pytest.mark.parametrize(
    ("source", "error_fragment"),
    [
        ("Текст\n[reactions]\n👍 = Да\n[/reactions]", "минимум 2"),
        ("Текст\n[reactions]\n👍 = Да\n👍 = Точно\n[/reactions]", "повторяется"),
        ("Текст\n[reactions]\n❤ = Да\n❤️ = Точно\n[/reactions]", "повторяется"),
        ("Текст\n[reactions]\n👍 Да\n👎 = Нет\n[/reactions]", "emoji = описание"),
        ("Текст\n[reactions]\n👍 = Да\n👎 = Нет", "не закрыт"),
        ("Текст\n[/reactions]", "закрывающий"),
        (
            "Текст\n[reactions]\n1️⃣ = 1\n2️⃣ = 2\n3️⃣ = 3\n4️⃣ = 4\n5️⃣ = 5\n6️⃣ = 6\n7️⃣ = 7\n[/reactions]",
            "не больше 6",
        ),
        ("[reactions]\n👍 = Да\n👎 = Нет\n[/reactions]", "текст сообщения"),
        ("Текст\n[reactions]\nслово = Да\n👎 = Нет\n[/reactions]", "emoji"),
        ("Текст\n[reactions]\n👍< = Да\n👎 = Нет\n[/reactions]", "emoji"),
    ],
)
def test_invalid_reaction_markup_is_rejected(source: str, error_fragment: str) -> None:
    with pytest.raises(BroadcastFormatError, match=error_fragment):
        parse_broadcast_source(source)


def test_native_mode_requires_admin_and_all_requested_reactions() -> None:
    options = parse_broadcast_source(
        "Текст\n[reactions]\n👍 = Да\n👎 = Нет\n[/reactions]"
    ).options

    assert resolve_reaction_mode(options=options, bot_is_admin=True, available_reactions=None) == "native"
    assert resolve_reaction_mode(options=options, bot_is_admin=False, available_reactions=None) == "inline"
    assert resolve_reaction_mode(options=options, bot_is_admin=True, available_reactions={"👍", "👎", "🔥"}) == "native"
    assert resolve_reaction_mode(options=options, bot_is_admin=True, available_reactions={"👍"}) == "inline"
    assert resolve_reaction_mode(options=(), bot_is_admin=True, available_reactions=None) == "none"


def test_native_mode_normalizes_emoji_presentation_selectors() -> None:
    options = parse_broadcast_source(
        "Текст\n[reactions]\n❤️ = Нравится\n👍 = Поддерживаю\n[/reactions]"
    ).options

    assert resolve_reaction_mode(
        options=options,
        bot_is_admin=True,
        available_reactions={"❤", "👍"},
    ) == "native"


def test_inline_keyboard_uses_short_stable_callback_data() -> None:
    options = parse_broadcast_source(
        "Текст\n[reactions]\n👍 = Да\n🤔 = Вопрос\n👎 = Нет\n[/reactions]"
    ).options

    markup = build_inline_keyboard(delivery_id=987654321, options=options)
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert [button.text for button in buttons] == ["👍", "🤔", "👎"]
    assert [button.callback_data for button in buttons] == [
        "abr:987654321:r1",
        "abr:987654321:r2",
        "abr:987654321:r3",
    ]
    assert all(len((button.callback_data or "").encode()) <= 64 for button in buttons)


def test_photo_validation_uses_file_signature_not_only_content_type() -> None:
    image = Image.new("RGB", (32, 24), "red")
    payload = BytesIO()
    image.save(payload, format="PNG")

    validated = validate_broadcast_photo(
        filename="notice.png",
        content_type="image/png",
        content=payload.getvalue(),
    )

    assert validated.format == "PNG"
    assert validated.width == 32
    assert validated.height == 24


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "error_fragment"),
    [
        ("fake.jpg", "image/jpeg", b"not an image", "повреждён"),
        ("payload.exe", "application/octet-stream", b"MZ", "JPEG или PNG"),
        ("huge.jpg", "image/jpeg", b"x" * (10 * 1024 * 1024 + 1), "10 МБ"),
        ("", "image/png", b"", "не выбран"),
    ],
)
def test_invalid_photo_is_rejected(
    filename: str,
    content_type: str,
    content: bytes,
    error_fragment: str,
) -> None:
    with pytest.raises(BroadcastFormatError, match=error_fragment):
        validate_broadcast_photo(filename=filename, content_type=content_type, content=content)


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")
async def test_inline_vote_is_validated_toggleable_and_single_choice() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    user = UserSnapshot(telegram_user_id=7001, username="voter", first_name="Voter", last_name=None, is_bot=False)
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=-1007001, type="supergroup", title="Votes"))
            repo = SqlAlchemyActivityRepository(session)
            broadcast = await repo.create_admin_broadcast(
                body="Исходник",
                rendered_body="Исходник\n\n<b>Реакции:</b>\n👍 — Да\n👎 — Нет",
                reaction_options=[
                    {"key": "r1", "emoji": "👍", "label": "Да"},
                    {"key": "r2", "emoji": "👎", "label": "Нет"},
                ],
                active_since_days=3,
                created_by_user_id=77,
            )
            delivery = (
                await repo.create_admin_broadcast_deliveries(
                    broadcast_id=broadcast.id,
                    targets=[AdminBroadcastTarget(-1007001, "supergroup", "Votes", now)],
                )
            )[0]
            await repo.mark_admin_broadcast_delivery_sent(
                delivery_id=delivery.id,
                telegram_message_id=9001,
                reaction_mode="inline",
                bot_member_status="member",
                sent_at=now,
            )

            assert (
                await repo.toggle_admin_broadcast_inline_reaction(
                    delivery_id=delivery.id,
                    chat_id=-1007999,
                    telegram_message_id=9001,
                    user=user,
                    option_key="r1",
                    reacted_at=now,
                )
                == "invalid"
            )
            assert await repo.toggle_admin_broadcast_inline_reaction(
                delivery_id=delivery.id,
                chat_id=-1007001,
                telegram_message_id=9001,
                user=user,
                option_key="r9",
                reacted_at=now,
            ) == "invalid"
            assert await repo.toggle_admin_broadcast_inline_reaction(
                delivery_id=delivery.id,
                chat_id=-1007001,
                telegram_message_id=9001,
                user=user,
                option_key="r1",
                reacted_at=now,
            ) == "selected"
            assert await repo.toggle_admin_broadcast_inline_reaction(
                delivery_id=delivery.id,
                chat_id=-1007001,
                telegram_message_id=9001,
                user=user,
                option_key="r2",
                reacted_at=now,
            ) == "selected"

            active = await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id)
            assert [(item.source, item.option_key, item.user.telegram_user_id if item.user else None) for item in active] == [
                ("inline", "r2", user.telegram_user_id)
            ]
            overview = (await repo.list_recent_admin_broadcasts(limit=1))[0]
            assert overview.reaction_count == 1

            assert await repo.toggle_admin_broadcast_inline_reaction(
                delivery_id=delivery.id,
                chat_id=-1007001,
                telegram_message_id=9001,
                user=user,
                option_key="r2",
                reacted_at=now,
            ) == "removed"
            assert await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id) == []
            overview_after_remove = (await repo.list_recent_admin_broadcasts(limit=1))[0]
            assert overview_after_remove.reaction_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")
async def test_native_reaction_snapshot_replaces_old_user_choices() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    chat = ChatSnapshot(telegram_chat_id=-1007002, chat_type="supergroup", title="Native")
    user = UserSnapshot(telegram_user_id=7002, username="native", first_name="Native", last_name=None, is_bot=False)
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=chat.telegram_chat_id, type=chat.chat_type, title=chat.title))
            repo = SqlAlchemyActivityRepository(session)
            broadcast = await repo.create_admin_broadcast(
                body="Исходник",
                rendered_body="Исходник\n\n<b>Реакции:</b>\n❤️ — Да\n👎 — Нет",
                reaction_options=[
                    {"key": "r1", "emoji": "❤️", "label": "Да"},
                    {"key": "r2", "emoji": "👎", "label": "Нет"},
                ],
                active_since_days=3,
                created_by_user_id=77,
            )
            delivery = (
                await repo.create_admin_broadcast_deliveries(
                    broadcast_id=broadcast.id,
                    targets=[AdminBroadcastTarget(chat.telegram_chat_id, chat.chat_type, chat.title, now)],
                )
            )[0]
            await repo.mark_admin_broadcast_delivery_sent(
                delivery_id=delivery.id,
                telegram_message_id=9002,
                reaction_mode="native",
                bot_member_status="administrator",
                sent_at=now,
            )

            assert await repo.replace_admin_broadcast_native_reactions(
                chat=chat,
                user=user,
                telegram_message_id=9002,
                emojis={"❤", "🔥"},
                reacted_at=now,
            )
            active = await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id)
            assert {(item.emoji, item.option_key) for item in active} == {("❤", "r1"), ("🔥", None)}

            assert await repo.replace_admin_broadcast_native_reactions(
                chat=chat,
                user=user,
                telegram_message_id=9002,
                reactions={
                    TelegramReactionValue("custom_emoji", "5368324170671202286", "✨"),
                    TelegramReactionValue("paid", "paid", "⭐"),
                },
                reacted_at=now,
            )
            active = await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id)
            assert {
                (item.reaction_type, item.reaction_value, item.option_key)
                for item in active
            } == {
                ("custom_emoji", "5368324170671202286", None),
                ("paid", "paid", None),
            }

            assert await repo.replace_admin_broadcast_native_reactions(
                chat=chat,
                user=user,
                telegram_message_id=9002,
                emojis={"🔥"},
                reacted_at=now - timedelta(seconds=1),
            )
            active = await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id)
            assert {(item.reaction_type, item.reaction_value) for item in active} == {
                ("custom_emoji", "5368324170671202286"),
                ("paid", "paid"),
            }

            assert await repo.replace_admin_broadcast_native_reactions(
                chat=chat,
                user=user,
                telegram_message_id=9002,
                emojis={"👎"},
                reacted_at=now,
            )
            assert [item.option_key for item in await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id)] == ["r2"]

            assert await repo.replace_admin_broadcast_native_reactions(
                chat=chat,
                user=user,
                telegram_message_id=9002,
                emojis=set(),
                reacted_at=now,
            )
            assert await repo.list_admin_broadcast_reactions(broadcast_id=broadcast.id) == []

            assert await repo.replace_admin_broadcast_reaction_counts(
                chat_id=chat.telegram_chat_id,
                telegram_message_id=9002,
                reactions=[
                    TelegramReactionTotal(TelegramReactionValue("emoji", "❤", "❤"), 3),
                    TelegramReactionTotal(
                        TelegramReactionValue("custom_emoji", "5368324170671202286", "✨"),
                        2,
                    ),
                    TelegramReactionTotal(TelegramReactionValue("paid", "paid", "⭐"), 1),
                ],
                observed_at=now,
            )
            assert await repo.replace_admin_broadcast_reaction_counts(
                chat_id=chat.telegram_chat_id,
                telegram_message_id=9002,
                counts={"👍": 1},
                observed_at=now - timedelta(seconds=1),
            )
            counts = await repo.list_admin_broadcast_reaction_counts(broadcast_id=broadcast.id)
            assert {
                (item.reaction_type, item.reaction_value): (item.count, item.option_key)
                for item in counts
            } == {
                ("emoji", "❤"): (3, "r1"),
                ("custom_emoji", "5368324170671202286"): (2, None),
                ("paid", "paid"): (1, None),
            }
    finally:
        await engine.dispose()
