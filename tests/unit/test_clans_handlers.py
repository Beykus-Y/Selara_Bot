"""Tests for clan command handlers (clans.py).

All DB calls are replaced with AsyncMock/SimpleNamespace; no real DB needed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from selara.presentation.handlers.clans import (
    _clan_card_markup,
    _clan_list_markup,
    _get_member_ids_ordered,
    _resolve_member_name,
    clans_list_handler,
    create_clan_handler,
    delete_clan_callback,
    delete_clan_handler,
    join_clan_callback,
    join_clan_handler,
    leave_clan_callback,
    leave_clan_handler,
    my_clan_handler,
)


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _msg(
    *,
    user_id: int = 1,
    username: str = "actor",
    first_name: str = "Actor",
    chat_id: int = -100,
    text: str = "",
) -> SimpleNamespace:
    msg = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id, type="supergroup", title="Test"),
        from_user=SimpleNamespace(
            id=user_id,
            username=username,
            first_name=first_name,
            last_name=None,
            is_bot=False,
        ),
        reply=AsyncMock(),
        answer=AsyncMock(),
        reply_to_message=None,
    )
    return msg


def _query(*, data: str, user_id: int = 1, chat_id: int = -100) -> SimpleNamespace:
    q = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username="actor", first_name="Actor"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type="supergroup"),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    return q


def _clan(*, clan_id: int = 1, name: str = "Alpha", creator_user_id: int = 1, chat_id: int = -100):
    return SimpleNamespace(
        id=clan_id,
        name=name,
        creator_user_id=creator_user_id,
        chat_id=chat_id,
    )


def _mock_session():
    """Returns a minimal AsyncSession-like mock."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _bot():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(
            user=SimpleNamespace(first_name="BotName", last_name=None, username=None)
        )
    )
    return bot


# ---------------------------------------------------------------------------
# Unit tests: markup builders
# ---------------------------------------------------------------------------

def test_clan_card_markup_shows_join_for_non_member():
    markup = _clan_card_markup(42, viewer_is_member=False, viewer_is_creator=False)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "clan:join:42" in callbacks
    assert "clan:leave:42" not in callbacks
    assert "clan:delete:42" not in callbacks


def test_clan_card_markup_shows_leave_for_member():
    markup = _clan_card_markup(42, viewer_is_member=True, viewer_is_creator=False)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "clan:leave:42" in callbacks
    assert "clan:join:42" not in callbacks
    assert "clan:delete:42" not in callbacks


def test_clan_card_markup_shows_delete_for_creator():
    markup = _clan_card_markup(42, viewer_is_member=True, viewer_is_creator=True)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "clan:delete:42" in callbacks
    assert "clan:join:42" not in callbacks
    assert "clan:leave:42" not in callbacks


def test_clan_list_markup_one_button_per_clan():
    clans = [(1, "Alpha", 3), (2, "Beta", 1)]
    markup = _clan_list_markup(clans)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "clan:info:1" in callbacks
    assert "clan:info:2" in callbacks
    assert len(callbacks) == 2


# ---------------------------------------------------------------------------
# Unit tests: _resolve_member_name
# ---------------------------------------------------------------------------

from collections import namedtuple

# Unified row returned by the combined UCA+UserModel query
_Row = namedtuple("_Row", ["display_name_override", "persona_label", "title_prefix", "first_name", "last_name", "username"])
# Fallback-only row (UserModel)
_URow = namedtuple("_URow", ["first_name", "last_name", "username"])


def _row(*, display=None, persona=None, title=None, first=None, last=None, uname=None):
    return _Row(display, persona, title, first, last, uname)


def _urow(first=None, last=None, uname=None):
    return _URow(first, last, uname)


async def test_resolve_member_name_uses_display_override():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(return_value=_row(display="Ник", first="Igor"))

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "Ник"


async def test_resolve_member_name_prepends_persona():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(
        return_value=_row(persona="Альбедо", first="Faust")
    )

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "[Альбедо] Faust"


async def test_resolve_member_name_persona_only_when_no_base():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(
        return_value=_row(persona="Альбедо")
    )

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "[Альбедо]"


