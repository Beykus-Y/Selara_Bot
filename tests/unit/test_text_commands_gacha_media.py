import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from aiogram.exceptions import TelegramBadRequest
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

    async def answer(self, text: str, **kwargs):
        self.text_calls.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.text_calls) + 9000, chat=self.chat)


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

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=False))
    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

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

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=False))
    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    assert message.photo_calls == []
    assert len(message.text_calls) == 1
    assert message.text_calls[0][0].startswith('<b><tg-emoji emoji-id="event-id">🎴</tg-emoji> Геншин</b>')
    assert '<tg-emoji emoji-id="new-card-id">🍀</tg-emoji>' in message.text_calls[0][0]
    assert 'Редкость: <b><tg-emoji emoji-id="epic-id">🟪</tg-emoji> Эпическая</b>' in message.text_calls[0][0]
    assert '<tg-emoji emoji-id="primogem-id">💠</tg-emoji>' in message.text_calls[0][0]
    assert '<tg-emoji emoji-id="hydro-id">💧</tg-emoji>' in message.text_calls[0][0]
    assert message.text_calls[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_gacha_pull_rejects_concurrent_text_command_from_same_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The callback ('гача инфо' buttons) path already had per-message
    in-flight protection (_GACHA_CALLBACK_IN_FLIGHT); the plain-text
    command 'гача генш' had none — a fast double-send could trigger two
    concurrent pulls (see docs/GACHA_MODERNIZATION_TODO.md, Этап 0's
    finding). The underlying gacha-service advisory lock already makes the
    *game result* safe either way, but this avoids sending two Telegram
    messages/animations for what the user meant as one action."""
    message1 = _DummyMessage()
    message2 = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})

    started = asyncio.Event()
    release = asyncio.Event()

    async def pull(*args, **kwargs):
        _ = (args, kwargs)
        started.set()
        await release.wait()
        return _gacha_pull_response_with_card()

    monkeypatch.setattr(text_commands, "pull_gacha_card", pull)
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=False))

    task1 = asyncio.create_task(
        text_commands._send_gacha_pull(message1, settings, bot, activity_repo, banner="genshin")
    )
    await started.wait()
    # Bounded wait: without the in-flight guard, message2 would also call
    # the (still-blocked-on-release) mocked pull_gacha_card and hang here
    # forever instead of returning quickly with a rejection.
    await asyncio.wait_for(
        text_commands._send_gacha_pull(message2, settings, bot, activity_repo, banner="genshin"), timeout=1.0
    )
    release.set()
    await task1

    assert message2.photo_calls == []
    assert len(message2.text_calls) == 1
    assert "уже" in message2.text_calls[0][0].lower()
    assert len(message1.photo_calls) == 1


def _gacha_pull_response_with_card() -> SimpleNamespace:
    return SimpleNamespace(
        message="🍀 Вы получили новую карту: Эмбер",
        card=SimpleNamespace(
            code="amber",
            name="Эмбер",
            rarity="epic",
            rarity_label="🟪 Эпическая",
            image_url="http://example.com/images/genshin/amber.jpg",
        ),
        sell_offer=None,
        pull_id=10,
    )


@pytest.mark.asyncio
async def test_send_gacha_pull_sends_animation_first_when_enabled_and_caches_new_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Этап 3 of docs/GACHA_MODERNIZATION_TODO.md: when the per-user
    animation mode is on, the reel animation is sent, held, and deleted
    before the normal instant-result message — and a freshly generated
    variant gets cached under the resulting Telegram file_id."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands,
        "_fetch_gacha_image_file",
        AsyncMock(return_value=BufferedInputFile(b"image-bytes", filename="amber.jpg")),
    )
    resolved = SimpleNamespace(payload="fresh-bytes-payload", needs_caching=True, cache_version="v1")
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    cache_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", cache_mock)
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    sent_message = SimpleNamespace(message_id=999, animation=SimpleNamespace(file_id="new-telegram-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    bot.send_animation.assert_awaited_once()
    assert bot.send_animation.await_args.kwargs["animation"] == "fresh-bytes-payload"
    cache_mock.assert_awaited_once_with(
        activity_repo=activity_repo,
        banner="genshin",
        card_code="amber",
        telegram_file_id="new-telegram-file-id",
        cache_version="v1",
    )
    bot.delete_message.assert_awaited_once_with(chat_id=message.chat.id, message_id=999)
    # the normal result message must still be sent after the animation
    assert len(message.photo_calls) == 1


@pytest.mark.asyncio
async def test_send_gacha_pull_retries_via_local_or_render_when_stored_file_id_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier-1 file_id can go stale server-side; retrying the exact same
    file_id would just fail again, so a send failure must force a retry
    that bypasses the DB lookup (falls to local disk / re-render), not give
    up on the animation immediately."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    stale = SimpleNamespace(payload="stale-file-id", needs_caching=False, cache_version="v1")
    fresh = SimpleNamespace(payload="fresh-bytes-payload", needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(side_effect=[stale, fresh])
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)
    cache_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", cache_mock)
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    sent_message = SimpleNamespace(message_id=999, animation=SimpleNamespace(file_id="new-telegram-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(side_effect=[TelegramBadRequest(method="sendAnimation", message="wrong file identifier"), sent_message])
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    assert bot.send_animation.await_count == 2
    assert resolve_mock.await_args_list[1].kwargs["skip_cached_lookup"] is True
    cache_mock.assert_awaited_once_with(
        activity_repo=activity_repo,
        banner="genshin",
        card_code="amber",
        telegram_file_id="new-telegram-file-id",
        cache_version="v1",
    )
    assert len(message.photo_calls) == 1


@pytest.mark.asyncio
async def test_send_gacha_pull_reuses_cached_variant_without_recaching(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    resolved = SimpleNamespace(payload="cached-file-id", needs_caching=False, cache_version="v1")
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    cache_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", cache_mock)
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=1, animation=None))
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    bot.send_animation.assert_awaited_once()
    cache_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_gacha_pull_shows_preparing_message_when_cache_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/GACHA_MODERNIZATION_TODO.md, раздел 10: while the animation
    cache isn't warm yet (fresh deploy), don't attempt a live render at
    all — tell the user to try later. The real pull result must still
    arrive normally, just without the animation."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=False))
    resolve_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    bot = AsyncMock()
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")
    # the notice is fire-and-forget (must not delay the result above) — give
    # its background task a couple of real loop iterations to actually run
    # (text_commands.asyncio.sleep is mocked above, so use the real sleep).
    for _ in range(5):
        await real_sleep(0)

    resolve_mock.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    assert any("подготов" in text.lower() for text, _kwargs in message.text_calls)
    assert len(message.photo_calls) == 1
    # the "preparing" notice must not linger in the chat forever
    bot.delete_message.assert_awaited_once()
    assert bot.delete_message.await_args.kwargs["chat_id"] == message.chat.id


@pytest.mark.asyncio
async def test_send_gacha_pull_preparing_notice_does_not_delay_result_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 7s hold on the 'preparing' notice is purely cosmetic (so it's
    readable before it vanishes) — it must never delay the real result,
    unlike the animation's hold which the user is actually meant to watch."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=False))

    never_resolves = asyncio.Event()
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == text_commands._GACHA_PREPARING_HOLD_SECONDS:
            await never_resolves.wait()
        else:
            await real_sleep(0)

    monkeypatch.setattr(text_commands.asyncio, "sleep", fake_sleep)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await asyncio.wait_for(
        text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin"), timeout=1.0
    )

    assert len(message.photo_calls) == 1


@pytest.mark.asyncio
async def test_send_gacha_pull_still_delivers_result_when_animation_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard constraint from docs/GACHA_MODERNIZATION_TODO.md: an animation
    failure must never cost the user their real (already-computed) result."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(
        text_commands, "resolve_gacha_reel_animation", AsyncMock(side_effect=RuntimeError("gacha service down"))
    )
    cache_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", cache_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    bot.send_animation.assert_not_awaited()
    cache_mock.assert_not_awaited()
    assert len(message.photo_calls) == 1


@pytest.mark.asyncio
async def test_send_gacha_pull_still_delivers_result_when_readiness_check_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-deploy audit finding (2026-08-19): is_gacha_animation_cache_ready
    calls out to the gacha service (network) and was NOT wrapped in
    try/except at the call site — if it raised (e.g. gacha-service briefly
    unreachable), the already-computed pull result would never be
    delivered at all, reproducing exactly the 'lost card' bug class Этап 0
    was built to eliminate, via a new code path."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(
        text_commands, "is_gacha_animation_cache_ready", AsyncMock(side_effect=RuntimeError("gacha service down"))
    )
    resolve_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    resolve_mock.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    assert len(message.photo_calls) == 1


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
