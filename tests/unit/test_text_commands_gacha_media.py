import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

from selara.presentation.handlers import text_commands


@pytest.fixture(autouse=True)
def _no_background_reel_render(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """A pull whose card has no MP4 yet starts a background render. These tests stub it out and check the call."""
    render = AsyncMock()
    monkeypatch.setattr(text_commands, "ensure_gacha_reel_on_disk", render)
    return render


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


async def _drain_gacha_background() -> None:
    """The animation hold, its deletion and the result run as background tasks after the pull returns."""
    while text_commands._GACHA_BACKGROUND_TASKS:
        await asyncio.gather(*list(text_commands._GACHA_BACKGROUND_TASKS), return_exceptions=True)


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
    await _drain_gacha_background()

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
    await _drain_gacha_background()

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
    await _drain_gacha_background()

    bot.send_animation.assert_awaited_once()
    cache_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_gacha_pull_without_cached_animation_delivers_result_and_renders_in_background(
    monkeypatch: pytest.MonkeyPatch, _no_background_reel_render: AsyncMock
) -> None:
    """docs/GACHA_MODERNIZATION_TODO.md, раздел 10: a card without an MP4 yet gets no animation and no "preparing" notice.
    The pull never renders inside its own handler. The card is rendered in the background for the next pull, and the real
    result arrives normally."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    resolve_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")
    await _drain_gacha_background()

    assert resolve_mock.await_args.kwargs["allow_render"] is False
    bot.send_animation.assert_not_awaited()
    assert not any("подготов" in text.lower() for text, _kwargs in message.text_calls)
    assert len(message.photo_calls) == 1
    _no_background_reel_render.assert_awaited_once()
    assert _no_background_reel_render.await_args.kwargs["banner"] == "genshin"
    assert _no_background_reel_render.await_args.kwargs["landing_card_code"] == "amber"
    assert _no_background_reel_render.await_args.kwargs["landing_rarity"] == "epic"


@pytest.mark.asyncio
async def test_background_render_does_not_delay_the_result_of_an_uncached_pull(
    monkeypatch: pytest.MonkeyPatch, _no_background_reel_render: AsyncMock
) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", AsyncMock(return_value=None))
    render_finishes = asyncio.Event()

    async def render_that_takes_forever(**_kwargs) -> None:
        await render_finishes.wait()

    _no_background_reel_render.side_effect = render_that_takes_forever
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    try:
        await asyncio.wait_for(
            text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin"), timeout=1.0
        )
        assert len(message.photo_calls) == 1
    finally:
        render_finishes.set()
        await _drain_gacha_background()


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
async def test_send_gacha_pull_still_delivers_result_when_animation_preference_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-deploy audit finding (2026-08-19): a failing database lookup must not swallow the already-computed pull result."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    resolve_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(
        is_gacha_animation_enabled=AsyncMock(side_effect=RuntimeError("database went away"))
    )

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


@pytest.mark.asyncio
async def test_send_gacha_pull_returns_while_the_animation_is_held_and_keeps_the_pull_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The update must not wait out the animation hold. The hold, the deletion
    and the result run in the background, and the user stays marked in flight
    until the result is out, so a second pull cannot start during the hold."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands,
        "_fetch_gacha_image_file",
        AsyncMock(return_value=BufferedInputFile(b"image-bytes", filename="amber.jpg")),
    )
    resolved = SimpleNamespace(payload="fresh-bytes-payload", needs_caching=True, cache_version="v1")
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", AsyncMock())

    real_sleep = asyncio.sleep
    hold_released = asyncio.Event()

    async def fake_sleep(delay):
        if delay == text_commands._GACHA_ANIMATION_HOLD_SECONDS:
            await hold_released.wait()
        else:
            await real_sleep(0)

    monkeypatch.setattr(text_commands.asyncio, "sleep", fake_sleep)

    sent_message = SimpleNamespace(message_id=999, animation=SimpleNamespace(file_id="new-telegram-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin")

    assert message.from_user.id in text_commands._GACHA_TEXT_PULL_IN_FLIGHT
    bot.delete_message.assert_not_awaited()
    assert len(message.photo_calls) == 0

    hold_released.set()
    await _drain_gacha_background()

    bot.delete_message.assert_awaited_once_with(chat_id=message.chat.id, message_id=999)
    assert len(message.photo_calls) == 1
    assert message.from_user.id not in text_commands._GACHA_TEXT_PULL_IN_FLIGHT


@pytest.mark.asyncio
async def test_shutdown_ends_the_animation_hold_and_still_delivers_the_pull_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pull is committed before its hold starts. A restart during the hold must still send the result,
    without waiting out the whole hold first."""
    monkeypatch.setattr(text_commands, "_GACHA_SHUTDOWN", asyncio.Event())
    monkeypatch.setattr(text_commands, "_GACHA_ANIMATION_HOLD_SECONDS", 60.0)
    delete_mock = AsyncMock()
    deliver_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "_delete_gacha_animation", delete_mock)
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)
    message = _DummyMessage()
    text_commands._GACHA_TEXT_PULL_IN_FLIGHT.add(message.from_user.id)

    text_commands._spawn_gacha_background(
        text_commands._finish_gacha_pull_after_animation(
            message,
            SimpleNamespace(),
            AsyncMock(),
            banner="genshin",
            response=_gacha_pull_response_with_card(),
            owner_user_id=message.from_user.id,
            sent=SimpleNamespace(message_id=999),
        )
    )
    await asyncio.sleep(0)

    await asyncio.wait_for(text_commands.drain_gacha_background_tasks(timeout=5), timeout=10)

    delete_mock.assert_awaited_once()
    deliver_mock.assert_awaited_once()
    assert message.from_user.id not in text_commands._GACHA_TEXT_PULL_IN_FLIGHT