async def test_resolve_member_name_falls_back_to_first_name():
    session = _mock_session()
    # First execute: UCA+UserModel row found but no display override / persona
    session.execute.return_value.first = MagicMock(
        return_value=_row(first="Иван", last="Петров")
    )

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "Иван Петров"


async def test_resolve_member_name_falls_back_to_username():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(
        return_value=_row(uname="vasya")
    )

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "@vasya"


async def test_resolve_member_name_falls_back_to_usermodel_when_no_uca():
    """User never appeared in this chat — UCA row missing, UserModel exists."""
    session = _mock_session()
    session.execute.return_value.first = MagicMock(
        side_effect=[None, _urow(first="Маша")]
    )

    name = await _resolve_member_name(_bot(), session, user_id=7, chat_id=-100)
    assert name == "Маша"


async def test_resolve_member_name_falls_back_to_telegram_api():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(side_effect=[None, _urow()])
    bot = _bot()
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(
            user=SimpleNamespace(first_name="TgName", last_name=None, username=None)
        )
    )

    name = await _resolve_member_name(bot, session, user_id=7, chat_id=-100)
    assert name == "TgName"


async def test_resolve_member_name_last_resort_id():
    session = _mock_session()
    session.execute.return_value.first = MagicMock(side_effect=[None, _urow()])
    bot = _bot()
    bot.get_chat_member = AsyncMock(side_effect=Exception("network error"))

    name = await _resolve_member_name(bot, session, user_id=99, chat_id=-100)
    assert name == "id:99"


# ---------------------------------------------------------------------------
# Handler tests: my_clan_handler
# ---------------------------------------------------------------------------

async def test_my_clan_handler_not_in_clan_shows_hint():
    msg = _msg(text="мой клан")
    session = _mock_session()

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
    ):
        await my_clan_handler(msg, bot=_bot(), db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "не состоишь" in reply_text
    assert "кланы" in reply_text
    assert "создать клан" in reply_text


async def test_my_clan_handler_in_clan_shows_card():
    msg = _msg(text="мой клан")
    session = _mock_session()
    clan = _clan()

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1))),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
        patch("selara.presentation.handlers.clans._send_clan_card", new=AsyncMock()) as mock_send,
    ):
        await my_clan_handler(msg, bot=_bot(), db_session=session)

    mock_send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Handler tests: clans_list_handler
# ---------------------------------------------------------------------------

async def test_clans_list_handler_empty_chat():
    msg = _msg(text="кланы")
    session = _mock_session()

    with patch("selara.presentation.handlers.clans._list_clans_in_chat", new=AsyncMock(return_value=[])):
        await clans_list_handler(msg, db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "нет кланов" in reply_text
    assert "создать клан" in reply_text


async def test_clans_list_handler_shows_clans_with_buttons():
    msg = _msg(text="кланы")
    session = _mock_session()
    fake_clans = [(1, "Alpha", 5), (2, "Beta", 2)]

    with patch("selara.presentation.handlers.clans._list_clans_in_chat", new=AsyncMock(return_value=fake_clans)):
        await clans_list_handler(msg, db_session=session)

    kwargs = msg.reply.await_args.kwargs
    reply_text = msg.reply.await_args.args[0]
    markup = kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]

    assert "Alpha" in reply_text
    assert "Beta" in reply_text
    assert "clan:info:1" in callbacks
    assert "clan:info:2" in callbacks


# ---------------------------------------------------------------------------
# Handler tests: create_clan_handler
# ---------------------------------------------------------------------------

