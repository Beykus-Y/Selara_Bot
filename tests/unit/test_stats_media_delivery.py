import pytest

from selara.presentation.handlers import stats as stats_module


class _DummyMessage:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None, dict[str, object]]] = []

    async def answer_photo(self, photo, **kwargs) -> None:
        _ = photo
        self.events.append(("photo", kwargs.get("caption"), kwargs))

    async def answer(self, text: str, **kwargs) -> None:
        self.events.append(("text", text, kwargs))


@pytest.mark.asyncio
async def test_send_text_or_photo_sends_photo_first_when_text_does_not_fit_caption() -> None:
    message = _DummyMessage()

    await stats_module._send_text_or_photo(
        message,
        html_text="x" * (stats_module._CAPTION_LIMIT_SAFE + 1),
        chart_bytes=b"chart",
        filename="leaderboard.png",
    )

    assert [event[0] for event in message.events] == ["photo", "text"]
    assert message.events[0][1] is None
    assert message.events[1][1] == "x" * (stats_module._CAPTION_LIMIT_SAFE + 1)


@pytest.mark.asyncio
async def test_send_text_or_photo_keeps_single_captioned_photo_when_text_fits() -> None:
    message = _DummyMessage()

    await stats_module._send_text_or_photo(
        message,
        html_text="short text",
        chart_bytes=b"chart",
        filename="leaderboard.png",
    )

    assert [event[0] for event in message.events] == ["photo"]
    assert message.events[0][1] == "short text"
