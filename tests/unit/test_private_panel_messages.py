from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers.private_panel import _answer_html_chunks


@pytest.mark.asyncio
async def test_answer_html_chunks_splits_long_settings_result_and_keeps_keyboard_on_last_chunk() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    reply_markup = SimpleNamespace()
    text = "\n".join(f"<b>Настройка {index}</b>: <code>{'x' * 120}</code>" for index in range(40))

    await _answer_html_chunks(message, text, reply_markup=reply_markup)

    assert message.answer.await_count > 1
    for call in message.answer.await_args_list:
        assert len(call.args[0]) <= 3500
        assert call.kwargs["parse_mode"] == "HTML"
    assert all(call.kwargs["reply_markup"] is None for call in message.answer.await_args_list[:-1])
    assert message.answer.await_args_list[-1].kwargs["reply_markup"] is reply_markup
