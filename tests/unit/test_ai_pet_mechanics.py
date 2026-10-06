from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from selara.application.ai_pets import mechanics as m

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 6)


def _stats(**overrides) -> m.PetStats:
    values = dict(level=1, xp=0, mood=70, satiety=70, energy=70, last_tick_at=NOW)
    values.update(overrides)
    return m.PetStats(**values)


def _relation(**overrides) -> m.RelationState:
    values = dict(affinity=0, affinity_gained_today=0, xp_gained_today=0, gained_day=TODAY)
    values.update(overrides)
    return m.RelationState(**values)


def test_tick_applies_whole_hours_and_keeps_the_remainder() -> None:
    stats = _stats(last_tick_at=NOW - timedelta(hours=2, minutes=40))
    ticked = m.apply_tick(stats, NOW)
    assert ticked.satiety == 70 - 2 * m.TICK_SATIETY_PER_HOUR
    assert ticked.mood == 70 - 2 * m.TICK_MOOD_PER_HOUR
    assert ticked.energy == 70 + 2 * m.TICK_ENERGY_REGEN_PER_HOUR
    assert ticked.last_tick_at == NOW - timedelta(minutes=40)
    assert m.apply_tick(ticked, NOW) == ticked


def test_tick_never_leaves_bounds_and_hunger_hurts_mood_more() -> None:
    long_ago = _stats(last_tick_at=NOW - timedelta(days=60))
    ticked = m.apply_tick(long_ago, NOW)
    assert (ticked.satiety, ticked.mood, ticked.energy) == (0, 0, 100)

    fed = m.apply_tick(_stats(satiety=100, mood=100, last_tick_at=NOW - timedelta(hours=5)), NOW)
    hungry = m.apply_tick(_stats(satiety=20, mood=100, last_tick_at=NOW - timedelta(hours=5)), NOW)
    assert hungry.mood < fed.mood


def test_positive_affinity_is_capped_per_day_but_losses_are_not() -> None:
    relation = _relation(affinity_gained_today=m.AFFINITY_DAILY_GAIN_CAP - 1)
    outcome = m.apply_effect(_stats(), relation, m.Effect(affinity=5), today=TODAY)
    assert outcome.applied["affinity"] == 1
    capped = m.apply_effect(outcome.stats, outcome.relation, m.Effect(affinity=5), today=TODAY)
    assert capped.applied["affinity"] == 0
    hurt = m.apply_effect(capped.stats, capped.relation, m.Effect(affinity=-30), today=TODAY)
    assert hurt.applied["affinity"] == -30


def test_daily_counters_reset_on_a_new_day() -> None:
    relation = _relation(affinity_gained_today=m.AFFINITY_DAILY_GAIN_CAP, xp_gained_today=99, gained_day=TODAY)
    outcome = m.apply_effect(_stats(), relation, m.Effect(affinity=3, xp=4), today=TODAY + timedelta(days=1))
    assert outcome.applied["affinity"] == 3
    assert outcome.applied["xp"] == 4
    assert outcome.relation.gained_day == TODAY + timedelta(days=1)


def test_xp_has_diminishing_returns_per_person_per_day() -> None:
    stats, relation = _stats(), _relation()
    awarded = 0
    for _ in range(30):  # 30 x 2 = 60 raw points
        outcome = m.apply_effect(stats, relation, m.Effect(xp=2), today=TODAY)
        stats, relation = outcome.stats, outcome.relation
        awarded += outcome.applied["xp"]
    assert awarded == m.XP_FULL_PER_DAY + (m.XP_HALF_PER_DAY - m.XP_FULL_PER_DAY) // 2
    assert stats.xp == awarded


def test_levels_follow_the_threshold_curve_and_report_level_up() -> None:
    assert m.level_for_xp(0) == 1
    assert m.level_for_xp(49) == 1
    assert m.level_for_xp(50) == 2
    assert m.level_for_xp(50 + 100) == 3
    assert m.level_progress(60) == (2, 10, 100)
    outcome = m.apply_effect(_stats(xp=48), _relation(), m.Effect(xp=5), today=TODAY)
    assert outcome.leveled_up_to == 2
    assert outcome.stats.level == 2


