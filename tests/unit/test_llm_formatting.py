from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from selara.presentation.handlers.llm_admin import _send_formatted_answer
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html


def test_screenshot_style_and_usernames():
    text = '*Тишина на площадке.*\n\n**elii_2288** — медленно опускает хлопушку.\n\n*Встаёт, хлопает в ладоши.*'
    html = "".join(render_llm_html(text))
    assert '<i>Тишина на площадке.</i>' in html
    assert '<b>elii_2288</b>' in html
    assert '<i>Встаёт, хлопает в ладоши.</i>' in html


def test_code_links_html_and_unsafe_urls():
    html = "".join(render_llm_html('**A & B** <script>evil</script>\n\n`x < 2`\n\n```python\nx & y\n```\n\n[site](https://example.org/?a=1&b=2)\n\n[bad](javascript:alert)'))
    assert '<b>A &amp; B</b>' in html
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert '<code>x &lt; 2</code>' in html and '<pre>x &amp; y</pre>' in html
    assert 'href="https://example.org/?a=1&amp;b=2"' in html
    assert 'href="javascript:' not in html


class _ValidateHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in {'b', 'i', 's', 'code', 'pre', 'a'}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def test_long_nested_answer_has_balanced_chunks_and_preserves_emoji():
    text = '**начало *' + '🦊<& ' * 1800 + 'последний* конец**'
    chunks = render_llm_html(text)
    assert len(chunks) > 1
    for chunk in chunks:
        parser = _ValidateHTML()
        parser.feed(chunk)
        assert parser.stack == []
        assert len(html_to_plain_text(chunk).encode('utf-16-le')) // 2 <= 3500
    assert ''.join(html_to_plain_text(chunk) for chunk in chunks) == 'начало ' + '🦊<& ' * 1800 + 'последний конец'


def test_unsupported_structure_is_flattened_and_unclosed_markdown_is_readable():
    html = ''.join(render_llm_html('# Заголовок\n\n- **один**\n- два\n\n---\n\nнепарная *звездочка'))
    assert 'Заголовок' in html and '<b>один</b>' in html
    assert '#' not in html and '•' not in html and '---' not in html
    assert 'непарная *звездочка' in html


def test_tables_separators_and_strikethrough():
    html = ''.join(render_llm_html('A | B\n--- | ---\none | two\n\n***\n\n~~удалено~~'))
    assert 'one, two' in html
    assert '---' not in html and '|' not in html and '***' not in html
    assert '<s>удалено</s>' in html


@pytest.mark.asyncio
async def test_edit_and_followup_messages_explicitly_use_html():
    thinking = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(reply=AsyncMock())
    await _send_formatted_answer(message, thinking, '**' + 'x' * 4500 + '**')
    assert thinking.edit_text.await_args.kwargs['parse_mode'] == 'HTML'
    assert message.reply.await_args.kwargs['parse_mode'] == 'HTML'


@pytest.mark.asyncio
async def test_telegram_entity_failure_preserves_plain_answer():
    error = TelegramBadRequest(method=SendMessage(chat_id=1, text='x'), message="can't parse entities")
    thinking = SimpleNamespace(edit_text=AsyncMock(side_effect=error))
    message = SimpleNamespace(reply=AsyncMock(side_effect=[error, None]))
    await _send_formatted_answer(message, thinking, '**A & B**')
    assert message.reply.await_args.args[0] == 'A & B'
    assert message.reply.await_args.kwargs['parse_mode'] is None
