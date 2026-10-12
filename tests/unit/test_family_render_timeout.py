from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import FamilyBundle
from selara.presentation.handlers import chat_assistant
from selara.presentation.handlers.chat_assistant import family_command
from selara.presentation.renderer_service import PlaywrightRendererService, RenderTimeoutError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


async def _stalled_render(self, *args) -> bytes:
    await asyncio.sleep(10)
    return b""


def _fake_browser() -> SimpleNamespace:
    return SimpleNamespace(close=AsyncMock(), is_connected=lambda: True)


async def test_render_timeout_raises_render_timeout_error_and_recycles_browser(monkeypatch) -> None:
    starts: list[int] = []

    async def fake_ensure_browser_started(self) -> None:
        starts.append(1)
        self._browser = _fake_browser()

    monkeypatch.setattr(PlaywrightRendererService, "_ensure_browser_started", fake_ensure_browser_started)
    monkeypatch.setattr(PlaywrightRendererService, "_do_render", _stalled_render)

    renderer = PlaywrightRendererService(render_timeout_seconds=0.05)

    with pytest.raises(RenderTimeoutError):
        await renderer.render_html("<p>slow</p>", width=100, height=100)

    # One start for the first render, one more when the timed-out browser is recycled.
    assert len(starts) == 2
    assert renderer._render_count == 1


async def test_renderer_recovers_with_real_browser_after_timeout() -> None:
    renderer = PlaywrightRendererService(render_timeout_seconds=0.2)
    await renderer.start()
    try:
        renderer._do_render = _stalled_render.__get__(renderer)
        with pytest.raises(RenderTimeoutError):
            await renderer.render_html("<p>slow</p>", width=200, height=100)
        del renderer._do_render

        image = await renderer.render_html("<p>ok</p>", width=200, height=100, timeout=30)
        assert image.startswith(PNG_SIGNATURE)
    finally:
        await renderer.stop()


def _message(*, chat_type: str = "supergroup") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type=chat_type, title="Test"),
        from_user=SimpleNamespace(
            id=1,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        ),
        reply_to_message=None,
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )


async def test_family_command_replies_when_render_times_out(monkeypatch) -> None:
    monkeypatch.setattr(chat_assistant, "_build_target_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_assistant, "_resolve_display_label", AsyncMock(return_value="@subject"))
    monkeypatch.setattr(chat_assistant, "_build_family_section_labels", AsyncMock(return_value=[]))
    render = AsyncMock(side_effect=RenderTimeoutError("Rendering timed out after 15 seconds."))
    monkeypatch.setattr(chat_assistant, "build_family_tree_image", render)

    repo = SimpleNamespace(
        list_family_bundle=AsyncMock(
            return_value=FamilyBundle(
                subject_user_id=1,
                spouse_user_id=None,
                parents=(2,),
                grandparents=(),
                step_parents=(),
                siblings=(),
                children=(),
                pets=(),
                owners=(),
            )
        ),
        get_chat_display_name=AsyncMock(return_value=None),
    )
    message = _message()
    chat_settings = SimpleNamespace(family_tree_enabled=True)

    await family_command(message, SimpleNamespace(args=None), repo, chat_settings)

    render.assert_awaited_once()
    message.answer.assert_awaited_once_with(chat_assistant.FAMILY_RENDER_TIMEOUT_TEXT)
    message.answer_photo.assert_not_awaited()