async def test_create_clan_handler_empty_name():
    msg = _msg(text="создать клан ")
    session = _mock_session()

    with patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)):
        await create_clan_handler(msg, bot=_bot(), db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "Укажи название" in reply_text


async def test_create_clan_handler_name_too_long():
    long_name = "A" * 65
    msg = _msg(text=f"создать клан {long_name}")
    session = _mock_session()

    with patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)):
        await create_clan_handler(msg, bot=_bot(), db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "64" in reply_text


async def test_create_clan_handler_already_in_clan():
    msg = _msg(text="создать клан NewClan")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=5, name="OldClan", creator_user_id=1)),
    ):
        await create_clan_handler(msg, bot=_bot(), db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "OldClan" in reply_text
    assert "выйди" in reply_text


async def test_create_clan_handler_duplicate_name():
    msg = _msg(text="создать клан Alpha")
    session = _mock_session()
    session.flush = AsyncMock(side_effect=IntegrityError(None, None, Exception()))

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans.ClanModel", return_value=SimpleNamespace(id=None, name="Alpha", creator_user_id=1, chat_id=-100)),
    ):
        await create_clan_handler(msg, bot=_bot(), db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "уже существует" in reply_text


async def test_create_clan_handler_success_shows_card():
    msg = _msg(text="создать клан Alpha")
    session = _mock_session()
    fake_clan = _clan(name="Alpha")

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans.ClanModel", return_value=fake_clan),
        patch("selara.presentation.handlers.clans.ClanMemberModel", return_value=SimpleNamespace()),
        patch("selara.presentation.handlers.clans._send_clan_card", new=AsyncMock()) as mock_send,
    ):
        await create_clan_handler(msg, bot=_bot(), db_session=session)

    mock_send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Handler tests: delete_clan_handler
# ---------------------------------------------------------------------------

async def test_delete_clan_handler_not_in_clan():
    msg = _msg(text="удалить клан")
    session = _mock_session()

    with patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)):
        await delete_clan_handler(msg, db_session=session)

    assert "не состоишь" in msg.reply.await_args.args[0]


async def test_delete_clan_handler_not_creator():
    msg = _msg(user_id=2, text="удалить клан")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
    ):
        await delete_clan_handler(msg, db_session=session)

    assert "Только создатель" in msg.reply.await_args.args[0]


async def test_delete_clan_handler_success():
    msg = _msg(user_id=1, text="удалить клан")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
    ):
        await delete_clan_handler(msg, db_session=session)

    session.execute.assert_awaited()
    reply_text = msg.reply.await_args.args[0]
    assert "Alpha" in reply_text
    assert "удалён" in reply_text


# ---------------------------------------------------------------------------
# Handler tests: join_clan_handler
# ---------------------------------------------------------------------------

async def test_join_clan_handler_already_in_clan():
    msg = _msg(text="вступить в клан 2")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
    ):
        await join_clan_handler(msg, bot=_bot(), db_session=session)

    assert "Alpha" in msg.reply.await_args.args[0]
    assert "выйди" in msg.reply.await_args.args[0]


async def test_join_clan_handler_not_found():
    msg = _msg(text="вступить в клан 99")
    session = _mock_session()

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._find_clan_by_name", new=AsyncMock(return_value=None)),
    ):
        await join_clan_handler(msg, bot=_bot(), db_session=session)

    assert "не найден" in msg.reply.await_args.args[0]


async def test_join_clan_handler_by_id_success():
    msg = _msg(text="вступить в клан 2")
    session = _mock_session()
    clan = _clan(clan_id=2, name="Beta")

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
        patch("selara.presentation.handlers.clans.ClanMemberModel", return_value=SimpleNamespace()),
        patch("selara.presentation.handlers.clans._send_clan_card", new=AsyncMock()) as mock_send,
    ):
        await join_clan_handler(msg, bot=_bot(), db_session=session)

    mock_send.assert_awaited_once()


async def test_join_clan_handler_by_name_success():
    msg = _msg(text="вступить в клан Beta")
    session = _mock_session()
    clan = _clan(clan_id=2, name="Beta")

    with (
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._find_clan_by_name", new=AsyncMock(return_value=clan)),
        patch("selara.presentation.handlers.clans.ClanMemberModel", return_value=SimpleNamespace()),
        patch("selara.presentation.handlers.clans._send_clan_card", new=AsyncMock()) as mock_send,
    ):
        await join_clan_handler(msg, bot=_bot(), db_session=session)

    mock_send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Handler tests: leave_clan_handler
# ---------------------------------------------------------------------------

async def test_leave_clan_handler_not_in_clan():
    msg = _msg(text="выйти из клана")
    session = _mock_session()

    with patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)):
        await leave_clan_handler(msg, db_session=session)

    assert "не состоишь" in msg.reply.await_args.args[0]


async def test_leave_clan_handler_creator_cannot_leave():
    msg = _msg(user_id=1, text="выйти из клана")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
    ):
        await leave_clan_handler(msg, db_session=session)

    reply_text = msg.reply.await_args.args[0]
    assert "Создатель" in reply_text
    assert "удалить" in reply_text