def test_cooldown_and_block_reasons() -> None:
    action = m.ACTIONS["play"]
    assert m.cooldown_left(None, action.cooldown, NOW) is None
    assert m.cooldown_left(NOW - timedelta(minutes=5), action.cooldown, NOW) == timedelta(minutes=25)
    assert m.cooldown_left(NOW - timedelta(hours=1), action.cooldown, NOW) is None
    assert m.action_block_reason(action, _stats(energy=5)) is not None
    assert m.action_block_reason(action, _stats(energy=50)) is None
    assert m.item_block_reason("food", _stats(satiety=m.FOOD_FULL_AT)) is not None
    assert m.item_block_reason("toy", _stats(energy=0)) is not None


@pytest.mark.parametrize("name", ["Мурка", "Сэр Ланселот", "Rex-2", "O'Malley"])
def test_valid_names(name: str) -> None:
    assert m.validate_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "x" * (m.NAME_MAX_LEN + 1), "@admin", "t.me/spam", "http://x", "Мур_ка", "-Мурка", "сука"]
)
def test_invalid_names_are_rejected(name: str) -> None:
    with pytest.raises(m.PetValidationError):
        m.validate_name(name)


def test_name_normalisation_is_case_and_yo_insensitive() -> None:
    assert m.normalize_name("  Ёжик   Пётр ") == m.normalize_name("ежик петр")


def test_species_aliases_and_custom_species() -> None:
    assert m.resolve_species("Кот") == ("cat", None)
    assert m.resolve_species("пёс") == ("dog", None)
    assert m.resolve_species("Ёжик") == ("custom", "Ёжик")
    with pytest.raises(m.PetValidationError):
        m.resolve_species("x" * (m.SPECIES_CUSTOM_MAX_LEN + 1))


def test_traits_parse_titles_dedupe_and_limit() -> None:
    assert m.parse_traits("Игривый, ласковый игривый") == ["playful", "affectionate"]
    with pytest.raises(m.PetValidationError):
        m.parse_traits("игривый, ласковый, смелый, гордый")
    with pytest.raises(m.PetValidationError):
        m.parse_traits("злобный")


def test_item_effects_validation_rejects_broken_catalog_rows() -> None:
    assert m.parse_item_effects({"satiety": 15, "mood": 2}, kind="food") == m.ItemEffects(satiety=15, mood=2)
    assert m.parse_item_effects({"mood": 10}, kind="toy") == m.ItemEffects(mood=10)
    assert m.parse_item_effects({"mood": 10}, kind="food") is None  # food must feed
    assert m.parse_item_effects({"satiety": "15"}, kind="food") is None
    assert m.parse_item_effects({"satiety": True}, kind="food") is None
    assert m.parse_item_effects({"satiety": 15, "gold": 1}, kind="food") is None
    assert m.parse_item_effects({"satiety": 15, "affinity": 50}, kind="food") is None
    assert m.parse_item_effects("{\"satiety\": 15}", kind="food") is None
    assert m.parse_item_effects({"satiety": 15}, kind="cosmetic") is None


def test_toy_costs_energy_on_top_of_its_effects() -> None:
    effect = m.item_effect("toy", m.ItemEffects(mood=10, energy=0))
    assert effect.energy == -m.TOY_ENERGY_COST
    assert m.item_effect("food", m.ItemEffects(satiety=10)).energy == 0


def test_labels_and_reply_templates() -> None:
    assert m.affinity_label(80) == "обожает"
    assert m.affinity_label(0) == "нейтрален"
    assert m.affinity_label(-90) == "злится"
    text = m.reply_text("feed", name="Мурка", species_key="cat", item="лакомство")
    assert "Мурка" in text and "🐱" in text
    assert "{" not in m.reply_text("pat", name="X", species_key="unknown")
