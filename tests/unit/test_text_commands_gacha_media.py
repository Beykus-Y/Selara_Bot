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
        "pull_gacha_card",
        AsyncMock(
            return_value=SimpleNamespace(
                message="🍀 Вы получили новую карту: Эмбер",
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
    assert kwargs["caption"].startswith("<b>🎴 Геншин</b>")
    assert 'tg://user?id=1' in kwargs["caption"]
    assert kwargs["parse_mode"] == "HTML"
    assert message.text_calls == []


@pytest.mark.asyncio
async def test_send_gacha_pull_falls_back_to_text_when_image_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    settings = SimpleNamespace(gacha_timeout_seconds=10.0)

    monkeypatch.setattr(
        text_commands,
        "pull_gacha_card",
        AsyncMock(
            return_value=SimpleNamespace(
                message="🍀 Вы получили новую карту: Эмбер",
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
    assert message.text_calls[0][0].startswith("<b>🎴 Геншин</b>")
    assert message.text_calls[0][1]["parse_mode"] == "HTML"
