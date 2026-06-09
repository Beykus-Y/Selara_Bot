from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import FamilyBundle
from selara.presentation.handlers.chat_assistant import (
    adopt_command,
    adopt_daughter_command,
    escape_family_command,
    escape_pet_command,
    famleave_callback,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle(*, parents: tuple[int, ...] = (), owners: tuple[int, ...] = ()) -> FamilyBundle:
    return FamilyBundle(
        subject_user_id=1,
        spouse_user_id=None,
        parents=parents,
        grandparents=(),
        step_parents=(),
        siblings=(),
        children=(),
        pets=(),
        owners=owners,
    )


def _message(*, chat_type: str = "group", user_id: int = 1, args: str | None = None):
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type=chat_type, title="Test"),
        from_user=SimpleNamespace(
            id=user_id,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        ),
        reply_to_message=None,
        answer=AsyncMock(),
    )
    return msg


def _command(args: str | None = None):
    return SimpleNamespace(args=args)


def _chat_settings(*, family_tree_enabled: bool = True):
    return SimpleNamespace(family_tree_enabled=family_tree_enabled)


def _repo(*, bundle: FamilyBundle | None = None, target_snapshot=None, remove_result: bool = True):
    repo = SimpleNamespace(
        list_family_bundle=AsyncMock(return_value=bundle or _bundle()),
        get_user_snapshot=AsyncMock(return_value=target_snapshot),
        find_chat_user_by_username=AsyncMock(return_value=target_snapshot),
        remove_graph_relationship=AsyncMock(return_value=remove_result),
        get_chat_display_name=AsyncMock(return_value=None),
    )
    return repo


