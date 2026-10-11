"""Progressively loaded skills and independently scoped artifact tools."""
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
import hashlib
from dataclasses import dataclass, field
from io import BytesIO
from uuid import uuid4

import httpx
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, ReplyParameters
from PIL import Image

from selara.application.artifact_content import reject_copied_prose
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.llm.artifact_rendering import validate_source, MAX_IMAGE_BYTES
from selara.infrastructure.llm.skill_catalog import load_catalog
from selara.infrastructure.llm.tools import ToolCall, ToolResult, _err, _ok, register_tool
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html, split_telegram_html

SKILL_VERSION = 5  # the artifacts skill file version (skills/artifacts/SKILL.md)
SKILLS = {"artifacts": "Инфографика, таблицы, графики и схемы как фото в Telegram, дополняющие текст."}


@dataclass
class ArtifactRequestContext:
    repository: ArtifactRepository
    renderer_url: str
    chat_id: int
    creator_id: int
    message_id: int | None
    thread_id: int | None = None
    loaded_skills: set[str] = field(default_factory=set)
    create_attempts: int = 0
    validation_attempts: int = 0
    sent_artifacts: list[str] = field(default_factory=list)
    created_artifacts: list[str] = field(default_factory=list)
    accompanying_text: str = ""
    summary_run_id: int | None = None
    # Set once untrusted web content is in the request: stored on the artifact source (no migration needed).
    web_tainted: bool = False


def _schema(description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"description": description, "parameters": {"type": "object", "properties": properties,
        "required": required or [], "additionalProperties": False}}


@register_tool("list_skills", _schema("Список встроенных навыков; подробные инструкции загружаются через read_skill.", {}))
async def list_skills(call: ToolCall, **_) -> ToolResult:
    return _ok(call.call_id, call.name, {"skills": SKILLS}, "Список навыков")


@register_tool("read_skill", _schema("Прочитать встроенный навык в текущем запросе перед использованием.",
    {"name": {"type": "string", "enum": list(SKILLS)}}, ["name"]), "Читаю навык {name}...")
async def read_skill(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, **_) -> ToolResult:
    name = call.arguments.get("name")
    skill = load_catalog().get(name)
    # Groups only know the skills listed in SKILLS; the wider catalog is offered by the Personal tool set itself.
    if name not in SKILLS or skill is None or artifact_context is None:
        return _err(call.call_id, call.name, "Навык недоступен.")
    artifact_context.loaded_skills.add(name)
    return _ok(call.call_id, call.name, {"name": name, "version": skill.version, "content": skill.body}, "Навык прочитан")


@register_tool("create_artifact", _schema(
    "Создать статический артефакт и получить ID после проверок, без отправки. Сначала read_skill(name=artifacts).",
    {"title": {"type": "string", "maxLength": 200},
     "pages": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string"},
               "description": 'Массив HTML-строк: ["<h1>Топ</h1><p>Данные</p>"]. Не строка и не объекты. Требования в навыке artifacts.'},
     "css": {"type": "string", "maxLength": 12000},
     "accompanying_text": {"type": "string", "maxLength": 12000, "description": "Планируемое пояснение: инфографика должна дополнять его, без копирования абзацев."}}, ["title", "pages"]), "Создаю и проверяю артефакт...")
