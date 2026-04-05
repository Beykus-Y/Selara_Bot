from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from aiogram.types import BufferedInputFile

from selara.presentation.handlers import text_commands


class _DummyMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.photo_calls: list[tuple[object, dict[str, object]]] = []
        self.text_calls: list[tuple[str, dict[str, object]]] = []

    async def answer_photo(self, photo, **kwargs) -> None:
        self.photo_calls.append((photo, kwargs))

    async def answer(self, text: str, **kwargs) -> None:
        self.text_calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_send_gacha_pull_downloads_remote_image_before_telegram_send(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(
        text_commands,
        "_load_gacha_custom_emoji_catalog",
        lambda: {
            "event_pull": text_commands._GachaCustomEmoji(custom_emoji_id="event-id", fallback="🎴"),
            "new_card": text_commands._GachaCustomEmoji(custom_emoji_id="new-card-id", fallback="🍀"),
            "epic_rarity": text_commands._GachaCustomEmoji(custom_emoji_id="epic-id", fallback="🟪"),
            "primogem": text_commands._GachaCustomEmoji(custom_emoji_id="primogem-id", fallback="💠"),
            "hydro": text_commands._GachaCustomEmoji(custom_emoji_id="hydro-id", fallback="💧"),
        },
    )

    monkeypatch.setattr(
        text_commands,
        "pull_gacha_card",
        AsyncMock(
            return_value=SimpleNamespace(
                message="🍀 Вы получили новую карту: Эмбер\nРедкость: 🟪 Эпическая\n💠 Примогемы: +10 [10]\n💧 Стихия: Гидро",
                card=SimpleNamespace(name="Эмбер", image_url="http://example.com/images/genshin/amber.jpg"),
                sell_offer=None,
                pull_id=10,
            )
        ),
    )
    monkeypatch.setattr(
        text_commands,
        "_fetch_gacha_image_file",
        AsyncMock(return_value=BufferedInputFile(b"image-bytes", filename="amber.jpg")),
    )

    await text_commands._send_gacha_pull(message, settings, banner="genshin")

    assert len(message.photo_calls) == 1
    photo, kwargs = message.photo_calls[0]
    assert isinstance(photo, BufferedInputFile)
    assert kwargs["caption"].startswith('<b><tg-emoji emoji-id="event-id">🎴</tg-emoji> Геншин</b>')
    assert '<tg-emoji emoji-id="new-card-id">🍀</tg-emoji>' in kwargs["caption"]
    assert 'Редкость: <b><tg-emoji emoji-id="epic-id">🟪</tg-emoji> Эпическая</b>' in kwargs["caption"]
    assert '<tg-emoji emoji-id="primogem-id">💠</tg-emoji>' in kwargs["caption"]
    assert '<tg-emoji emoji-id="hydro-id">💧</tg-emoji>' in kwargs["caption"]
    assert 'tg://user?id=1' in kwargs["caption"]
    assert kwargs["parse_mode"] == "HTML"
    assert message.text_calls == []


@pytest.mark.asyncio
async def test_send_gacha_pull_falls_back_to_text_when_image_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(
        text_commands,
        "_load_gacha_custom_emoji_catalog",
        lambda: {
            "event_pull": text_commands._GachaCustomEmoji(custom_emoji_id="event-id", fallback="🎴"),
            "new_card": text_commands._GachaCustomEmoji(custom_emoji_id="new-card-id", fallback="🍀"),
            "epic_rarity": text_commands._GachaCustomEmoji(custom_emoji_id="epic-id", fallback="🟪"),
            "primogem": text_commands._GachaCustomEmoji(custom_emoji_id="primogem-id", fallback="💠"),
            "hydro": text_commands._GachaCustomEmoji(custom_emoji_id="hydro-id", fallback="💧"),
        },
    )

    monkeypatch.setattr(
        text_commands,
        "pull_gacha_card",
        AsyncMock(
            return_value=SimpleNamespace(
                message="🍀 Вы получили новую карту: Эмбер\nРедкость: 🟪 Эпическая\n💠 Примогемы: +10 [10]\n💧 Стихия: Гидро",
                card=SimpleNamespace(name="Эмбер", image_url="http://example.com/images/genshin/amber.jpg"),
                sell_offer=None,
                pull_id=10,
            )
        ),
    )
    monkeypatch.setattr(
        text_commands,
        "_fetch_gacha_image_file",
        AsyncMock(side_effect=httpx.ConnectError("boom")),
    )

    await text_commands._send_gacha_pull(message, settings, banner="genshin")

    assert message.photo_calls == []
    assert len(message.text_calls) == 1
    assert message.text_calls[0][0].startswith('<b><tg-emoji emoji-id="event-id">🎴</tg-emoji> Геншин</b>')
    assert '<tg-emoji emoji-id="new-card-id">🍀</tg-emoji>' in message.text_calls[0][0]
    assert 'Редкость: <b><tg-emoji emoji-id="epic-id">🟪</tg-emoji> Эпическая</b>' in message.text_calls[0][0]
    assert '<tg-emoji emoji-id="primogem-id">💠</tg-emoji>' in message.text_calls[0][0]
    assert '<tg-emoji emoji-id="hydro-id">💧</tg-emoji>' in message.text_calls[0][0]
    assert message.text_calls[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_gacha_profile_renders_compact_html(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace()
    monkeypatch.setattr(
        text_commands,
        "_load_gacha_custom_emoji_catalog",
        lambda: {
            "primogem": text_commands._GachaCustomEmoji(custom_emoji_id="primogem-id", fallback="💠"),
            "mythic_rarity": text_commands._GachaCustomEmoji(custom_emoji_id="mythic-id", fallback="🟥"),
            "legendary_rarity": text_commands._GachaCustomEmoji(custom_emoji_id="legendary-id", fallback="🟨"),
            "epic_rarity": text_commands._GachaCustomEmoji(custom_emoji_id="epic-id", fallback="🟪"),
        },
    )
    monkeypatch.setattr(
        text_commands,
        "get_gacha_profile",
        AsyncMock(
            return_value=SimpleNamespace(
                message="ignored",
                player=SimpleNamespace(
                    adventure_rank=13,
                    xp_into_rank=1137,
                    xp_for_next_rank=2100,
                    total_points=674200,
                    total_primogems=498,
                ),
                unique_cards=51,
                total_copies=81,
                rarity_counts=[
                    SimpleNamespace(rarity="mythic", rarity_label="🟥 Мифическая", count=2),
                    SimpleNamespace(rarity="legendary", rarity_label="🟨 Легендарная", count=8),
                    SimpleNamespace(rarity="epic", rarity_label="🟪 Эпическая", count=38),
                ],
                recent_pulls=[
                    SimpleNamespace(
                        card_name="Яо Яо",
                        rarity_label="🟪 Эпическая",
                        pulled_at="2026-04-05T12:48:00+00:00",
                    )
                ],
            )
        ),
    )

    await text_commands._send_gacha_profile(message, settings, banner="genshin")

    assert message.photo_calls == []
    assert len(message.text_calls) == 1
    text, kwargs = message.text_calls[0]
    assert '<tg-emoji emoji-id="primogem-id">💠</tg-emoji> <b>Геншин</b>' in text
    assert "🧭 Ранг: <b>13</b> (1137 / 2100)" in text
    assert "⭐ Очки: <b>674 200</b> | <tg-emoji emoji-id=\"primogem-id\">💠</tg-emoji> Примогемы: <b>498</b>" in text
    assert (
        "📊 В коллекции: "
        '<tg-emoji emoji-id="mythic-id">🟥</tg-emoji> <b>2</b> | '
        '<tg-emoji emoji-id="legendary-id">🟨</tg-emoji> <b>8</b> | '
        '<tg-emoji emoji-id="epic-id">🟪</tg-emoji> <b>38</b>'
    ) in text
    assert "🕘 Последние крутки:" in text
    assert '<tg-emoji emoji-id="epic-id">🟪</tg-emoji> Яо Яо (<b>05.04 в 12:48</b>)' in text
    assert kwargs["parse_mode"] == "HTML"