def _query(*, data: str, user_id: int = 1):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(
            id=user_id,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        ),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="group", title="Test"),
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# escape_family_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escape_family_disabled_in_chat() -> None:
    msg = _message()
    await escape_family_command(msg, _repo(), _chat_settings(family_tree_enabled=False))
    msg.answer.assert_awaited_once()
    assert "отключены" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_escape_family_only_in_group() -> None:
    msg = _message(chat_type="private")
    await escape_family_command(msg, _repo(), _chat_settings())
    msg.answer.assert_awaited_once()
    assert "группе" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_escape_family_no_parents() -> None:
    msg = _message()
    repo = _repo(bundle=_bundle(parents=()))
    await escape_family_command(msg, repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "родител" in text or "семь" in text


@pytest.mark.asyncio
async def test_escape_family_with_one_parent_shows_confirm() -> None:
    msg = _message()
    repo = _repo(bundle=_bundle(parents=(600,)))
    await escape_family_command(msg, repo, _chat_settings())
    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args
    # должна быть inline клавиатура с confirm/cancel
    markup = call_kwargs.kwargs.get("reply_markup")
    assert markup is not None
    buttons_text = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("подтвердить" in t.lower() or "✅" in t for t in buttons_text)
    assert any("отмен" in t.lower() or "❌" in t for t in buttons_text)
    # callback data содержит actor_id и parent_id
    buttons_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert any("600" in d for d in buttons_data if d)
    assert any("1" in d for d in buttons_data if d)


@pytest.mark.asyncio
async def test_escape_family_with_multiple_parents_shows_all_options() -> None:
    msg = _message()
    repo = _repo(bundle=_bundle(parents=(600, 601)))
    await escape_family_command(msg, repo, _chat_settings())
    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args
    markup = call_kwargs.kwargs.get("reply_markup")
    assert markup is not None
    buttons_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    # обе кнопки убегания — одна для 600, другая для 601
    assert any("600" in d for d in buttons_data)
    assert any("601" in d for d in buttons_data)


# ---------------------------------------------------------------------------
# escape_pet_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escape_pet_disabled_in_chat() -> None:
    msg = _message()
    await escape_pet_command(msg, _command(), _repo(), _chat_settings(family_tree_enabled=False))
    msg.answer.assert_awaited_once()
    assert "отключены" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_escape_pet_only_in_group() -> None:
    msg = _message(chat_type="private")
    await escape_pet_command(msg, _command(), _repo(), _chat_settings())
    msg.answer.assert_awaited_once()
    assert "группе" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_escape_pet_no_target_no_owners_shows_error() -> None:
    # без args и без хозяев в чате — сообщение об отсутствии хозяев
    msg = _message()
    repo = _repo(target_snapshot=None, bundle=_bundle(owners=()))
    await escape_pet_command(msg, _command(args=None), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "хозяев" in text or "нет" in text


@pytest.mark.asyncio
async def test_escape_pet_self_target() -> None:
    msg = _message(user_id=1)
    from selara.domain.entities import UserSnapshot
    self_snapshot = UserSnapshot(
        telegram_user_id=1, username="actor", first_name="Actor", last_name=None, is_bot=False
    )
    repo = _repo(target_snapshot=self_snapshot)
    await escape_pet_command(msg, _command(args="@actor"), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "себ" in text or "нельзя" in text


@pytest.mark.asyncio
async def test_escape_pet_with_valid_owner_shows_confirm() -> None:
    msg = _message(user_id=1)
    from selara.domain.entities import UserSnapshot
    owner = UserSnapshot(
        telegram_user_id=700, username="owner", first_name="Owner", last_name=None, is_bot=False
    )
    repo = _repo(target_snapshot=owner)
    await escape_pet_command(msg, _command(args="@owner"), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    markup = msg.answer.call_args.kwargs.get("reply_markup")
    assert markup is not None
    buttons_text = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("подтвердить" in t.lower() or "✅" in t for t in buttons_text)
    buttons_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any("700" in d for d in buttons_data)


# ---------------------------------------------------------------------------
# famleave_callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_famleave_callback_invalid_format() -> None:
    query = _query(data="famleave:confirm:escape_family:1")  # только 4 части
    await famleave_callback(query, _repo())
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_famleave_callback_blocks_non_initiator() -> None:
    # initiator_id=1, но нажимает user_id=2
    query = _query(data="famleave:confirm:escape_family:1:600", user_id=2)
    await famleave_callback(query, _repo())
    query.answer.assert_awaited_once()
    text = query.answer.call_args.args[0].lower()
    assert "инициатор" in text or "только" in text


@pytest.mark.asyncio
async def test_famleave_callback_cancel_escape_family() -> None:
    query = _query(data="famleave:cancel:escape_family:1:600", user_id=1)
    await famleave_callback(query, _repo())
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.call_args.args[0].lower()
    assert "отмен" in text


@pytest.mark.asyncio
async def test_famleave_callback_cancel_escape_pet() -> None:
    query = _query(data="famleave:cancel:escape_pet:1:700", user_id=1)
    await famleave_callback(query, _repo())
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.call_args.args[0].lower()
    assert "отмен" in text


@pytest.mark.asyncio
async def test_famleave_callback_confirm_escape_family_calls_remove() -> None:
    query = _query(data="famleave:confirm:escape_family:1:600", user_id=1)
    repo = _repo(remove_result=True)
    await famleave_callback(query, repo)
    repo.remove_graph_relationship.assert_awaited_once_with(
        chat_id=-100,
        user_a=1,
        user_b=600,
        relation_type="child",
    )
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_famleave_callback_confirm_escape_family_not_found() -> None:
    query = _query(data="famleave:confirm:escape_family:1:600", user_id=1)
    repo = _repo(remove_result=False)
    await famleave_callback(query, repo)
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.call_args.args[0].lower()
    assert "не найден" in text or "нет" in text


@pytest.mark.asyncio
async def test_famleave_callback_confirm_escape_pet_calls_remove() -> None:
    query = _query(data="famleave:confirm:escape_pet:1:700", user_id=1)
    repo = _repo(remove_result=True)
    await famleave_callback(query, repo)
    repo.remove_graph_relationship.assert_awaited_once_with(
        chat_id=-100,
        user_a=1,
        user_b=700,
        relation_type="pet",
    )
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_famleave_callback_confirm_escape_pet_not_owner() -> None:
    query = _query(data="famleave:confirm:escape_pet:1:700", user_id=1)
    repo = _repo(remove_result=False)
    await famleave_callback(query, repo)
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.call_args.args[0].lower()
    assert "не найден" in text or "хозяин" in text or "нет" in text


# ---------------------------------------------------------------------------
# adopt_command vs adopt_daughter_command — verb и child_role
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adopt_command_sends_son_verb() -> None:
    from selara.domain.entities import UserSnapshot
    msg = _message(user_id=1)
    target = UserSnapshot(telegram_user_id=800, username="child", first_name="Child", last_name=None, is_bot=False)
    repo = _repo(target_snapshot=target)
    await adopt_command(msg, _command(args="@child"), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "усыновить" in text or "сын" in text


@pytest.mark.asyncio
async def test_adopt_daughter_command_sends_daughter_verb() -> None:
    from selara.domain.entities import UserSnapshot
    msg = _message(user_id=1)
    target = UserSnapshot(telegram_user_id=800, username="child", first_name="Child", last_name=None, is_bot=False)
    repo = _repo(target_snapshot=target)
    await adopt_daughter_command(msg, _command(args="@child"), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "удочерить" in text or "дочь" in text


@pytest.mark.asyncio
async def test_adopt_daughter_disabled_in_chat() -> None:
    msg = _message()
    await adopt_daughter_command(msg, _command(), _repo(), _chat_settings(family_tree_enabled=False))
    msg.answer.assert_awaited_once()
    assert "отключены" in msg.answer.call_args.args[0].lower()


# ---------------------------------------------------------------------------
# escape_pet без аргументов — список хозяев
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escape_pet_no_args_no_owners() -> None:
    msg = _message()
    repo = _repo(target_snapshot=None, bundle=_bundle(owners=()))
    await escape_pet_command(msg, _command(args=None), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0].lower()
    assert "хозяев" in text or "нет" in text or "хозяин" in text


@pytest.mark.asyncio
async def test_escape_pet_no_args_one_owner_shows_button() -> None:
    msg = _message()
    repo = _repo(target_snapshot=None, bundle=_bundle(owners=(700,)))
    await escape_pet_command(msg, _command(args=None), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    markup = msg.answer.call_args.kwargs.get("reply_markup")
    assert markup is not None
    buttons_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any("700" in d and "escape_pet" in d for d in buttons_data)


@pytest.mark.asyncio
async def test_escape_pet_no_args_multiple_owners_shows_all() -> None:
    msg = _message()
    repo = _repo(target_snapshot=None, bundle=_bundle(owners=(700, 701)))
    await escape_pet_command(msg, _command(args=None), repo, _chat_settings())
    msg.answer.assert_awaited_once()
    markup = msg.answer.call_args.kwargs.get("reply_markup")
    assert markup is not None
    buttons_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any("700" in d for d in buttons_data)
    assert any("701" in d for d in buttons_data)