@pytest.mark.asyncio
async def test_send_gacha_pull_ends_the_db_transaction_after_caching_the_animation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pooled connection must not sit in an open transaction through the
    upload or the animation: the caller's session is committed before the
    upload and again right after the variant is cached."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands,
        "_fetch_gacha_image_file",
        AsyncMock(return_value=BufferedInputFile(b"image-bytes", filename="amber.jpg")),
    )
    resolved = SimpleNamespace(payload="fresh-bytes-payload", needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(return_value=resolved)
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)
    events: list[str] = []
    monkeypatch.setattr(
        text_commands,
        "cache_reel_variant_after_send",
        AsyncMock(side_effect=lambda **_kwargs: events.append("cache")),
    )
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    sent_message = SimpleNamespace(message_id=999, animation=SimpleNamespace(file_id="new-telegram-file-id"))

    def record_upload(**_kwargs):
        events.append("upload")
        return sent_message

    bot = AsyncMock()
    bot.send_animation = AsyncMock(side_effect=record_upload)
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))
    db_session = SimpleNamespace(commit=AsyncMock(side_effect=lambda: events.append("commit")))

    await text_commands._send_gacha_pull(
        message, settings, bot, activity_repo, banner="genshin", db_session=db_session
    )
    await _drain_gacha_background()

    # The transaction ends before the upload and again after the cache write.
    assert events == ["commit", "upload", "cache", "commit"]
    assert len(message.photo_calls) == 1


