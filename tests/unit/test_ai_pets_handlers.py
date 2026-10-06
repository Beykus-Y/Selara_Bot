from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from selara.application.ai_pets import mechanics as m
from selara.infrastructure.db.ai_pets import ActionResult, CatalogItem, PetView
from selara.presentation.commands.access import resolve_command_key_input
from selara.presentation.commands.command_catalog import commands_for_category
from selara.presentation.handlers import ai_pets
from selara.presentation.middlewares.chat_write_lock import _LOCKED_CALLBACK_PREFIXES, is_write_locked_command

HANDLERS_DIR = Path(__file__).resolve().parents[2] / "src/selara/presentation/handlers"


def _pet(**overrides) -> PetView:
    values = dict(
        id=7, owner_user_id=1, name="Мурка", species_key="cat", species_custom=None, traits=("playful",),
        level=2, xp=60, mood=72, satiety=55, energy=80, status="active", dormant_reason=None,
        current_chat_id=-1, home_chat_id=-1, travel_unlocked=False,
    )
    values.update(overrides)
    return PetView(**values)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("пет", ("panel", None)),
        ("Пет", ("panel", None)),
        ("петы", ("list", None)),
        ("пет погладить", ("pat", None)),
        ("пет погладить Сэр Мурр", ("pat", "Сэр Мурр")),
        ("пет поиграть мурка", ("play", "мурка")),
        ("пет покормить", ("feed", None)),
        ("пет подразнить Мурка", ("tease", "Мурка")),
        ("пет обидеть", ("hurt", None)),
        ("пет магазин", ("shop", None)),
        ("пет и кот", None),
        ("петух", None),
        ("питомец", None),
    ],
)
def test_parse_pet_text(text: str, expected) -> None:
    assert ai_pets.parse_pet_text(text) == expected


def _message(text: str, reply_from=None) -> SimpleNamespace:
    reply = SimpleNamespace(from_user=reply_from) if reply_from is not None else None
    return SimpleNamespace(text=text, reply_to_message=reply)


def test_bare_pet_command_detection_keeps_the_role_play_form() -> None:
    person = SimpleNamespace(is_bot=False)
    bot = SimpleNamespace(is_bot=True)
    assert ai_pets.is_bare_pet_command(_message("/pet"))
    assert ai_pets.is_bare_pet_command(_message("/pet@selara_bot"))
    assert ai_pets.is_bare_pet_command(_message("/pet", reply_from=bot))  # reply to the pet's card
    assert not ai_pets.is_bare_pet_command(_message("/pet @user"))
    assert not ai_pets.is_bare_pet_command(_message("/pet", reply_from=person))


def test_card_shows_level_progress_traits_and_attitude() -> None:
    text = ai_pets.render_card(_pet(), owner_label="<a>Илья</a>", viewer_affinity=30, top=[("<a>Лиза</a>", 70)])
    assert "Мурка" in text and "ур. 2 (10/100 XP)" in text
    assert "игривый" in text
    assert "К вам: доверяет" in text
    assert "Лиза" in text and "обожает" in text


def test_card_escapes_user_text() -> None:
    text = ai_pets.render_card(_pet(name="<b>x</b>", species_key="custom", species_custom="<i>"), owner_label="o", viewer_affinity=None)
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;" in text


def test_result_mentions_effects_price_and_level_up() -> None:
    item = CatalogItem(code="ball", title="Мячик", kind="toy", price=120, effects=m.ItemEffects(mood=10), min_level=1)
    result = ActionResult(
        status="ok", pet=_pet(), applied={"mood": 10, "xp": 5, "energy": -10}, leveled_up_to=m.TRAVEL_UNLOCK_LEVEL,
        item=item, new_balance=880,
    )
    text = ai_pets.render_result(result, event_type="toy", actor_link="<a>Лиза</a>")
    assert "настроение +10" in text and "энергия -10" in text and "XP +5" in text
    assert "Потрачено 120 монет, баланс: 880." in text
    assert f"{m.TRAVEL_UNLOCK_LEVEL} уровня" in text and "путешествия" in text.lower()


def test_keyboard_buttons_fit_callback_limits_and_are_write_locked() -> None:
    keyboard = ai_pets.pet_keyboard(2**40)
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert all(len(item.encode()) <= 64 for item in data)
    assert f"aipet:a:{2**40}:pat" in data and f"aipet:shop:{2**40}" in data
    assert any(prefix == ai_pets.CALLBACK_PREFIX for prefix in _LOCKED_CALLBACK_PREFIXES)


@pytest.mark.parametrize("command", ["pet", "pets", "pet_new", "pet_shop"])
def test_pet_commands_are_write_locked(command: str) -> None:
    assert is_write_locked_command(command)


def test_admin_sleep_commands_are_not_rank_gated_by_the_pet_key() -> None:
    # They check manage_settings themselves; a rank rule on «pet» must not hide them from admins.
    assert resolve_command_key_input("/pet_sleep Мурка") is None
    assert resolve_command_key_input("/pet_traits игривый") == "pet"


def test_pets_catalog_syntax_matches_real_registrations() -> None:
    import re

    source = (HANDLERS_DIR / "ai_pets.py").read_text(encoding="utf-8")
    real = set(re.findall(r'@router\.message\(Command\("([a-z_]+)"', source))
    for spec in commands_for_category("pets"):
        for entry in spec.syntax:
            assert entry.split()[0][1:] in real, entry


def test_today_uses_bot_timezone() -> None:
    from datetime import datetime, timezone

    late_utc = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
    assert ai_pets._today(SimpleNamespace(bot_timezone="Asia/Barnaul"), late_utc).isoformat() == "2026-10-07"
    assert ai_pets._today(SimpleNamespace(bot_timezone="Nowhere/Bad"), late_utc).isoformat() == "2026-10-06"