async def test_leave_clan_handler_member_leaves():
    msg = _msg(user_id=2, text="выйти из клана")
    session = _mock_session()

    with patch(
        "selara.presentation.handlers.clans._get_user_clan_row",
        new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
    ):
        await leave_clan_handler(msg, db_session=session)

    session.execute.assert_awaited()
    reply_text = msg.reply.await_args.args[0]
    assert "Alpha" in reply_text
    assert "вышел" in reply_text


# ---------------------------------------------------------------------------
# Callback tests: join
# ---------------------------------------------------------------------------

async def test_join_callback_already_in_clan():
    q = _query(data="clan:join:1", user_id=2)
    session = _mock_session()

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch(
            "selara.presentation.handlers.clans._get_user_clan_row",
            new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
        ),
    ):
        await join_clan_callback(q, bot=_bot(), db_session=session)

    q.answer.assert_awaited()
    alert_text = q.answer.await_args.args[0]
    assert "Alpha" in alert_text


async def test_join_callback_success_updates_message():
    q = _query(data="clan:join:1", user_id=2)
    session = _mock_session()
    clan = _clan()

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
        patch("selara.presentation.handlers.clans.ClanMemberModel", return_value=SimpleNamespace()),
        patch("selara.presentation.handlers.clans._build_clan_card_text", new=AsyncMock(return_value="card")),
    ):
        await join_clan_callback(q, bot=_bot(), db_session=session)

    q.message.edit_text.assert_awaited()


# ---------------------------------------------------------------------------
# Callback tests: leave
# ---------------------------------------------------------------------------

async def test_leave_callback_not_in_this_clan():
    q = _query(data="clan:leave:1", user_id=2)
    session = _mock_session()

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch("selara.presentation.handlers.clans._get_user_clan_row", new=AsyncMock(return_value=None)),
    ):
        await leave_clan_callback(q, bot=_bot(), db_session=session)

    alert_text = q.answer.await_args.args[0]
    assert "не в этом клане" in alert_text


async def test_leave_callback_creator_blocked():
    q = _query(data="clan:leave:1", user_id=1)
    session = _mock_session()

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch(
            "selara.presentation.handlers.clans._get_user_clan_row",
            new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
        ),
    ):
        await leave_clan_callback(q, bot=_bot(), db_session=session)

    alert_text = q.answer.await_args.args[0]
    assert "Создатель" in alert_text


async def test_leave_callback_member_leaves_updates_card():
    q = _query(data="clan:leave:1", user_id=2)
    session = _mock_session()
    clan = _clan()

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch(
            "selara.presentation.handlers.clans._get_user_clan_row",
            new=AsyncMock(return_value=SimpleNamespace(id=1, name="Alpha", creator_user_id=1)),
        ),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
        patch("selara.presentation.handlers.clans._build_clan_card_text", new=AsyncMock(return_value="card")),
    ):
        await leave_clan_callback(q, bot=_bot(), db_session=session)

    session.execute.assert_awaited()
    q.message.edit_text.assert_awaited()


# ---------------------------------------------------------------------------
# Callback tests: delete
# ---------------------------------------------------------------------------

async def test_delete_callback_not_creator():
    q = _query(data="clan:delete:1", user_id=2)
    session = _mock_session()
    clan = _clan(creator_user_id=1)

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
    ):
        await delete_clan_callback(q, db_session=session)

    alert_text = q.answer.await_args.args[0]
    assert "создатель" in alert_text.lower()


async def test_delete_callback_creator_deletes():
    q = _query(data="clan:delete:1", user_id=1)
    session = _mock_session()
    clan = _clan(creator_user_id=1)

    with (
        patch("selara.presentation.handlers.clans._safe_callback_answer", new=AsyncMock()),
        patch("selara.presentation.handlers.clans._get_clan_by_id", new=AsyncMock(return_value=clan)),
    ):
        await delete_clan_callback(q, db_session=session)

    session.execute.assert_awaited()
    q.message.edit_text.assert_awaited()
    alert_text = q.answer.await_args.args[0]
    assert "Alpha" in alert_text
    assert "удалён" in alert_text