@pytest.mark.asyncio
async def test_send_gacha_pull_ends_the_db_transaction_when_the_animation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed animation falls back to the instant result, and the transaction still ends before that result is sent."""
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)
    monkeypatch.setattr(text_commands, "_load_gacha_custom_emoji_catalog", lambda: {})
    monkeypatch.setattr(text_commands, "pull_gacha_card", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(
        text_commands, "_fetch_gacha_image_file", AsyncMock(return_value=BufferedInputFile(b"x", filename="a.jpg"))
    )
    monkeypatch.setattr(
        text_commands, "resolve_gacha_reel_animation", AsyncMock(side_effect=RuntimeError("gacha service down"))
    )
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))
    db_session = SimpleNamespace(commit=AsyncMock())

    await text_commands._send_gacha_pull(message, settings, bot, activity_repo, banner="genshin", db_session=db_session)

    db_session.commit.assert_awaited_once()
    bot.send_animation.assert_not_awaited()
    assert len(message.photo_calls) == 1


_GACHA_CHAT_SETTINGS = SimpleNamespace(economy_mode="global", gacha_enabled=True)


def _gacha_callback_query(message: _DummyMessage) -> SimpleNamespace:
    return SimpleNamespace(data="gacha:buy:genshin:u1", from_user=message.from_user, message=message, answer=AsyncMock())


def _paid_pull_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.send_animation = AsyncMock(
        return_value=SimpleNamespace(message_id=555, animation=SimpleNamespace(file_id="paid-file-id"))
    )
    bot.delete_message = AsyncMock()
    return bot


def _patch_paid_pull_flow(monkeypatch: pytest.MonkeyPatch, *, deliver: AsyncMock) -> None:
    """Everything around a paid pull's handler, except the animation hold and the result send, which the tests drive."""
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", AsyncMock(return_value=_gacha_pull_response_with_card()))
    monkeypatch.setattr(text_commands, "_require_channel_subscription_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands,
        "resolve_gacha_reel_animation",
        AsyncMock(return_value=SimpleNamespace(payload="fresh-bytes-payload", needs_caching=False, cache_version="v1")),
    )
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver)
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", AsyncMock(return_value=("<b>Гача инфо</b>", None)))
    monkeypatch.setattr(text_commands, "_run_gacha_message_edit", AsyncMock())
    monkeypatch.setattr(text_commands, "_safe_callback_answer", AsyncMock())


