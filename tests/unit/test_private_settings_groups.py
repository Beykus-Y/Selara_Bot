from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import CHAT_SETTINGS_KEYS
from selara.core.config import Settings
from selara.domain.entities import UserChatOverview
from selara.presentation.handlers import private_settings
from selara.presentation.handlers.private_panel import (
    _build_admin_group_keyboard,
    _clear_pending_cfg_input,
    _get_pending_cfg_input,
)
from selara.presentation.handlers.settings_common import CFG_ENUM_VALUES
from selara.presentation.navigation.contract import MAX_CALLBACK_DATA_BYTES, callback_data_size
from selara.presentation.navigation.settings_groups import SETTINGS_CATEGORIES, category_index_for_key

CHAT_ID = -1001234567890
USER_ID = 42


def _settings() -> Settings:
    return Settings(
        BOT_TOKEN="token",
        DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
    )


def _overview() -> UserChatOverview:
    return UserChatOverview(
        chat_id=CHAT_ID,
        chat_type="supergroup",
        chat_title="Тестовая группа",
        bot_role=None,
        message_count=None,
        last_seen_at=None,
    )


def _query(data: str, chat_type: str = "private") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=USER_ID, username="admin", first_name="Admin", last_name=None, is_bot=False),
        message=SimpleNamespace(chat=SimpleNamespace(type=chat_type)),
        answer=AsyncMock(),
    )


def _repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_chat_settings = AsyncMock(return_value=None)
    return repo


def _key_index(key: str) -> int:
    return CHAT_SETTINGS_KEYS.index(key)


@pytest.fixture(autouse=True)
def _reset_pending_input():
    yield
    _clear_pending_cfg_input(USER_ID)


@pytest.fixture
def admin(monkeypatch):
    overview = AsyncMock(return_value=_overview())
    can_manage = AsyncMock(return_value=True)
    edit = AsyncMock()
    monkeypatch.setattr(private_settings, "_resolve_admin_chat_overview", overview)
    monkeypatch.setattr(private_settings, "_ensure_manage_settings", can_manage)
    monkeypatch.setattr(private_settings, "_edit_or_answer", edit)
    return SimpleNamespace(overview=overview, can_manage=can_manage, edit=edit)


def test_every_setting_belongs_to_exactly_one_category() -> None:
    grouped = [key for category in SETTINGS_CATEGORIES for key in category.settings]
    assert len(grouped) == len(set(grouped))
    assert sorted(grouped) == sorted(CHAT_SETTINGS_KEYS)


def test_category_slugs_are_unique() -> None:
    slugs = [category.slug for category in SETTINGS_CATEGORIES]
    assert len(slugs) == len(set(slugs))


