from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatModel
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.llm.artifact_rendering import validate_source, render_static_pages
from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext, create_artifact, delivery_plan, read_skill, send_artifact, get_artifact
from selara.infrastructure.llm.tools import ToolCall
from selara.presentation.llm_formatting import html_to_plain_text


def call(tool_name, **args):
    return ToolCall(name=tool_name, arguments=args, call_id="test")


def png():
    buffer = BytesIO()
    Image.new("RGB", (1600, 200), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
async def context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([ChatModel(telegram_chat_id=i, type="supergroup", title="Chat") for i in (1, 2)])
        await session.commit()
        yield ArtifactRequestContext(repository=ArtifactRepository(session), renderer_url="http://renderer", chat_id=1,
            creator_id=10, message_id=100, thread_id=7)
    await engine.dispose()


async def artifact(context, count=1):
    row = await context.repository.create(chat_id=1, thread_id=7, creator_id=10, title="Test",
        pages=[png()] * count, source={"pages": ["<p>Text</p>"], "css": ""})
    await context.repository.session.commit()
    return row


@pytest.mark.parametrize("source", [
    '<script>alert(1)</script>', '<img src="http://internal">', '<iframe></iframe>',
    '<svg><foreignObject>bad</foreignObject></svg>', '<div onclick="alert(1)">x</div>',
    '<div style="background:url(http://internal)">x</div>',
    '<style>@import "http://internal";</style>', '<style>p{background:u/**/rl(x)}</style>',
    '<div style="background:u\\72l(x)">x</div>', '<svg><use href="#x"/></svg>',
    '<div><p>broken</div>', '<style>p{background:url(&#104;ttp://internal)}</style>',
])
def test_rejects_active_or_malformed_html(source):
    with pytest.raises(ValueError):
        validate_source([source], "")


def test_accepts_static_table_svg_and_cyrillic():
    validate_source(['<h1>Сравнение</h1><table><tr><td>Число</td><td>42</td></tr></table>'
                     '<svg viewBox="0 0 100 100"><rect x="0" y="0" width="80" height="40" fill="red"/></svg>'], "td {padding:12px}")


@pytest.mark.parametrize("caption,inline", [("**" + "я" * 1024 + "**", True), ("я" * 1025, False),
    ("😀" * 512, True), ("😀" * 513, False), ("[ссылка](https://example.org)", True)])
def test_caption_boundary_after_formatting(caption, inline):
    captions, texts = delivery_plan(caption, 1)
    assert bool(captions[0]) == inline
    assert bool(texts) != inline
    for chunk in texts:
        assert len(html_to_plain_text(chunk).encode("utf-16-le")) // 2 <= 3500


async def test_skill_is_required_per_request(context):
    assert not (await create_artifact(call("create_artifact", title="T", pages=["<p>x</p>"]), artifact_context=context)).success
    result = await read_skill(call("read_skill", name="artifacts"), artifact_context=context)
    assert result.success and "artifacts" in context.loaded_skills
    other = ArtifactRequestContext(repository=context.repository, renderer_url="", chat_id=1, creator_id=10, message_id=100)
    assert not (await create_artifact(call("create_artifact", title="T", pages=["<p>x</p>"]), artifact_context=other)).success


async def test_other_chat_topic_and_expired_artifact_cannot_be_read_or_sent(context):
    row = await artifact(context)
    bot = AsyncMock()
    for chat, topic in [(2, 7), (1, 8), (1, None)]:
        foreign = ArtifactRequestContext(repository=context.repository, renderer_url="", chat_id=chat,
            creator_id=10, message_id=100, thread_id=topic)
        assert not (await send_artifact(call("send_artifact", artifact_id=row.id), artifact_context=foreign, bot=bot)).success
        assert not (await get_artifact(call("get_artifact", artifact_id=row.id), artifact_context=foreign)).success
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await context.repository.session.commit()
    assert not (await send_artifact(call("send_artifact", artifact_id=row.id), artifact_context=context, bot=bot)).success
    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_caption_inline_and_confirmed_delivery_is_not_duplicated(context):
    row = await artifact(context)
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=200)
    request = call("send_artifact", artifact_id=row.id, caption="**Готово**")
    assert (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert bot.send_photo.await_count == 1
    bot.send_message.assert_not_awaited()
    assert bot.send_photo.call_args.kwargs["caption"] == "<b>Готово</b>"
    assert bot.send_photo.call_args.kwargs["chat_id"] == 1
    assert bot.send_photo.call_args.kwargs["message_thread_id"] == 7


async def test_long_caption_sent_after_all_photos(context):
    row = await artifact(context, count=2)
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=200)
    bot.send_message.return_value = SimpleNamespace(message_id=201)
    assert (await send_artifact(call("send_artifact", artifact_id=row.id, caption="x" * 7001), artifact_context=context, bot=bot)).success
    assert bot.send_photo.await_count == 2 and bot.send_message.await_count == 3
    assert [c[0] for c in bot.mock_calls] == ["send_photo", "send_photo", "send_message", "send_message", "send_message"]