async def create_artifact(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, **_) -> ToolResult:
    ctx = artifact_context
    if ctx is None or "artifacts" not in ctx.loaded_skills:
        return _err(call.call_id, call.name, "Сначала прочитай read_skill(name=artifacts) в этом запросе.")
    if ctx.create_attempts >= 3 or ctx.validation_attempts >= 6:
        return _err(call.call_id, call.name, "Создание недоступно: лимит 3 отрисовок или 6 проверок исходника. Не обещай успех повторного запроса; верни проверенные данные текстом.")
    ctx.validation_attempts += 1
    try:
        title = call.arguments.get("title")
        pages, css = call.arguments.get("pages"), call.arguments.get("css", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Заголовок должен содержать 1–200 символов.")
        validate_source(pages, css)
        if ctx.summary_run_id is not None and len(pages) != 1:
            raise ValueError("Для итогов дня нужна одна компактная инфографика.")
        accompanying = ctx.accompanying_text or call.arguments.get("accompanying_text", "")
        if not isinstance(accompanying, str) or len(accompanying) > 12000:
            raise ValueError("Пояснение должно быть строкой до 12000 символов.")
        reject_copied_prose(pages, accompanying)
        ctx.create_attempts += 1
        async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
            response = await client.post(ctx.renderer_url.rstrip("/") + "/render", json={"pages": pages, "css": css})
        if response.status_code != 200:
            detail = response.json().get("detail", "Рендер недоступен") if response.status_code == 422 else "Сервис рендера недоступен."
            raise ValueError(str(detail)[:700])
        if len(response.content) > 8_100_000:
            raise ValueError("Слишком большой результат рендера.")
        result = response.json()
        images = result["pages"]
        if not isinstance(images, list) or len(images) != len(pages):
            raise ValueError("Некорректное число изображений.")
        dimensions = []
        for encoded in images:
            png = base64.b64decode(encoded, validate=True)
            if len(png) > MAX_IMAGE_BYTES:
                raise ValueError("Превышен размер изображения.")
            with Image.open(BytesIO(png)) as image:
                width, height = image.size
                if image.format != "PNG" or width != 1600 or not 80 <= height <= 2400:
                    raise ValueError("Недопустимые размеры/формат изображения.")
                image.verify()
            dimensions.append({"width": width, "height": height, "bytes": len(png)})
        row = await ctx.repository.create(chat_id=ctx.chat_id, thread_id=ctx.thread_id, creator_id=ctx.creator_id,
            title=title.strip(), pages=images, source={"pages": pages, "css": css, "skill_version": SKILL_VERSION,
                "summary_run_id": ctx.summary_run_id, "web_tainted": ctx.web_tainted})
        await ctx.repository.session.commit()
    except (ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
        return _err(call.call_id, call.name, str(exc)[:700] +
            f" Исправь причину в текущем запросе. Осталось проверок: {6 - ctx.validation_attempts}; отрисовок: {3 - ctx.create_attempts}. "
            "При сложной композиции используй простую HTML-таблицу и полосы div вместо SVG; не выдумывай данные.")
    ctx.created_artifacts.append(row.id)
    return _ok(call.call_id, call.name, {"artifact_id": row.id, "title": title.strip(), "dimensions": dimensions,
        "checks": "passed", "sent": False, "expires_in_days": 7}, "Артефакт создан")


@register_tool("get_artifact", _schema("Прочитать исходник известного артефакта текущего чата и темы для исправления.",
    {"artifact_id": {"type": "string", "maxLength": 36}}, ["artifact_id"]))
async def get_artifact(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, **_) -> ToolResult:
    if artifact_context is None:
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    row = await artifact_context.repository.get(artifact_id=str(call.arguments.get("artifact_id", "")),
        chat_id=artifact_context.chat_id, thread_id=artifact_context.thread_id)
    if row is None:
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    return _ok(call.call_id, call.name, {"artifact_id": row.id, "title": row.title,
        "source": row.source, "data_trust": "user_controlled"}, "Исходник артефакта")



def delivery_plan(caption: str, page_count: int, *, caption_is_html: bool = False) -> tuple[list[str | None], list[str]]:
    if not caption.strip():
        return [None if page_count == 1 else f"{i+1}/{page_count}" for i in range(page_count)], []
    renderer = split_telegram_html if caption_is_html else render_llm_html
    chunks = renderer(caption, max_units=1024)
    if len(chunks) == 1:
        return [chunks[0]] + [f"{i+1}/{page_count}" for i in range(1, page_count)], []
    return [None if page_count == 1 else f"{i+1}/{page_count}" for i in range(page_count)], renderer(caption)


def message_link(chat_id: int, message_id: int, thread_id: int | None = None) -> str | None:
    # Only channel/supergroup IDs have shareable message links. Never fabricate a
    # private-chat or legacy-group URL, and never accept a model-supplied target.
    if type(chat_id) is not int or chat_id >= -1000000000000 or type(message_id) is not int or message_id <= 0:
        return None
    channel_id = -chat_id - 1000000000000
    thread = f"/{thread_id}" if type(thread_id) is int and thread_id > 0 else ""
    return f"https://t.me/c/{channel_id}{thread}/{message_id}"


async def _link_delivered_messages(*, bot, ctx, row, state, captions, texts) -> str | None:
    """Idempotent edits link only acknowledged messages belonging to this delivery."""
    ids = state["message_ids"]
    photo_count = len(row.pages)
    if state.get("text_only") or not texts or len(ids) != photo_count + len(texts):
        return None
    visual_url = message_link(ctx.chat_id, ids[0], ctx.thread_id)
    text_url = message_link(ctx.chat_id, ids[photo_count], ctx.thread_id)
    if not visual_url or not text_url:
        return None
    try:
        await bot.edit_message_caption(chat_id=ctx.chat_id, message_id=ids[0],
            caption=(captions[0] or "") + f'\n\n<a href="{text_url}">Пояснение к инфографике</a>', parse_mode="HTML")
        await bot.edit_message_text(chat_id=ctx.chat_id, message_id=ids[-1],
            text=texts[-1] + f'\n\n<a href="{visual_url}">Инфографика</a>', parse_mode="HTML", disable_web_page_preview=True)
        return None
    except Exception:
        # Edits do not change the already committed delivery success. No resend.
        return "Картинка и текст доставлены, но взаимные ссылки добавить не удалось."


@register_tool("send_artifact", _schema("Отправить проверенный артефакт по ID в текущий чат и тему. Подпись дополняет инфографику, без повторов. Длинный текст идёт отдельно; бот добавит взаимные ссылки между доставленными сообщениями.",
    {"artifact_id": {"type": "string", "maxLength": 36},
     "caption": {"type": "string", "maxLength": 12000}}, ["artifact_id"]), "Отправляю артефакт...")
async def send_artifact(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, bot, **_) -> ToolResult:
    if artifact_context is None:
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    return await deliver_artifact(call=call, ctx=artifact_context, bot=bot, caption=call.arguments.get("caption", ""))


async def deliver_artifact(*, call: ToolCall, ctx: ArtifactRequestContext, bot, caption: str,
                           caption_is_html: bool = False, fallback_to_text: bool = False,
                           claim_check: Callable[[], Awaitable[bool]] | None = None) -> ToolResult:
    """Internal delivery shared by the agent and daily summary; format flags are
    trusted server parameters and never appear in model tool arguments."""
    limit = 16000 if caption_is_html else 12000
    if not isinstance(caption, str) or len(caption) > limit:
        return _err(call.call_id, call.name, "Слишком длинная подпись.")
    row = await ctx.repository.get(artifact_id=str(call.arguments.get("artifact_id", "")),
        chat_id=ctx.chat_id, thread_id=ctx.thread_id, lock=True)
    if row is None or (ctx.summary_run_id is not None and row.source.get("summary_run_id") != ctx.summary_run_id):
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    state = dict(row.delivery)
    if state.get("pending"):
        return _err(call.call_id, call.name, "Доставка uncertain: сообщение могло дойти. Автоматический повтор запрещён.")
    if state.get("complete"):
        if not state.get("text_only") and row.id not in ctx.sent_artifacts:
            ctx.sent_artifacts.append(row.id)
        return _ok(call.call_id, call.name, {"artifact_id": row.id, "status": "already_sent",
            "message_ids": state["message_ids"], "text_only": bool(state.get("text_only"))}, "Ответ уже отправлен")
    try:
        plain = html_to_plain_text(caption) if caption_is_html else html_to_plain_text("".join(render_llm_html(caption)))
        reject_copied_prose(row.source.get("pages", []), plain)
    except ValueError as exc:
        return _err(call.call_id, call.name, str(exc))
    signature = hashlib.sha256((("html:" if caption_is_html else "") + caption).encode()).hexdigest()
    if state and state.get("caption_hash") != signature:
        return _err(call.call_id, call.name, "Частичная доставка: повтори с исходной подписью.")
    captions, texts = delivery_plan(caption, len(row.pages), caption_is_html=caption_is_html)
    renderer = split_telegram_html if caption_is_html else render_llm_html
    state = state or {"caption_hash": signature, "next": 0, "message_ids": []}
    token = str(uuid4())

    async def claim_is_lost() -> bool:
        if claim_check is None or await claim_check():
            return False
        if state.get("pending") == token:
            state.pop("pending", None)
            row.delivery = dict(state)
            await ctx.repository.session.commit()
        return True

    while True:
        # The final Telegram send is already confirmed and persisted. Do not
        # re-check the delivery deadline after completion: the clock may have
        # crossed it during the final network call, despite successful delivery.
        # The caller still fences mark_daily_summary_run_sent with the claim.
        if state.get("complete"):
            break
        if await claim_is_lost():
            return _err(call.call_id, call.name, "Доставка остановлена: право отправки сводки перешло другому процессу.")
        items = ([("text", value) for value in renderer(caption)] if state.get("text_only")
                 else [("photo", value) for value in captions] + [("text", value) for value in texts])
        index = state["next"]
        if index >= len(items):
            break
        state["pending"] = token
        row.delivery = dict(state)
        await ctx.repository.session.commit()
        if await claim_is_lost():
            return _err(call.call_id, call.name, "Доставка остановлена: право отправки сводки перешло другому процессу.")
        kind, content = items[index]
        kwargs = {"chat_id": ctx.chat_id, "message_thread_id": ctx.thread_id}
        if ctx.message_id is not None:
            kwargs["reply_parameters"] = ReplyParameters(message_id=ctx.message_id, allow_sending_without_reply=True)
        try:
            try:
                if kind == "photo":
                    sent = await bot.send_photo(**kwargs, photo=BufferedInputFile(base64.b64decode(row.pages[index]),
                        filename=f"artifact-{index+1}.png"), caption=content, parse_mode="HTML")
                else:
                    sent = await bot.send_message(**kwargs, text=content, parse_mode="HTML", disable_web_page_preview=True)
            except TelegramBadRequest as exc:
                if "parse" not in exc.message.lower() and "entit" not in exc.message.lower():
                    raise
                if await claim_is_lost():
                    return _err(call.call_id, call.name, "Доставка остановлена: право отправки сводки перешло другому процессу.")
                plain_content = html_to_plain_text(content) if content else None
                if kind == "photo":
                    sent = await bot.send_photo(**kwargs, photo=BufferedInputFile(base64.b64decode(row.pages[index]),
                        filename=f"artifact-{index+1}.png"), caption=plain_content, parse_mode=None)
                else:
                    sent = await bot.send_message(**kwargs, text=plain_content, parse_mode=None, disable_web_page_preview=True)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter) as exc:
            state.pop("pending", None)
            if fallback_to_text and kind == "photo" and index == 0 and len(row.pages) == 1 and not isinstance(exc, TelegramRetryAfter):
                # Telegram confirmed the photo was rejected: no uncertain replay.
                state["text_only"] = True
                state["next"] = 0
                row.delivery = dict(state)
                await ctx.repository.session.commit()
                continue
            row.delivery = dict(state)
            await ctx.repository.session.commit()
            return _err(call.call_id, call.name, f"Доставка остановлена: {exc.message[:250]}. Подтверждённых сообщений: {len(state['message_ids'])}.")
        except Exception:
            return _err(call.call_id, call.name, "Доставка uncertain: сообщение могло дойти. Автоматический повтор запрещён.")
        state["message_ids"] = [*state["message_ids"], sent.message_id]
        state["next"] = index + 1
        state.pop("pending", None)
        if index + 1 < len(items):
            state["pending"] = token
        else:
            state["complete"] = True
        row.delivery = dict(state)
        await ctx.repository.session.commit()
    if not state.get("text_only"):
        ctx.sent_artifacts.append(row.id)
    warning = await _link_delivered_messages(bot=bot, ctx=ctx, row=row, state=state, captions=captions, texts=texts)
    return _ok(call.call_id, call.name, {"artifact_id": row.id, "status": "sent",
        "message_ids": state["message_ids"], "text_only": bool(state.get("text_only")), "link_warning": warning}, "Ответ отправлен")