def test_every_grouped_screen_callback_fits_telegram_limit() -> None:
    all_data: list[str] = []
    for index, _category in enumerate(SETTINGS_CATEGORIES):
        keyboard = private_settings._build_category_keyboard(CHAT_ID, index)
        all_data += [button.callback_data for row in keyboard.inline_keyboard for button in row]
    all_data += [
        button.callback_data
        for row in private_settings._build_home_keyboard(CHAT_ID).inline_keyboard
        for button in row
    ]
    for key in CHAT_SETTINGS_KEYS:
        current = True if key in private_settings.CFG_BOOL_KEYS else (
            CFG_ENUM_VALUES[key][0] if key in CFG_ENUM_VALUES else "x"
        )
        keyboard = private_settings._build_key_keyboard(chat_id=CHAT_ID, key=key, current_value=current)
        all_data += [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert all_data
    for data in all_data:
        assert callback_data_size(data) <= MAX_CALLBACK_DATA_BYTES, data


def test_key_screen_back_button_returns_to_its_category() -> None:
    for key in CHAT_SETTINGS_KEYS:
        keyboard = private_settings._build_key_keyboard(chat_id=CHAT_ID, key=key, current_value="x")
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        expected = f"pms:c:{CHAT_ID}:{category_index_for_key(key)}"
        assert expected in callbacks, key


def test_admin_group_keyboard_opens_grouped_settings() -> None:
    markup = _build_admin_group_keyboard(CHAT_ID)
    first_button = markup.inline_keyboard[0][0]
    assert first_button.text == "⚙️ Управление группой"
    assert first_button.callback_data == f"pms:h:{CHAT_ID}"


@pytest.mark.asyncio
async def test_home_and_category_screens_render_for_admin(admin) -> None:
    repo = _repo()

    await private_settings.private_settings_callback(_query(f"pms:h:{CHAT_ID}"), repo, _settings())
    home_text = admin.edit.call_args.args[1]
    assert "Управление группой" in home_text
    assert "/autocfg" in home_text

    await private_settings.private_settings_callback(_query(f"pms:c:{CHAT_ID}:0"), repo, _settings())
    category_markup = admin.edit.call_args.args[2]
    callbacks = [button.callback_data for row in category_markup.inline_keyboard for button in row]
    assert f"pms:k:{CHAT_ID}:{_key_index(SETTINGS_CATEGORIES[0].settings[0])}" in callbacks


@pytest.mark.asyncio
async def test_private_only(admin) -> None:
    repo = _repo()
    query = _query(f"pms:h:{CHAT_ID}", chat_type="group")

    await private_settings.private_settings_callback(query, repo, _settings())

    query.answer.assert_awaited_once_with("Доступно только в ЛС", show_alert=True)
    admin.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_forged_value_change_without_rights_is_rejected(admin) -> None:
    admin.can_manage.return_value = False
    repo = _repo()
    query = _query(f"pms:v:{CHAT_ID}:{_key_index('welcome_enabled')}:t")

    await private_settings.private_settings_callback(query, repo, _settings())

    query.answer.assert_awaited_once_with("Недостаточно прав", show_alert=True)
    repo.upsert_chat_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_no_longer_managed_is_rejected(admin) -> None:
    admin.overview.return_value = None
    repo = _repo()
    query = _query(f"pms:c:{CHAT_ID}:0")

    await private_settings.private_settings_callback(query, repo, _settings())

    query.answer.assert_awaited_once_with("Группа недоступна", show_alert=True)
    admin.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bool_value_change_saves_and_rerenders(admin) -> None:
    repo = _repo()
    query = _query(f"pms:v:{CHAT_ID}:{_key_index('welcome_enabled')}:t")

    await private_settings.private_settings_callback(query, repo, _settings())

    repo.upsert_chat_settings.assert_awaited_once()
    assert repo.upsert_chat_settings.call_args.kwargs["values"]["welcome_enabled"] is True
    admin.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enum_value_uses_variant_index_not_free_text(admin) -> None:
    repo = _repo()
    key = "daily_summary_style"
    variant_idx = CFG_ENUM_VALUES[key].index("snarky")
    query = _query(f"pms:v:{CHAT_ID}:{_key_index(key)}:e{variant_idx}")

    await private_settings.private_settings_callback(query, repo, _settings())

    repo.upsert_chat_settings.assert_awaited_once()
    assert repo.upsert_chat_settings.call_args.kwargs["values"]["daily_summary_style"] == "snarky"


@pytest.mark.asyncio
async def test_forged_enum_token_is_rejected(admin) -> None:
    repo = _repo()
    key = "daily_summary_style"
    query = _query(f"pms:v:{CHAT_ID}:{_key_index(key)}:e99")

    await private_settings.private_settings_callback(query, repo, _settings())

    query.answer.assert_awaited_once_with("Неверное значение", show_alert=True)
    repo.upsert_chat_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_input_sets_pending_and_leaving_screen_clears_it(admin) -> None:
    repo = _repo()
    key = "welcome_text"

    await private_settings.private_settings_callback(
        _query(f"pms:i:{CHAT_ID}:{_key_index(key)}"), repo, _settings()
    )
    pending = _get_pending_cfg_input(USER_ID)
    assert pending is not None
    assert pending.key == key
    assert pending.chat_id == CHAT_ID

    await private_settings.private_settings_callback(_query(f"pms:h:{CHAT_ID}"), repo, _settings())
    assert _get_pending_cfg_input(USER_ID) is None


@pytest.mark.asyncio
async def test_text_input_refused_for_bool_keys(admin) -> None:
    repo = _repo()
    query = _query(f"pms:i:{CHAT_ID}:{_key_index('welcome_enabled')}")

    await private_settings.private_settings_callback(query, repo, _settings())

    query.answer.assert_awaited_once()
    assert _get_pending_cfg_input(USER_ID) is None
