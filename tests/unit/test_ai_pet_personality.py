from __future__ import annotations

from datetime import date

from selara.application.ai_pets import mechanics as m
from selara.application.ai_pets import personality as p


def test_traits_need_some_history_and_follow_behaviour() -> None:
    assert p.derive_traits({"pat": 9}) == []
    assert p.derive_traits({"play": 8, "toy": 4, "pat": 3}) == ["playful"]
    assert p.derive_traits({"pat": 12, "custom_care": 3}) == ["affectionate"]
    assert p.derive_traits({"tease": 8, "pat": 2, "feed": 2}) == ["grumpy"]
    assert p.derive_traits({"hurt": 5, "pat": 5}) == ["affectionate", "shy"]


def test_traits_are_capped_stable_and_never_user_picked() -> None:
    counts = {"play": 10, "pat": 10, "feed": 10}
    traits = p.derive_traits(counts, top_affinity=90)  # four candidates (with «loyal»), three kept
    assert traits == ["affectionate", "greedy", "playful"]
    assert len(traits) == m.MAX_TRAITS
    assert traits == p.derive_traits(dict(reversed(list(counts.items()))), top_affinity=90)
    assert set(traits) <= set(m.TRAITS)


def test_lazy_calm_and_loyal() -> None:
    assert "lazy" in p.derive_traits({"feed": 10, "pat": 12})
    assert "calm" in p.derive_traits({"feed": 8, "pat": 8, "talk": 2, "play": 3})
    assert "loyal" in p.derive_traits({"pat": 10}, top_affinity=80)
    assert "loyal" not in p.derive_traits({"pat": 10}, top_affinity=40)


def test_mood_of_the_day_is_stable_per_day_and_follows_mood() -> None:
    day = date(2026, 10, 7)
    first = p.mood_of_the_day(pet_id=3, day=day, mood=90, traits=("playful",))
    assert first == p.mood_of_the_day(pet_id=3, day=day, mood=90, traits=("playful",))
    seen = {p.mood_of_the_day(pet_id=3, day=date(2026, 10, d), mood=90) for d in range(1, 15)}
    assert len(seen) > 1
    assert p.mood_of_the_day(pet_id=3, day=day, mood=5) != p.mood_of_the_day(pet_id=3, day=day, mood=95)


def test_attitude_to_the_group_weights_by_interactions() -> None:
    assert p.group_attitude([]) == "ещё присматривается к чату"
    assert p.group_attitude([(80, 10), (-50, 1)]) == "тепло относится к чату"
    assert p.group_attitude([(20, 6)]) == "в целом дружелюбно относится к чату"
    assert p.group_attitude([(0, 8)]) == "нейтрально относится к чату"
    assert p.group_attitude([(-60, 8)]) == "настороженно относится к чату"
