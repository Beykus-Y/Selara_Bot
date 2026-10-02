"""Progressively loaded skills and independently scoped artifact tools."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from importlib.resources import files
from io import BytesIO
from uuid import uuid4

import httpx
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, ReplyParameters
from PIL import Image

from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.llm.artifact_rendering import validate_source, MAX_IMAGE_BYTES
from selara.infrastructure.llm.tools import ToolCall, ToolResult, _err, _ok, register_tool
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html

SKILLS = {"artifacts": "Таблицы, графики, схемы и статические HTML-карточки как фото в Telegram."}


@dataclass
class ArtifactRequestContext:
    repository: ArtifactRepository
    renderer_url: str
    chat_id: int
    creator_id: int
    message_id: int
    thread_id: int | None = None
    loaded_skills: set[str] = field(default_factory=set)
    create_attempts: int = 0
    sent_artifacts: list[str] = field(default_factory=list)


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
    if name not in SKILLS or artifact_context is None:
        return _err(call.call_id, call.name, "Навык недоступен.")
    content = files("selara.infrastructure.llm").joinpath("skills", name, "SKILL.md").read_text(encoding="utf-8")
    artifact_context.loaded_skills.add(name)
    return _ok(call.call_id, call.name, {"name": name, "version": 1, "content": content}, "Навык прочитан")


@register_tool("create_artifact", _schema(
    "Создать статический артефакт и получить ID после проверок, без отправки. Сначала read_skill(name=artifacts).",
    {"title": {"type": "string", "maxLength": 200},
     "pages": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string"},
               "description": "HTML-фрагменты страниц. Подробные требования в навыке artifacts."},
     "css": {"type": "string", "maxLength": 12000}}, ["title", "pages"]), "Создаю и проверяю артефакт...")
async def create_artifact(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, **_) -> ToolResult:
    ctx = artifact_context
    if ctx is None or "artifacts" not in ctx.loaded_skills:
        return _err(call.call_id, call.name, "Сначала прочитай read_skill(name=artifacts) в этом запросе.")
    if ctx.create_attempts >= 3:
        return _err(call.call_id, call.name, "Лимит: три попытки создания за запрос.")
    ctx.create_attempts += 1
    try:
        title = call.arguments.get("title")
        pages, css = call.arguments.get("pages"), call.arguments.get("css", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Заголовок должен содержать 1–200 символов.")
        validate_source(pages, css)
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
            title=title.strip(), pages=images, source={"pages": pages, "css": css, "skill_version": 1})
        await ctx.repository.session.commit()
    except (ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
        return _err(call.call_id, call.name, str(exc)[:700])
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


def delivery_plan(caption: str, page_count: int) -> tuple[list[str | None], list[str]]:
    if not caption.strip():
        return [None if page_count == 1 else f"{i+1}/{page_count}" for i in range(page_count)], []
    chunks = render_llm_html(caption, max_units=1024)
    if len(chunks) == 1:
        return [chunks[0]] + [f"{i+1}/{page_count}" for i in range(1, page_count)], []
    return [None if page_count == 1 else f"{i+1}/{page_count}" for i in range(page_count)], render_llm_html(caption)


@register_tool("send_artifact", _schema("Отправить проверенный артефакт по ID только в текущий чат и тему. Длинная подпись автоматически идёт отдельным текстом. Повтор не дублирует подтверждённую доставку.",
    {"artifact_id": {"type": "string", "maxLength": 36},
     "caption": {"type": "string", "maxLength": 12000}}, ["artifact_id"]), "Отправляю артефакт...")
async def send_artifact(call: ToolCall, *, artifact_context: ArtifactRequestContext | None = None, bot, **_) -> ToolResult:
    ctx = artifact_context
    if ctx is None:
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    caption = call.arguments.get("caption", "")
    if not isinstance(caption, str) or len(caption) > 12000:
        return _err(call.call_id, call.name, "Подпись должна быть строкой до 12000 символов.")
    row = await ctx.repository.get(artifact_id=str(call.arguments.get("artifact_id", "")),
        chat_id=ctx.chat_id, thread_id=ctx.thread_id, lock=True)
    if row is None:
        return _err(call.call_id, call.name, "Артефакт недоступен.")
    # Store progress before side effects. A crash/ambiguous transport failure must
    # never make a retry send a duplicate to Telegram.
    state = dict(row.delivery)
    if state.get("pending"):
        return _err(call.call_id, call.name, "Доставка uncertain: сообщение могло дойти. Автоматический повтор запрещён.")
    if state.get("complete"):
        if row.id not in ctx.sent_artifacts:
            ctx.sent_artifacts.append(row.id)
        return _ok(call.call_id, call.name, {"artifact_id": row.id, "status": "already_sent",
            "message_ids": state["message_ids"]}, "Артефакт уже отправлен")
    signature = hashlib.sha256(caption.encode()).hexdigest()
    if state and state.get("caption_hash") != signature:
        return _err(call.call_id, call.name, "Частичная доставка: повтори с исходной подписью.")
    captions, texts = delivery_plan(caption, len(row.pages))
    state = state or {"caption_hash": signature, "next": 0, "message_ids": []}
    # Unique owner marker prevents a second request from resuming an active delivery.
    token = str(uuid4())
    items = [("photo", value) for value in captions] + [("text", value) for value in texts]
    for index in range(state["next"], len(items)):
        state["pending"] = token
        row.delivery = dict(state)
        await ctx.repository.session.commit()
        kind, content = items[index]
        kwargs = {"chat_id": ctx.chat_id, "message_thread_id": ctx.thread_id,
                  "reply_parameters": ReplyParameters(message_id=ctx.message_id, allow_sending_without_reply=True)}
        try:
            try:
                if kind == "photo":
                    sent = await bot.send_photo(**kwargs, photo=BufferedInputFile(base64.b64decode(row.pages[index]),
                        filename=f"artifact-{index+1}.png"), caption=content, parse_mode="HTML")
                else:
                    sent = await bot.send_message(**kwargs, text=content, parse_mode="HTML")
            except TelegramBadRequest as exc:
                # Only retry a known entity rejection; other BadRequest means no delivery.
                if "parse" not in exc.message.lower() and "entit" not in exc.message.lower():
                    raise
                plain = html_to_plain_text(content) if content else None
                if kind == "photo":
                    sent = await bot.send_photo(**kwargs, photo=BufferedInputFile(base64.b64decode(row.pages[index]),
                        filename=f"artifact-{index+1}.png"), caption=plain, parse_mode=None)
                else:
                    sent = await bot.send_message(**kwargs, text=plain, parse_mode=None)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter) as exc:
            state.pop("pending", None)
            row.delivery = dict(state)
            await ctx.repository.session.commit()
            return _err(call.call_id, call.name, f"Доставка остановлена: {exc.message[:250]}. Подтверждённых сообщений: {len(state['message_ids'])}.")
        except Exception:
            # Keep pending durable: Telegram might have accepted the upload.
            return _err(call.call_id, call.name, "Доставка uncertain: сообщение могло дойти. Автоматический повтор запрещён.")
        state["message_ids"] = [*state["message_ids"], sent.message_id]
        state["next"] = index + 1
        state.pop("pending", None)
        # Keep a reservation between individual messages as well.
        if index + 1 < len(items):
            state["pending"] = token
        else:
            state["complete"] = True
        row.delivery = dict(state)
        await ctx.repository.session.commit()
    ctx.sent_artifacts.append(row.id)
    return _ok(call.call_id, call.name, {"artifact_id": row.id, "status": "sent",
        "message_ids": state["message_ids"]}, "Артефакт отправлен")