async def test_partial_delivery_resumes_without_duplicate_photo(context):
    row = await artifact(context, count=2)
    bot = AsyncMock()
    bot.send_photo.side_effect = [SimpleNamespace(message_id=200),
        TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"), message="PHOTO_INVALID_DIMENSIONS"),
        SimpleNamespace(message_id=201)]
    request = call("send_artifact", artifact_id=row.id)
    assert not (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert row.delivery["next"] == 1
    assert (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert bot.send_photo.await_count == 3
    assert row.delivery["message_ids"] == [200, 201]


async def test_ambiguous_network_failure_never_auto_retries(context):
    row = await artifact(context)
    bot = AsyncMock()
    bot.send_photo.side_effect = TimeoutError()
    request = call("send_artifact", artifact_id=row.id)
    assert not (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert not (await send_artifact(request, artifact_context=context, bot=bot)).success
    assert bot.send_photo.await_count == 1


async def test_render_real_table_and_reject_overflow():
    result = await render_static_pages(['<h1>Сравнение</h1><table><tr><th>Имя</th><th>Баланс</th></tr>'
        '<tr><td>Бейкус 😀</td><td>1 234</td></tr><tr><td>Юля</td><td>567</td></tr></table>'], "")
    image = Image.open(BytesIO(base64.b64decode(result["pages"][0])))
    assert image.width == 1600 and 80 <= image.height <= 2400
    with pytest.raises(ValueError, match="размер|ширину|переполнение"):
        await render_static_pages(['<div style="width:1000px">Текст</div>'], "")
    with pytest.raises(ValueError, match="18 px"):
        await render_static_pages(['<p style="font-size:10px">Микротекст</p>'], "")


async def test_created_id_is_persisted_only_after_validated_render(context, monkeypatch):
    from unittest.mock import MagicMock
    import httpx
    import json
    from selara.infrastructure.llm import artifact_tools
    await read_skill(call("read_skill", name="artifacts"), artifact_context=context)
    response = httpx.Response(200, json={"pages": [png()]})
    client = AsyncMock()
    client.post.return_value = response
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = client
    monkeypatch.setattr(artifact_tools.httpx, "AsyncClient", factory)
    result = await create_artifact(call("create_artifact", title="Таблица", pages=["<p>Пример</p>"], css=""), artifact_context=context)
    assert result.success
    data = json.loads(result.result_text)
    row = await context.repository.get(artifact_id=data["artifact_id"], chat_id=1, thread_id=7)
    assert row is not None and data["sent"] is False
    assert row.source["pages"] == ["<p>Пример</p>"]
    assert not context.sent_artifacts


async def test_bad_render_does_not_create_an_id_and_attempts_are_bounded(context, monkeypatch):
    from unittest.mock import MagicMock
    import httpx
    from sqlalchemy import select, func
    from selara.infrastructure.db.models import LlmArtifactModel
    from selara.infrastructure.llm import artifact_tools
    await read_skill(call("read_skill", name="artifacts"), artifact_context=context)
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={"pages": ["invalid-base64"]})
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = client
    monkeypatch.setattr(artifact_tools.httpx, "AsyncClient", factory)
    for _ in range(4):
        assert not (await create_artifact(call("create_artifact", title="T", pages=["<p>x</p>"]), artifact_context=context)).success
    assert client.post.await_count == 3
    assert await context.repository.session.scalar(select(func.count()).select_from(LlmArtifactModel)) == 0


async def test_entity_error_falls_back_to_plain_caption(context):
    row = await artifact(context)
    bot = AsyncMock()
    bot.send_photo.side_effect = [TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"),
        message="can't parse entities"), SimpleNamespace(message_id=200)]
    result = await send_artifact(call("send_artifact", artifact_id=row.id, caption="**Тест**"), artifact_context=context, bot=bot)
    assert result.success and bot.send_photo.await_count == 2
    assert bot.send_photo.call_args.kwargs["caption"] == "Тест"
    assert bot.send_photo.call_args.kwargs["parse_mode"] is None


async def test_caption_change_after_partial_delivery_is_rejected(context):
    row = await artifact(context, count=2)
    bot = AsyncMock()
    bot.send_photo.side_effect = [SimpleNamespace(message_id=200),
        TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"), message="PHOTO_INVALID")]
    assert not (await send_artifact(call("send_artifact", artifact_id=row.id, caption="Первый"), artifact_context=context, bot=bot)).success
    assert not (await send_artifact(call("send_artifact", artifact_id=row.id, caption="Другой"), artifact_context=context, bot=bot)).success
    assert bot.send_photo.await_count == 2