@pytest.fixture
def fresh_gacha_shutdown_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A drain in one test must neither leave shutdown set nor wait on handlers that other tests left behind."""
    monkeypatch.setattr(text_commands, "_GACHA_SHUTDOWN", asyncio.Event())
    monkeypatch.setattr(text_commands, "_GACHA_BACKGROUND_TASKS", set())
    monkeypatch.setattr(text_commands, "_GACHA_PULL_HANDLER_TASKS", set())


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_paid_pull_shutdown_ends_the_hold_and_drain_waits_for_its_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paid pull is committed before its animation hold. A restart during the hold must end the hold early, and the
    drain must wait for the update handler until the result is out."""
    monkeypatch.setattr(text_commands, "_GACHA_ANIMATION_HOLD_SECONDS", 60.0)
    real_hold = text_commands._hold_gacha_animation
    hold_started = asyncio.Event()

    async def hold_and_signal() -> None:
        hold_started.set()
        await real_hold()

    monkeypatch.setattr(text_commands, "_hold_gacha_animation", hold_and_signal)
    deliver = AsyncMock()
    _patch_paid_pull_flow(monkeypatch, deliver=deliver)
    message = _DummyMessage()
    bot = _paid_pull_bot()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    handler = asyncio.create_task(
        text_commands.gacha_callback(
            _gacha_callback_query(message),
            bot=bot,
            settings=SimpleNamespace(gacha_timeout_seconds=10.0),
            economy_repo=object(),
            activity_repo=activity_repo,
            chat_settings=_GACHA_CHAT_SETTINGS,
        )
    )
    await asyncio.wait_for(hold_started.wait(), timeout=5)

    await text_commands.drain_gacha_background_tasks(timeout=5)

    assert handler.done()
    await handler
    bot.delete_message.assert_awaited_once_with(chat_id=message.chat.id, message_id=555)
    deliver.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_shutdown_waits_for_a_result_send_that_takes_longer_than_five_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain used to stop after five seconds. A paid result that Telegram needs longer than that to accept must
    still go out, because the pull is already charged."""
    deliver_started = asyncio.Event()
    delivered: list[str] = []

    async def slow_deliver(*_args, **_kwargs) -> None:
        deliver_started.set()
        await asyncio.sleep(5.5)
        delivered.append("result")

    _patch_paid_pull_flow(monkeypatch, deliver=AsyncMock(side_effect=slow_deliver))
    message = _DummyMessage()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=False))

    handler = asyncio.create_task(
        text_commands.gacha_callback(
            _gacha_callback_query(message),
            bot=_paid_pull_bot(),
            settings=SimpleNamespace(gacha_timeout_seconds=10.0),
            economy_repo=object(),
            activity_repo=activity_repo,
            chat_settings=_GACHA_CHAT_SETTINGS,
        )
    )
    await asyncio.wait_for(deliver_started.wait(), timeout=5)

    await text_commands.drain_gacha_background_tasks()

    assert handler.done()
    await handler
    assert delivered == ["result"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_paid_pull_reaching_shutdown_delivers_its_result_without_rendering_the_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A render outlasts the drain, so a pull that reaches shutdown after its purchase skips the animation, ends its
    transaction and sends the result at once."""

    async def purchase_during_shutdown(*_args, **_kwargs):
        text_commands._GACHA_SHUTDOWN.set()
        return _gacha_pull_response_with_card()

    deliver = AsyncMock()
    _patch_paid_pull_flow(monkeypatch, deliver=deliver)
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_during_shutdown)
    resolve = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve)
    bot = _paid_pull_bot()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))
    db_session = SimpleNamespace(commit=AsyncMock())

    await text_commands.gacha_callback(
        _gacha_callback_query(_DummyMessage()),
        bot=bot,
        settings=SimpleNamespace(gacha_timeout_seconds=10.0),
        economy_repo=object(),
        activity_repo=activity_repo,
        chat_settings=_GACHA_CHAT_SETTINGS,
        db_session=db_session,
    )

    resolve.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    db_session.commit.assert_awaited_once()
    deliver.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_paid_pull_arriving_after_shutdown_is_refused_before_it_is_charged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once shutdown has started, a buy is refused before the purchase, so nothing is charged for a result that the
    restart could cut off."""
    text_commands._GACHA_SHUTDOWN.set()
    deliver = AsyncMock()
    _patch_paid_pull_flow(monkeypatch, deliver=deliver)
    purchase = AsyncMock()
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase)
    bot = _paid_pull_bot()

    await text_commands.gacha_callback(
        _gacha_callback_query(_DummyMessage()),
        bot=bot,
        settings=SimpleNamespace(gacha_timeout_seconds=10.0),
        economy_repo=object(),
        activity_repo=SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True)),
        chat_settings=_GACHA_CHAT_SETTINGS,
    )

    purchase.assert_not_awaited()
    deliver.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    answer = text_commands._safe_callback_answer
    answer.assert_awaited_once()
    assert answer.await_args.args[1] == text_commands._GACHA_RESTARTING_TEXT
    assert answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_text_pull_arriving_after_shutdown_is_refused_before_it_is_charged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_commands._GACHA_SHUTDOWN.set()
    pull = AsyncMock(return_value=_gacha_pull_response_with_card())
    monkeypatch.setattr(text_commands, "pull_gacha_card", pull)
    message = _DummyMessage()
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    await text_commands._send_gacha_pull(
        message, SimpleNamespace(gacha_timeout_seconds=10.0), bot, activity_repo, banner="genshin"
    )

    pull.assert_not_awaited()
    assert message.photo_calls == []
    assert message.text_calls[0][0] == text_commands._GACHA_RESTARTING_TEXT
    assert message.from_user.id not in text_commands._GACHA_TEXT_PULL_IN_FLIGHT


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_drain_waits_for_a_tail_that_a_handler_hands_off_while_the_drain_runs() -> None:
    """A handler can hand its tail to a background task after the drain has started. The drain reads the tasks again
    after each wait, so it also waits for that tail."""
    release_handler = asyncio.Event()
    delivered: list[str] = []

    async def tail() -> None:
        await asyncio.sleep(0.05)
        delivered.append("result")

    async def handler() -> None:
        with text_commands._tracked_gacha_pull_handler():
            await release_handler.wait()
            text_commands._spawn_gacha_background(tail())

    handler_task = asyncio.create_task(handler())
    await asyncio.sleep(0)
    drain = asyncio.create_task(text_commands.drain_gacha_background_tasks(timeout=5))
    await asyncio.sleep(0)

    release_handler.set()
    await asyncio.wait_for(drain, timeout=5)

    assert handler_task.done()
    assert delivered == ["result"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_gacha_shutdown_state")
async def test_drain_gives_up_at_its_budget_when_a_handler_never_ends() -> None:
    never_ends = asyncio.Event()

    async def stuck_handler() -> None:
        with text_commands._tracked_gacha_pull_handler():
            await never_ends.wait()

    handler_task = asyncio.create_task(stuck_handler())
    await asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(text_commands.drain_gacha_background_tasks(timeout=0.2), timeout=5)

    assert loop.time() - started < 2
    assert not handler_task.done()
    handler_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler_task
