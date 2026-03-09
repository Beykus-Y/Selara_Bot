from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from html import escape
from importlib import import_module
from pathlib import Path
from urllib.parse import parse_qs, quote

from aiogram import Bot
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from selara.application.use_cases.economy.results import EconomyDashboard
from selara.application.achievements import get_achievement_catalog_from_settings
from selara.application.use_cases.economy.catalog import localize_crop_code, localize_item_code
from selara.application.use_cases.economy.market_buy_listing import execute as market_buy_listing
from selara.application.use_cases.economy.market_cancel_listing import execute as market_cancel_listing
from selara.application.use_cases.economy.market_create_listing import execute as market_create_listing
from selara.application.use_cases.economy.plant_crop import execute as plant_crop
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.application.use_cases.get_my_stats import execute as get_my_stats
from selara.application.use_cases.get_rep_stats import execute as get_rep_stats
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.core.web_auth import (
    digest_login_code,
    digest_session_token,
    generate_session_token,
    normalize_login_code,
)
from selara.domain.entities import ChatSnapshot, LeaderboardItem, UserChatOverview, UserSnapshot
from selara.domain.economy_entities import FarmState
from selara.infrastructure.db.models import EconomyAccountModel, UserChatActivityDailyModel, UserChatActivityModel, UserModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository
from selara.presentation.auth import has_permission
from selara.presentation.commands.catalog import resolve_builtin_command_key
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.audit import log_chat_action
from selara.presentation.handlers.chat_assistant import invalidate_chat_feature_cache
from selara.presentation import game_state as game_state_module
from selara.presentation.game_state import (
    GAME_DEFINITIONS,
    GAME_LAUNCHABLE_KINDS,
    GAME_STORE,
    GroupGame,
    LiveEvent,
    ZLOBCARDS_BLACK_BY_CATEGORY,
    ZLOBCARDS_CATEGORIES,
    ZLOBCARDS_WHITE_BY_CATEGORY,
    WHOAMI_CARDS_BY_CATEGORY,
    WHOAMI_CATEGORIES,
)
from selara.presentation.handlers.settings_common import apply_setting_update, setting_title_ru, settings_to_dict
from selara.web.admin_docs import build_admin_docs_context
from selara.web.presenters import (
    build_achievement_rows,
    build_chat_context,
    build_home_context,
    build_landing_context,
    format_datetime,
    user_label,
)
from selara.web.rendering import create_template_environment
from selara.web.user_docs import build_user_docs_context

_UTC = timezone.utc
_CHAT_HUB_PAGE_SIZE = 50
_CHAT_HUB_MAX_ROWS = 500
game_router_module = import_module("selara.presentation.handlers.game.router")
logger = logging.getLogger(__name__)


def _chat_hub_mode(raw_value: str | None) -> str:
    value = (raw_value or "mix").strip().lower()
    if value in {"mix", "activity", "karma"}:
        return value
    return "mix"


def _leaderboard_item_search_text(item: LeaderboardItem) -> str:
    return " ".join(
        str(part).lower()
        for part in (
            item.user_id,
            item.username,
            item.first_name,
            item.last_name,
            item.chat_display_name,
        )
        if part is not None
    )


def _leaderboard_row_payload(
    *,
    position: int,
    item: LeaderboardItem,
    viewer_user_id: int,
) -> dict[str, object]:
    return {
        "position": position,
        "user_id": item.user_id,
        "name": user_label(item),
        "username": f"@{item.username}" if item.username else "",
        "activity": item.activity_value,
        "karma": item.karma_value,
        "hybrid_score": round(item.hybrid_score, 3),
        "last_seen_at": format_datetime(item.last_seen_at),
        "is_me": item.user_id == viewer_user_id,
    }


async def _build_chat_daily_activity_series(
    session: AsyncSession,
    *,
    chat_id: int,
    days: int = 7,
) -> list[dict[str, object]]:
    window_days = max(1, days)
    today = datetime.now(_UTC).date()
    start_date = today - timedelta(days=window_days - 1)
    stmt = (
        select(
            UserChatActivityDailyModel.activity_date,
            func.coalesce(func.sum(UserChatActivityDailyModel.message_count), 0),
        )
        .where(
            UserChatActivityDailyModel.chat_id == chat_id,
            UserChatActivityDailyModel.activity_date >= start_date,
        )
        .group_by(UserChatActivityDailyModel.activity_date)
        .order_by(UserChatActivityDailyModel.activity_date.asc())
    )
    rows = (await session.execute(stmt)).all()
    counts_by_day = {
        activity_date: int(message_count or 0)
        for activity_date, message_count in rows
    }

    series: list[dict[str, object]] = []
    for offset in range(window_days):
        day = start_date + timedelta(days=offset)
        series.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "messages": counts_by_day.get(day, 0),
            }
        )
    return series


async def _build_richest_user_payload(
    session: AsyncSession,
    *,
    scope_id: str,
    chat_id: int,
) -> dict[str, object] | None:
    stmt = (
        select(
            EconomyAccountModel.balance,
            UserModel.telegram_user_id,
            UserModel.username,
            UserModel.first_name,
            UserModel.last_name,
            UserChatActivityModel.display_name_override,
        )
        .join(UserModel, UserModel.telegram_user_id == EconomyAccountModel.user_id)
        .outerjoin(
            UserChatActivityModel,
            and_(
                UserChatActivityModel.chat_id == chat_id,
                UserChatActivityModel.user_id == EconomyAccountModel.user_id,
            ),
        )
        .where(EconomyAccountModel.scope_id == scope_id)
        .order_by(EconomyAccountModel.balance.desc(), EconomyAccountModel.user_id.asc())
        .limit(1)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None

    balance, user_id, username, first_name, last_name, display_name = row
    snapshot = UserSnapshot(
        telegram_user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=False,
        chat_display_name=display_name,
    )
    return {
        "label": user_label(snapshot),
        "balance": int(balance or 0),
    }


async def _build_achievement_sections(
    activity_repo: SqlAlchemyActivityRepository,
    *,
    settings: Settings,
    chat_id: int,
    user_id: int,
) -> list[dict[str, object]]:
    catalog = get_achievement_catalog_from_settings(settings)
    chat_awards = {item.achievement_id: item for item in await activity_repo.list_user_chat_achievements(chat_id=chat_id, user_id=user_id)}
    global_awards = {item.achievement_id: item for item in await activity_repo.list_user_global_achievements(user_id=user_id)}
    chat_stats = await activity_repo.get_chat_achievement_stats_map(chat_id=chat_id)
    global_stats = await activity_repo.get_global_achievement_stats_map()

    def _row(definition, award, stats, *, scope_label: str) -> dict[str, object]:
        hidden_locked = bool(definition.hidden and award is None)
        holders_count, holders_percent = stats
        return {
            "title": "Скрытое достижение" if hidden_locked else definition.title,
            "description": "Описание откроется после получения." if hidden_locked else definition.description,
            "icon": "???" if hidden_locked else definition.icon,
            "rarity": definition.rarity,
            "scope_label": scope_label,
            "status": "получено" if award is not None else "не получено",
            "holders_count": holders_count,
            "holders_percent": holders_percent,
            "awarded_at": format_datetime(award.awarded_at) if award is not None else None,
        }

    chat_rows = [
        _row(
            definition,
            chat_awards.get(definition.id),
            chat_stats.get(definition.id, (0, 0.0)),
            scope_label="чат",
        )
        for definition in catalog.list_by_scope("chat")
    ]
    global_rows = [
        _row(
            definition,
            global_awards.get(definition.id),
            global_stats.get(definition.id, (0, 0.0)),
            scope_label="глобал",
        )
        for definition in catalog.list_by_scope("global")
    ]
    return [
        {"title": "Чатовые достижения", "rows": build_achievement_rows(chat_rows)},
        {"title": "Глобальные достижения", "rows": build_achievement_rows(global_rows)},
    ]


def create_web_app(*, settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    base_dir = Path(__file__).resolve().parent
    template_environment = create_template_environment(template_dir=base_dir / "templates")

    app = FastAPI(title="Selara Web Panel")
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    failed_attempts: dict[str, deque[datetime]] = defaultdict(deque)
    chat_settings_defaults = default_chat_settings(settings)
    bot_username = (settings.bot_username or settings.bot_name or "selara_ru_bot").lstrip("@")
    game_bot: Bot | None = None

    async def _get_game_bot() -> Bot:
        nonlocal game_bot
        if game_bot is None:
            game_bot = Bot(token=settings.bot_token)
        return game_bot

    @app.on_event("shutdown")
    async def _close_game_bot() -> None:
        nonlocal game_bot
        if game_bot is not None:
            await game_bot.session.close()
            game_bot = None
        store_close = getattr(GAME_STORE, "close", None)
        if callable(store_close):
            try:
                await store_close()
            except Exception:
                logger.exception("Failed to close shared game store")

    async def _load_user_from_websocket(session: AsyncSession, websocket: WebSocket, *, touch: bool) -> UserSnapshot | None:
        token = websocket.cookies.get(settings.web_session_cookie_name)
        if not token:
            return None
        auth_repo = SqlAlchemyWebAuthRepository(session)
        return await auth_repo.get_user_by_session(
            session_digest=digest_session_token(secret=settings.resolved_web_auth_secret, token=token),
            now=_now_utc(),
            touch=touch,
        )

    async def _ensure_chat_visible_or_none(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user_id: int,
        chat_id: int,
    ) -> UserChatOverview | None:
        admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user_id)
        visible = _merge_visible_groups(admin_groups, activity_groups)
        return visible.get(chat_id)

    async def _stream_live_events(
        *,
        scope: str,
        chat_id: int | None = None,
        game_id: str | None = None,
    ):
        live_broker = getattr(GAME_STORE, "live_broker", None)
        if live_broker is None:
            raise StarletteHTTPException(status_code=503, detail="Live updates are not configured.")

        async def _iterator():
            async for event in live_broker.subscribe(scope=scope, chat_id=chat_id, game_id=game_id):
                if event is None:
                    yield ": ping\n\n"
                    continue
                if not isinstance(event, LiveEvent):
                    continue
                yield f"event: {event.event_type}\ndata: {event.to_json()}\n\n"

        return StreamingResponse(_iterator(), media_type="text/event-stream")

    def _render_template(template_name: str, *, response_status_code: int = 200, **context) -> HTMLResponse:
        content = template_environment.get_template(template_name).render(**context)
        return HTMLResponse(content=content, status_code=response_status_code)

    def _top_links(*links: tuple[str, str, str]) -> list[dict[str, str]]:
        return [
            {"href": href, "label": label, "variant": variant}
            for href, label, variant in links
        ]

    def _login_context(*, flash: str | None, error: str | None) -> dict[str, object]:
        return {
            "page_title": "Selara • Вход",
            "page_name": "login",
            "top_links": _top_links(
                ("/", "Главная", "ghost"),
                ("/app/docs/user", "Справка", "ghost"),
                (f"https://t.me/{bot_username}", "Telegram", "ghost"),
            ),
            "show_logout": False,
            "flash": flash,
            "error": error,
            "home_href": "/",
            "brand_subtitle": "бот для Telegram-групп",
            "bot_username": bot_username,
            "bot_dm_url": f"https://t.me/{bot_username}",
        }

    def _landing_layout_context(*, user: UserSnapshot | None, flash: str | None, error: str | None) -> dict[str, object]:
        if user is None:
            links = _top_links(
                ("#capabilities", "Возможности", "ghost"),
                ("#routes", "Ссылки", "ghost"),
                (f"https://t.me/{bot_username}", "Telegram", "ghost"),
                ("/login", "Войти", "primary"),
            )
            return {
                "top_links": links,
                "show_logout": False,
                "flash": flash,
                "error": error,
                "home_href": "/",
                "brand_subtitle": "бот для Telegram-групп",
            }

        links = _top_links(
            ("/app/docs/user", "Справка пользователя", "ghost"),
            ("/app/games", "Игры", "ghost"),
            (f"https://t.me/{bot_username}", "Telegram", "ghost"),
            ("/app", "К кабинету", "primary"),
        )
        return {
            "top_links": links,
            "show_logout": True,
            "flash": flash,
            "error": error,
            "home_href": "/",
            "brand_subtitle": "бот для Telegram-групп",
        }

    def _home_layout_context(*, flash: str | None, error: str | None) -> dict[str, object]:
        return {
            "top_links": _top_links(
                ("/app/docs/user", "Справка пользователя", "ghost"),
                ("/app/games", "Активные игры", "ghost"),
                ("/app", "Обновить", "subtle"),
            ),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _chat_layout_context(chat_id: int, *, flash: str | None, error: str | None) -> dict[str, object]:
        return {
            "top_links": _top_links(
                ("/app", "К кабинетам", "ghost"),
                ("/app/games", "Активные игры", "ghost"),
                (f"/app/chat/{chat_id}/economy", "Экономика", "ghost"),
                (f"/app/family/{chat_id}", "Моя семья", "ghost"),
                (f"/app/docs/user?chat_id={chat_id}", "Справка пользователя", "ghost"),
                (f"/app/chat/{chat_id}/audit", "Журнал", "ghost"),
                (f"/app/chat/{chat_id}", "Обновить", "subtle"),
            ),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _games_layout_context(*, flash: str | None, error: str | None) -> dict[str, object]:
        return {
            "top_links": _top_links(
                ("#create-game", "Создать игру", "primary"),
                ("/app/docs/user", "Справка пользователя", "ghost"),
                ("/app", "К кабинетам", "ghost"),
                ("/app/games", "Обновить", "subtle"),
            ),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _audit_layout_context(chat_id: int, *, flash: str | None, error: str | None) -> dict[str, object]:
        return {
            "top_links": _top_links(
                (f"/app/chat/{chat_id}", "К группе", "ghost"),
                (f"/app/chat/{chat_id}/audit", "Обновить", "subtle"),
            ),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _docs_layout_context(chat_id: int | None, *, flash: str | None, error: str | None) -> dict[str, object]:
        docs_href = f"/app/docs/admin?chat_id={chat_id}" if chat_id is not None else "/app/docs/admin"
        links: list[tuple[str, str, str]] = []
        if chat_id is not None:
            links.append((f"/app/chat/{chat_id}", "К группе", "ghost"))
        links.extend(
            (
                ("/app/games", "Активные игры", "ghost"),
                ("/app", "К кабинетам", "ghost"),
                (docs_href, "Обновить", "subtle"),
            )
        )
        return {
            "top_links": _top_links(*links),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _user_docs_layout_context(chat_id: int | None, *, flash: str | None, error: str | None) -> dict[str, object]:
        docs_href = f"/app/docs/user?chat_id={chat_id}" if chat_id is not None else "/app/docs/user"
        links: list[tuple[str, str, str]] = []
        if chat_id is not None:
            links.append((f"/app/chat/{chat_id}", "К группе", "ghost"))
        links.extend(
            (
                ("/app/games", "Активные игры", "ghost"),
                ("/app", "К кабинетам", "ghost"),
                (docs_href, "Обновить", "subtle"),
            )
        )
        return {
            "top_links": _top_links(*links),
            "show_logout": True,
            "flash": flash,
            "error": error,
        }

    def _error_context(
        *,
        status_code: int,
        headline: str,
        message: str,
        user: UserSnapshot | None,
        flash: str | None = None,
        error: str | None = None,
        action_links: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        top_links = (
            _top_links(("/", "На главную", "ghost"), ("/login", "Войти", "primary"))
            if user is None
            else _top_links(("/", "На главную", "ghost"), ("/app", "К кабинетам", "primary"))
        )
        actions = action_links
        if actions is None:
            actions = (
                [
                    {"href": "/", "label": "На главную", "variant": "ghost"},
                    {"href": "/login", "label": "Войти через Telegram", "variant": "primary"},
                ]
                if user is None
                else [
                    {"href": "/app", "label": "К кабинетам", "variant": "primary"},
                    {"href": "/app/docs/user", "label": "Справка пользователя", "variant": "ghost"},
                ]
            )
        return {
            "page_title": f"Selara • {status_code}",
            "page_name": "error",
            "top_links": top_links,
            "show_logout": user is not None,
            "flash": flash,
            "error": error,
            "home_href": "/",
            "brand_subtitle": "бот для Telegram-групп",
            "status_code": str(status_code),
            "status_label": (
                "не найдено"
                if status_code == 404
                else "нет доступа"
                if status_code == 403
                else "метод не поддерживается"
                if status_code == 405
                else "ошибка сервера"
                if status_code >= 500
                else "ошибка"
            ),
            "headline": headline,
            "message": message,
            "action_links": actions,
        }

    async def _load_error_user(request: Request) -> UserSnapshot | None:
        try:
            async with session_factory() as session:
                user = await _load_user_from_request(session, request, touch=False)
                await session.commit()
            return user
        except Exception:
            logger.warning("Unable to load user for error page", exc_info=True)
            return None

    async def _render_status_page(
        request: Request,
        *,
        status_code: int,
        headline: str,
        message: str,
        action_links: list[dict[str, str]] | None = None,
    ):
        if _prefers_json(request):
            return JSONResponse(
                status_code=status_code,
                content={"ok": False, "status_code": status_code, "headline": headline, "message": message},
            )
        try:
            user = await _load_error_user(request)
            return _render_template(
                "error.html",
                response_status_code=status_code,
                **_error_context(
                    status_code=status_code,
                    headline=headline,
                    message=message,
                    user=user,
                    action_links=action_links,
                ),
            )
        except Exception:
            logger.exception("Failed to render status page", extra={"status_code": status_code})
            return HTMLResponse(
                status_code=status_code,
                content=(
                    "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
                    f"<title>Selara {status_code}</title></head><body>"
                    f"<h1>{escape(headline)}</h1><p>{escape(message)}</p>"
                    "<p><a href='/'>На главную</a></p></body></html>"
                ),
            )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return await _render_status_page(
                request,
                status_code=404,
                headline="Страница не найдена",
                message="Такого адреса нет или страница уже была перемещена. Проверьте URL или вернитесь на главную.",
            )
        if exc.status_code == 403:
            return await _render_status_page(
                request,
                status_code=403,
                headline="Нет доступа",
                message="У вашего аккаунта нет прав для просмотра этого раздела или действия.",
            )
        if exc.status_code == 401:
            if _prefers_json(request):
                return JSONResponse(
                    status_code=401,
                    content={"ok": False, "status_code": 401, "headline": "Сессия истекла", "message": "Войдите снова."},
                )
            return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))
        if exc.status_code == 405:
            return await _render_status_page(
                request,
                status_code=405,
                headline="Метод не поддерживается",
                message="Этот адрес существует, но не принимает такой тип запроса.",
            )
        return await _render_status_page(
            request,
            status_code=exc.status_code,
            headline="Запрос не выполнен",
            message=exc.detail if isinstance(exc.detail, str) and exc.detail else "Сервер отклонил запрос.",
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        return await _render_status_page(
            request,
            status_code=422,
            headline="Некорректный запрос",
            message="Адрес или параметры запроса не распознаны. Проверьте ссылку и попробуйте снова.",
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled web exception", exc_info=exc)
        return await _render_status_page(
            request,
            status_code=500,
            headline="Внутренняя ошибка",
            message="На сервере произошла ошибка. Страница не упала насовсем, но запрос выполнить не удалось.",
        )

    def _redirect(path: str) -> RedirectResponse:
        return RedirectResponse(url=path, status_code=303)

    def _with_message(path: str, *, key: str, text: str) -> str:
        separator = "&" if "?" in path else "?"
        return f"{path}{separator}{key}={quote(text, safe='')}"

    def _prefers_json(request: Request) -> bool:
        accept = request.headers.get("accept", "")
        requested_with = request.headers.get("x-requested-with", "")
        return "application/json" in accept or requested_with.lower() == "fetch"

    def _json_result(
        *,
        ok: bool,
        message: str,
        status_code: int,
        redirect: str | None = None,
        setting: dict[str, str] | None = None,
    ) -> JSONResponse:
        payload: dict[str, object] = {"ok": ok, "message": message}
        if redirect is not None:
            payload["redirect"] = redirect
        if setting is not None:
            payload["setting"] = setting
        return JSONResponse(content=payload, status_code=status_code)

    def _check_rate_limit(host: str, now: datetime) -> bool:
        attempts = failed_attempts[host]
        window = timedelta(minutes=max(1, settings.web_login_attempt_window_minutes))
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        return len(attempts) >= max(1, settings.web_login_attempt_limit)

    def _register_failed_attempt(host: str, now: datetime) -> None:
        failed_attempts[host].append(now)

    async def _parse_form(request: Request) -> dict[str, str]:
        payload = (await request.body()).decode("utf-8")
        parsed = parse_qs(payload, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items() if values}

    async def _load_user_from_request(session: AsyncSession, request: Request, *, touch: bool) -> UserSnapshot | None:
        token = request.cookies.get(settings.web_session_cookie_name)
        if not token:
            return None
        auth_repo = SqlAlchemyWebAuthRepository(session)
        return await auth_repo.get_user_by_session(
            session_digest=digest_session_token(secret=settings.resolved_web_auth_secret, token=token),
            now=_now_utc(),
            touch=touch,
        )

    async def _load_dashboard_if_exists(
        economy_repo: SqlAlchemyEconomyRepository,
        *,
        mode: str,
        chat_id: int | None,
        user_id: int,
    ) -> EconomyDashboard | None:
        scope, _ = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=user_id)
        if scope is None:
            return None

        account = await economy_repo.get_account(scope=scope, user_id=user_id)
        if account is None:
            return None

        farm = await economy_repo.get_farm_state(account_id=account.id)
        if farm is None:
            farm = FarmState(account_id=account.id, farm_level=1, size_tier="small", negative_event_streak=0)

        plots = await economy_repo.list_plots(account_id=account.id)
        inventory = await economy_repo.list_inventory(account_id=account.id)
        return EconomyDashboard(
            scope=scope,
            account=account,
            farm=farm,
            plots=tuple(sorted(plots, key=lambda item: item.plot_no)),
            inventory=tuple(sorted(inventory, key=lambda item: item.item_code)),
        )

    async def _collect_visible_groups(activity_repo: SqlAlchemyActivityRepository, *, user_id: int) -> tuple[list[UserChatOverview], list[UserChatOverview]]:
        admin_groups = await activity_repo.list_user_admin_chats(user_id=user_id)
        activity_groups = await activity_repo.list_user_activity_chats(user_id=user_id, limit=200)
        return admin_groups, activity_groups

    def _merge_visible_groups(*groups: list[UserChatOverview]) -> dict[int, UserChatOverview]:
        merged: dict[int, UserChatOverview] = {}
        for group_list in groups:
            for group in group_list:
                merged[group.chat_id] = group
        return merged

    def _group_overview_from_game(game: GroupGame) -> UserChatOverview:
        return UserChatOverview(
            chat_id=game.chat_id,
            chat_type="group",
            chat_title=game.chat_title,
            bot_role=None,
            message_count=None,
            last_seen_at=None,
        )

    def _attach_games_to_visible_groups(
        *,
        visible_groups: dict[int, UserChatOverview],
        games: list[GroupGame],
        user_id: int,
        manageable_chat_ids: set[int],
    ) -> None:
        for game in games:
            if game.chat_id in visible_groups:
                continue
            if game.chat_id in manageable_chat_ids or game.owner_user_id == user_id or user_id in game.players:
                visible_groups[game.chat_id] = _group_overview_from_game(game)

    async def _collect_game_groups(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user: UserSnapshot,
        extra_games: tuple[GroupGame, ...] = (),
        recent_limit: int = 6,
    ) -> tuple[dict[int, UserChatOverview], list[UserChatOverview], set[int], list[GroupGame], list[GroupGame]]:
        activity_groups = await activity_repo.list_user_activity_chats(user_id=user.telegram_user_id, limit=200)
        manageable_chats = await activity_repo.list_user_manageable_game_chats(user_id=user.telegram_user_id)
        manageable_chat_ids = {chat.chat_id for chat in manageable_chats}
        active_games = await GAME_STORE.list_active_games()
        recent_games = await GAME_STORE.list_recent_games_for_user(
            user_id=user.telegram_user_id,
            limit=max(1, recent_limit),
        )
        visible_groups = _merge_visible_groups(activity_groups, manageable_chats)
        _attach_games_to_visible_groups(
            visible_groups=visible_groups,
            games=active_games,
            user_id=user.telegram_user_id,
            manageable_chat_ids=manageable_chat_ids,
        )
        _attach_games_to_visible_groups(
            visible_groups=visible_groups,
            games=recent_games,
            user_id=user.telegram_user_id,
            manageable_chat_ids=manageable_chat_ids,
        )
        _attach_games_to_visible_groups(
            visible_groups=visible_groups,
            games=[game for game in extra_games if game is not None],
            user_id=user.telegram_user_id,
            manageable_chat_ids=manageable_chat_ids,
        )
        return visible_groups, manageable_chats, manageable_chat_ids, active_games, recent_games

    async def _can_manage_games(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user: UserSnapshot,
        chat: UserChatOverview,
    ) -> bool:
        return await game_router_module._actor_can_manage_games(
            activity_repo,
            chat_id=chat.chat_id,
            chat_type=chat.chat_type,
            chat_title=chat.chat_title,
            user=user,
            bootstrap_if_missing_owner=False,
        )

    async def _chat_settings_for_game(activity_repo: SqlAlchemyActivityRepository, *, chat_id: int):
        return await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults

    async def _resolve_chat_member_label(activity_repo: SqlAlchemyActivityRepository, *, chat_id: int, user_id: int) -> str:
        label = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
        if label:
            return label
        snapshot = await activity_repo.get_user_snapshot(user_id=user_id)
        if snapshot is None:
            return f"user:{user_id}"
        return snapshot.chat_display_name or snapshot.username or snapshot.first_name or f"user:{user_id}"

    def _market_filter_group(item_code: str) -> str:
        if item_code.startswith("seed:"):
            return "seeds"
        if item_code.startswith("item:"):
            return "consumables"
        return "all"

    def _economy_inventory_target(item_code: str) -> str:
        if item_code.startswith("seed:"):
            return "plot-empty"
        if item_code.startswith("item:"):
            if item_code in {
                "item:fertilizer_fast",
                "item:fertilizer_rich",
                "item:pesticide",
                "item:crop_insurance",
            }:
                return "plot-occupied"
            return "self"
        return "none"

    def _game_id_from_callback_data(callback_data: str) -> str | None:
        parts = callback_data.split(":")
        if not parts:
            return None
        if parts[0] == "game" and len(parts) == 3:
            return parts[2]
        if parts[0] in {
            "gcfg",
            "gquiz",
            "gdice",
            "gbredcat",
            "gbred",
            "gbkr",
            "gbkv",
            "gspy",
            "gwho",
            "gzlobp",
            "gzlobv",
            "gmact",
            "gmvote",
            "gmconfirm",
        } and len(parts) == 3:
            return parts[1]
        return None

    def _button_variant(callback_data: str) -> str:
        if callback_data.startswith("game:start:"):
            return "primary"
        if callback_data.startswith("game:join:"):
            return "subtle"
        if callback_data.startswith("game:cancel:"):
            return "danger"
        if callback_data.startswith("gdice:"):
            return "primary"
        if callback_data.startswith("game:advance:"):
            return "primary"
        if callback_data.startswith("gcfg:"):
            return "ghost"
        if callback_data.startswith(
            ("gquiz:", "gspy:", "gwho:", "gzlobp:", "gzlobv:", "gbred:", "gbredcat:", "gbkr:", "gbkv:", "gmact:", "gmvote:", "gmconfirm:")
        ):
            return "subtle"
        return "ghost"

    def _is_callback_visible(
        callback_data: str,
        *,
        game: GroupGame,
        user_id: int,
        can_manage_games: bool,
        is_member: bool,
    ) -> bool:
        if callback_data.endswith(":noop"):
            return False
        if callback_data.startswith("game:start:"):
            return can_manage_games or game.owner_user_id == user_id
        if callback_data.startswith(("game:cancel:", "game:advance:", "game:reveal:", "gcfg:")):
            return can_manage_games
        if callback_data.startswith(("gmact:", "gmvote:", "gmconfirm:")):
            return is_member and user_id in game.alive_player_ids
        if callback_data.startswith("gzlobp:"):
            return is_member and game.kind == "zlobcards" and game.status == "started" and game.phase == "private_answers"
        if callback_data.startswith("gzlobv:"):
            return is_member and game.kind == "zlobcards" and game.status == "started" and game.phase == "public_vote"
        if callback_data.startswith("gwho:"):
            return is_member and user_id in game.players and user_id != game.whoami_current_actor_user_id
        participant_prefixes = (
            "gquiz:",
            "gdice:",
            "gspy:",
            "gwho:",
            "gzlobv:",
            "gbred:",
            "gbredcat:",
            "gbkr:",
            "gbkv:",
        )
        if callback_data.startswith(participant_prefixes):
            return is_member
        return True

    def _keyboard_to_buttons(
        markup,
        *,
        game: GroupGame,
        user_id: int,
        can_manage_games: bool,
        is_member: bool,
    ) -> list[dict[str, str]]:
        if markup is None:
            return []
        buttons: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in markup.inline_keyboard:
            for button in row:
                text = button.text or "действие"
                if button.url:
                    if not is_member:
                        continue
                    signature = ("url", button.url)
                    if signature in seen:
                        continue
                    seen.add(signature)
                    buttons.append({"kind": "url", "label": text, "url": button.url, "variant": "telegram"})
                    continue
                callback_data = button.callback_data
                if not callback_data:
                    continue
                if not _is_callback_visible(
                    callback_data,
                    game=game,
                    user_id=user_id,
                    can_manage_games=can_manage_games,
                    is_member=is_member,
                ):
                    continue
                signature = ("action", callback_data)
                if signature in seen:
                    continue
                seen.add(signature)
                buttons.append(
                    {
                        "kind": "action",
                        "label": text,
                        "callback_data": callback_data,
                        "variant": _button_variant(callback_data),
                    }
                )
        return buttons

    def _group_game_buttons(game: GroupGame, buttons: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
        grouped = {
            "main_buttons": [],
            "manage_buttons": [],
            "category_buttons": [],
            "vote_buttons": [],
            "telegram_buttons": [],
        }
        for button in buttons:
            if button["kind"] == "url":
                if game.kind == "bredovukha" and game.phase == "private_answers":
                    continue
                grouped["telegram_buttons"].append(button)
                continue

            callback_data = button.get("callback_data", "")
            if callback_data.startswith("gbredcat:"):
                grouped["category_buttons"].append(button)
                continue
            if callback_data.startswith("gbred:"):
                grouped["vote_buttons"].append(button)
                continue
            if callback_data.startswith("gzlobv:"):
                grouped["vote_buttons"].append(button)
                continue
            if callback_data.startswith(("game:start:", "game:cancel:", "game:advance:", "game:reveal:", "gcfg:")):
                grouped["manage_buttons"].append(button)
                continue
            grouped["main_buttons"].append(button)
        return grouped

    def _build_bred_score_rows(game: GroupGame, *, limit: int = 5) -> list[dict[str, str]]:
        if game.kind != "bredovukha" or not game.bred_scores:
            return []
        ranking = sorted(
            game.bred_scores.items(),
            key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
        )
        rows: list[dict[str, str]] = []
        for position, (player_user_id, score) in enumerate(ranking[:limit], start=1):
            rows.append(
                {
                    "position": f"{position:02d}",
                    "label": game.players.get(player_user_id, f"user:{player_user_id}"),
                    "value": str(score),
                }
            )
        return rows

    def _build_live_score_rows(game: GroupGame, *, limit: int = 5) -> list[dict[str, str]]:
        if game.kind == "bredovukha":
            return _build_bred_score_rows(game, limit=limit)
        if game.kind in {"quiz", "dice"}:
            return _build_recent_score_rows(game, limit=limit)
        if game.kind == "zlobcards" and game.zlob_scores:
            ranking = sorted(
                game.zlob_scores.items(),
                key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
            )
            return [
                {
                    "position": f"{position:02d}",
                    "label": game.players.get(player_user_id, f"user:{player_user_id}"),
                    "value": str(score),
                }
                for position, (player_user_id, score) in enumerate(ranking[:limit], start=1)
            ]
        return []

    def _build_bred_submission_rows(game: GroupGame) -> list[dict[str, str]]:
        if game.kind != "bredovukha" or game.status != "started" or game.phase != "private_answers":
            return []
        rows: list[dict[str, str]] = []
        for player_user_id, label in sorted(game.players.items(), key=lambda item: (item[1].lower(), item[0])):
            submitted = player_user_id in game.bred_lies
            rows.append(
                {
                    "label": label,
                    "state": "ready" if submitted else "waiting",
                    "state_label": "сдал" if submitted else "ждём",
                }
            )
        return rows

    def _build_bred_reveal_rows(game: GroupGame) -> list[dict[str, str | bool]]:
        if game.kind != "bredovukha" or not game.bred_last_options:
            return []
        rows: list[dict[str, str | bool]] = []
        for option_index, option_text in enumerate(game.bred_last_options):
            owner_user_id = game.bred_last_option_owner_user_ids[option_index] if option_index < len(game.bred_last_option_owner_user_ids) else None
            votes = game.bred_last_vote_tally[option_index] if option_index < len(game.bred_last_vote_tally) else 0
            is_truth = option_index == game.bred_last_correct_option_index
            author_label = "истина" if is_truth else (game.players.get(owner_user_id, f"user:{owner_user_id}") if owner_user_id is not None else "-")
            rows.append(
                {
                    "slot": game_router_module._quiz_choice_label(option_index),
                    "text": option_text,
                    "author": author_label,
                    "votes": str(votes),
                    "is_truth": is_truth,
                    "tone": "truth" if is_truth else "lie",
                }
            )
        return rows

    def _player_label(game: GroupGame, user_id: int) -> str:
        return game.players.get(user_id, f"user:{user_id}")

    def _spy_category_label(game: GroupGame) -> str:
        return game.spy_category or "случайная тема"

    def _zlob_category_label(game: GroupGame) -> str:
        return game.zlob_category or "случайная тема"

    def _mafia_role_briefing(role: str | None, *, team_code: str | None) -> dict[str, str]:
        if role is None:
            return {
                "title": "Наблюдатель",
                "team": "вне состава",
                "objective": "Вы видите открытую сцену и можете вести игру, но скрытые роли доступны только участникам.",
                "ability": "Личные ночные решения и секретные отчёты доступны только тем, кто вошёл в состав партии.",
                "tone": "observer",
            }

        team_label = GAME_STORE._human_team_name(team_code or game_state_module.MAFIA_TEAM_CIVILIAN)
        objective = {
            game_state_module.MAFIA_TEAM_CIVILIAN: "Найдите мафию и нейтралов раньше, чем стол развалится по ночам.",
            game_state_module.MAFIA_TEAM_MAFIA: "Сведите число живых мафиози к паритету с остальными и не дайте городу договориться.",
            game_state_module.MAFIA_TEAM_VAMPIRE: "Перетяните партию на сторону вампиров и переживите остальные фракции.",
            game_state_module.MAFIA_TEAM_NEUTRAL: "Играйте в свой уникальный финал и ломайте расчёты обеих основных сторон.",
        }.get(team_code or "", "Доведите свою сторону до победного финала.")
        ability_by_role = {
            game_state_module.MAFIA_ROLE_CIVILIAN: "Ночью без отдельного действия. Главная сила этой роли в обсуждении и голосе днём.",
            game_state_module.MAFIA_ROLE_COMMISSIONER: "Ночью проверяете игрока и узнаёте, относится ли он к мафии.",
            game_state_module.MAFIA_ROLE_DOCTOR: "Каждую ночь выбираете, кого спасти от убийства. Можно лечить себя.",
            game_state_module.MAFIA_ROLE_ESCORT: "Блокируете ночное действие выбранного игрока.",
            game_state_module.MAFIA_ROLE_BODYGUARD: "Прикрываете игрока и можете принять удар на себя.",
            game_state_module.MAFIA_ROLE_JOURNALIST: "Сравниваете двух игроков и получаете ответ, в одной ли они команде.",
            game_state_module.MAFIA_ROLE_INSPECTOR: "Проверяете игрока и узнаёте его точную роль.",
            game_state_module.MAFIA_ROLE_CHILD: "Можете раскрыться ночью, выбрав себя, и стать известным мирным.",
            game_state_module.MAFIA_ROLE_PRIEST: "Ставите защиту на игрока и мешаете тёмным эффектам пройти по цели.",
            game_state_module.MAFIA_ROLE_VETERAN: "Один раз объявляете боеготовность, выбрав себя этой ночью.",
            game_state_module.MAFIA_ROLE_REANIMATOR: "Один раз за игру возвращаете выбывшего игрока.",
            game_state_module.MAFIA_ROLE_PSYCHOLOGIST: "Проверяете, совершал ли игрок убийство прошлой ночью.",
            game_state_module.MAFIA_ROLE_DETECTIVE: "Узнаёте, выходил ли игрок ночью из дома.",
            game_state_module.MAFIA_ROLE_MAFIA: "Ночью выбираете общую цель мафии.",
            game_state_module.MAFIA_ROLE_DON: "Помогаете выбрать жертву мафии и параллельно держите под контролем проверку.",
            game_state_module.MAFIA_ROLE_LAWYER: "Ночью прикрываете игрока юридической защитой и влияете на дневной разбор.",
            game_state_module.MAFIA_ROLE_WEREWOLF: "Нападаете ночью как часть тёмной стороны.",
            game_state_module.MAFIA_ROLE_NINJA: "Делаете скрытый ночной удар, оставляя минимум следов.",
            game_state_module.MAFIA_ROLE_POISONER: "Выбираете цель для атаки с ядом и отложенным давлением.",
            game_state_module.MAFIA_ROLE_TERRORIST: "Играете агрессивно и можете устроить цепную развязку после казни.",
            game_state_module.MAFIA_ROLE_MANIAC: "Каждую ночь выбираете личную жертву и играете против всех.",
            game_state_module.MAFIA_ROLE_JESTER: "У вас нет ночного действия: вам выгодно запутать стол и попасть под казнь.",
            game_state_module.MAFIA_ROLE_WITCH: "У вас два зелья: можно спасти себя или устранить чужую цель.",
            game_state_module.MAFIA_ROLE_SERIAL: "Ночью охотитесь в одиночку и не играете ни за одну из основных команд.",
            game_state_module.MAFIA_ROLE_VAMPIRE: "Кусаете цель ночью и постепенно собираете свою сторону.",
            game_state_module.MAFIA_ROLE_BOMBER: "Минируете игрока и готовите поздний взрывной размен.",
            game_state_module.MAFIA_ROLE_VAMPIRE_THRALL: "Вы уже на стороне вампиров и помогаете им дожать стол днём.",
        }
        tone = {
            game_state_module.MAFIA_TEAM_CIVILIAN: "civilian",
            game_state_module.MAFIA_TEAM_MAFIA: "mafia",
            game_state_module.MAFIA_TEAM_VAMPIRE: "vampire",
            game_state_module.MAFIA_TEAM_NEUTRAL: "neutral",
        }.get(team_code or "", "observer")
        return {
            "title": role,
            "team": team_label,
            "objective": objective,
            "ability": ability_by_role.get(role, "Следите за фазой партии и используйте доступное действие вовремя."),
            "tone": tone,
        }

    def _mafia_night_status(game: GroupGame, *, user_id: int, role: str) -> tuple[str, str]:
        if role in game_state_module.MAFIA_ATTACKER_ROLES:
            target_user_id = game.mafia_votes.get(user_id)
            if target_user_id is None:
                return "Мафия ещё не выбрала цель. Сделайте ночной ход ниже.", "waiting"
            return f"Текущая цель: {_player_label(game, target_user_id)}. До конца ночи её можно поменять.", "ready"
        if role == game_state_module.MAFIA_ROLE_COMMISSIONER:
            target_user_id = game.sheriff_checks.get(user_id)
            if target_user_id is None:
                return "Проверка ещё не выбрана. Решите, кого вскрыть этой ночью.", "waiting"
            return f"Проверка направлена на {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_INSPECTOR:
            target_user_id = game.inspector_checks.get(user_id)
            if target_user_id is None:
                return "Инспекция ещё не назначена. Выберите игрока ниже.", "waiting"
            return f"Инспекция направлена на {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_DOCTOR:
            target_user_id = game.doctor_saves.get(user_id)
            if target_user_id is None:
                return "Лечение ещё не назначено. Выберите, кого прикрыть этой ночью.", "waiting"
            return f"Под защитой доктора: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_ESCORT:
            target_user_id = game.escort_blocks.get(user_id)
            if target_user_id is None:
                return "Блок ещё не поставлен. Выберите, кого отвлечь этой ночью.", "waiting"
            return f"Ночное действие блокируется у {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_BODYGUARD:
            target_user_id = game.bodyguard_protects.get(user_id)
            if target_user_id is None:
                return "Телохранитель ещё не выбрал, кого прикрывать.", "waiting"
            return f"Под вашей защитой: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_JOURNALIST:
            pair = game.journalist_checks.get(user_id)
            if pair is not None:
                return f"Пара собрана: {_player_label(game, pair[0])} и {_player_label(game, pair[1])}.", "ready"
            first_pick_user_id = game.journalist_first_pick.get(user_id)
            if first_pick_user_id is not None:
                return f"Первый игрок уже выбран: {_player_label(game, first_pick_user_id)}. Нужен второй.", "waiting"
            return "Сначала выберите первого игрока для сравнения.", "waiting"
        if role == game_state_module.MAFIA_ROLE_PRIEST:
            target_user_id = game.priest_protects.get(user_id)
            if target_user_id is None:
                return "Благословение ещё не поставлено. Выберите цель защиты.", "waiting"
            return f"Под вашей защитой: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_VETERAN:
            if user_id in game.veteran_alerts:
                return "Боевая готовность уже включена на эту ночь.", "ready"
            if user_id in game.veteran_used:
                return "Боеготовность уже была использована в прошлую ночь.", "locked"
            return "Можете один раз включить боеготовность, выбрав себя.", "waiting"
        if role == game_state_module.MAFIA_ROLE_REANIMATOR:
            if user_id in game.reanimator_used and user_id not in game.reanimator_targets:
                return "Реанимация уже потрачена. Этой ночью хода нет.", "locked"
            target_user_id = game.reanimator_targets.get(user_id)
            if target_user_id is None:
                return "Можно вернуть одного выбывшего игрока. Если хотите, выберите цель.", "waiting"
            return f"На возврат выбран: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_PSYCHOLOGIST:
            target_user_id = game.psychologist_checks.get(user_id)
            if target_user_id is None:
                return "Проверка психолога ещё не назначена.", "waiting"
            return f"Психолог проверяет {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_DETECTIVE:
            target_user_id = game.detective_checks.get(user_id)
            if target_user_id is None:
                return "Детектив ещё не выбрал цель проверки.", "waiting"
            return f"Детектив следит за {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_LAWYER:
            target_user_id = game.lawyer_targets.get(user_id)
            if target_user_id is None:
                return "Адвокат ещё не выбрал, кого прикрывать этой ночью.", "waiting"
            return f"Юридическая защита выдана игроку {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_MANIAC:
            target_user_id = game.maniac_kills.get(user_id)
            if target_user_id is None:
                return "Жертва маньяка ещё не выбрана.", "waiting"
            return f"Текущая жертва: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_SERIAL:
            target_user_id = game.serial_kills.get(user_id)
            if target_user_id is None:
                return "Цель серийного убийцы ещё не выбрана.", "waiting"
            return f"Под прицелом: {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_WITCH:
            save_target_user_id = game.witch_save_targets.get(user_id)
            kill_target_user_id = game.witch_kill_targets.get(user_id)
            if save_target_user_id is not None:
                return "Зелье спасения уже направлено на вас. Можно дополнительно выбрать цель для яда, если заряд ещё есть.", "ready"
            if kill_target_user_id is not None:
                return f"Зелье убийства нацелено на {_player_label(game, kill_target_user_id)}.", "ready"
            if user_id in game.witch_save_used and user_id in game.witch_kill_used:
                return "Оба зелья уже израсходованы. Ночь для вас проходит без хода.", "locked"
            return "Можно выбрать себя для спасения или другого игрока для убийства.", "waiting"
        if role == game_state_module.MAFIA_ROLE_VAMPIRE:
            target_user_id = game.vampire_bites.get(user_id)
            if target_user_id is None:
                return "Цель укуса ещё не выбрана.", "waiting"
            return f"Укус направлен на {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_BOMBER:
            target_user_id = game.bomber_mines.get(user_id)
            if target_user_id is None:
                return "Мина ещё не заложена. Выберите цель ниже.", "waiting"
            return f"Заминирован игрок {_player_label(game, target_user_id)}.", "ready"
        if role == game_state_module.MAFIA_ROLE_CHILD:
            if user_id in game.child_revealed:
                return "Вы уже раскрылись как ребёнок и больше не делаете этот ход.", "ready"
            return "Если хотите раскрыться, выберите себя.", "waiting"
        return "Этой ночью у вашей роли нет отдельного действия. Следите за общим столом.", "locked"

    def _build_mafia_roster_rows(game: GroupGame, *, user_id: int) -> list[dict[str, object]]:
        if game.kind != "mafia" or game.status == "lobby":
            return []
        current_vote_user_id = game.day_votes.get(user_id) if user_id in game.alive_player_ids else None
        winner_user_ids = game_router_module._winner_ids_for_mafia(game) if game.status == "finished" else set()
        rows: list[dict[str, object]] = []
        ordered_players = sorted(
            game.players.items(),
            key=lambda item: (item[0] not in game.alive_player_ids, item[1].lower(), item[0]),
        )
        for player_user_id, label in ordered_players:
            is_alive = player_user_id in game.alive_player_ids
            badges: list[dict[str, str]] = []
            if is_alive:
                badges.append({"label": "в игре", "tone": "alive"})
            else:
                badges.append({"label": "выбыл", "tone": "dead"})
            if player_user_id == user_id:
                badges.append({"label": "вы", "tone": "self"})
            if game.phase == "day_vote" and current_vote_user_id == player_user_id:
                badges.append({"label": "ваш голос", "tone": "vote"})
            if game.phase == "day_execution_confirm" and game.mafia_execution_candidate_user_id == player_user_id:
                badges.append({"label": "кандидат", "tone": "candidate"})
            if player_user_id in winner_user_ids:
                badges.append({"label": "победа", "tone": "winner"})
            rows.append(
                {
                    "label": label,
                    "tone": "alive" if is_alive else "dead",
                    "badges": badges,
                }
            )
        return rows

    def _build_mafia_view(
        game: GroupGame,
        *,
        user_id: int,
        is_member: bool,
        private_buttons: list[dict[str, str]],
        grouped_board_buttons: dict[str, list[dict[str, str]]],
    ) -> dict[str, object] | None:
        if game.kind != "mafia" or game.status == "lobby":
            return None

        role = game.roles.get(user_id) if is_member else None
        team_code = GAME_STORE._mafia_team_for_user(game, user_id) if role else None
        role_card = _mafia_role_briefing(role, team_code=team_code)
        is_alive = user_id in game.alive_player_ids
        action_buttons: list[dict[str, str]] = []
        action_title = ""
        action_text = ""
        status_title = "Статус"
        status_text = "Следите за текущей фазой игры."
        status_tone = "observer"

        confirm_buttons = [
            button
            for button in grouped_board_buttons["main_buttons"]
            if button.get("callback_data", "").startswith("gmconfirm:")
        ]
        board_vote_buttons = [
            button
            for button in grouped_board_buttons["main_buttons"]
            if button.get("callback_data", "").startswith("gmvote:")
        ]

        if not is_member:
            status_title = "Режим ведущего"
            status_text = "Вы не в составе этой партии. Сайт показывает открытую сцену, а скрытые роли остаются у игроков."
            status_tone = "observer"
        elif not is_alive and game.phase != "finished":
            status_title = "Вы уже выбыли"
            status_text = "Скрытая роль остаётся с вами, но ходы этой ночью и днём уже недоступны. Можно наблюдать за столом дальше."
            status_tone = "locked"
        elif game.phase == "night":
            action_title = "Ночной ход"
            action_text = "Если вашей роли доступно ночное действие, сделайте его прямо здесь. Выбор можно менять до конца ночи."
            if role is None:
                status_title = "Роль скрыта"
                status_text = "Скрытая информация доступна только участникам партии."
                status_tone = "observer"
            else:
                status_title = "Ваш ночной статус"
                status_text, status_tone = _mafia_night_status(game, user_id=user_id, role=role)
                action_buttons = private_buttons
        elif game.phase == "day_discussion":
            status_title = "Дневное обсуждение"
            if is_member and is_alive:
                status_text = "Сейчас стол обсуждает ночь. Кнопки для голоса появятся здесь, как только откроется этап голосования."
                status_tone = "ready"
            else:
                status_text = "Идёт общее обсуждение. Сайт держит расклад и ждёт следующую фазу."
                status_tone = "observer"
        elif game.phase == "day_vote":
            current_target_user_id = game.day_votes.get(user_id)
            action_title = "Голос дня"
            action_text = "Выберите кандидата на выбывание. Голос можно менять до закрытия этапа."
            action_buttons = private_buttons or board_vote_buttons
            if is_member and is_alive:
                status_title = "Ваш голос"
                if current_target_user_id is None:
                    status_text = "Голос ещё не отдан. Выберите подозреваемого ниже."
                    status_tone = "waiting"
                else:
                    status_text = f"Сейчас вы голосуете против {_player_label(game, current_target_user_id)}. Выбор можно поменять."
                    status_tone = "ready"
            else:
                status_title = "Голосование идёт"
                status_text = "На сцене открыто дневное голосование. У живых игроков кнопки доступны прямо на сайте."
                status_tone = "observer"
        elif game.phase == "day_execution_confirm":
            candidate_label = "-"
            if game.mafia_execution_candidate_user_id is not None:
                candidate_label = _player_label(game, game.mafia_execution_candidate_user_id)
            current_vote = game.execution_confirm_votes.get(user_id)
            action_title = "Подтверждение казни"
            action_text = f"Живые игроки решают судьбу кандидата {candidate_label}. Решение можно менять до конца этапа."
            action_buttons = confirm_buttons
            if is_member and is_alive:
                status_title = "Ваше решение"
                if current_vote is None:
                    status_text = f"Кандидат: {candidate_label}. Голос ещё не подан."
                    status_tone = "waiting"
                elif current_vote is True:
                    status_text = f"Кандидат: {candidate_label}. Вы поддержали казнь."
                    status_tone = "ready"
                else:
                    status_text = f"Кандидат: {candidate_label}. Вы проголосовали против казни."
                    status_tone = "ready"
            else:
                status_title = "Подтверждение казни"
                status_text = f"На столе решается судьба игрока {candidate_label}. Сайт показывает этап без ухода в Telegram."
                status_tone = "observer"
        elif game.phase == "finished":
            status_title = "Партия завершена"
            status_text = game.winner_text or "Финальный исход зафиксирован."
            status_tone = "ready"

        return {
            "role_card": role_card,
            "report_html": game.mafia_private_reports.get(user_id) if is_member else None,
            "roster_rows": _build_mafia_roster_rows(game, user_id=user_id),
            "status_title": status_title,
            "status_text": status_text,
            "status_tone": status_tone,
            "action_title": action_title,
            "action_text": action_text,
            "action_buttons": action_buttons,
        }

    def _spy_role_briefing(game: GroupGame, *, user_id: int, is_member: bool) -> dict[str, str]:
        role = game.roles.get(user_id) if is_member else None
        if role is None:
            return {
                "title": "Наблюдатель",
                "tone": "observer",
                "team": "вне состава",
                "intel_label": "Тема",
                "intel_value": _spy_category_label(game),
                "objective": "Сайт показывает дедуктивную сцену и ход подозрений, но секрет роли раскрыт только участникам.",
                "ability": "Игроки получают свою информацию прямо в веб-карточке, поэтому Telegram больше не нужен как костыль.",
            }
        if role == "Шпион":
            return {
                "title": "Шпион",
                "tone": "spy",
                "team": "один против всех",
                "intel_label": "Тема",
                "intel_value": _spy_category_label(game),
                "objective": "Слиться с разговором, вычислить локацию по чужим репликам и не дать себя вычислить до финального голосования.",
                "ability": "Слушайте, как мирные описывают место, и не называйте слишком точные детали раньше времени.",
            }
        return {
            "title": "Мирный",
            "tone": "civilian",
            "team": "команда мирных",
            "intel_label": "Локация",
            "intel_value": game.spy_location or "-",
            "objective": "По косвенным ответам найти игрока, который не знает общую локацию.",
            "ability": "Не палите место в лоб: задавайте наводящие вопросы и давите на противоречия.",
        }

    def _build_spy_suspect_rows(game: GroupGame, *, user_id: int) -> list[dict[str, object]]:
        if game.kind != "spy" or game.status == "lobby":
            return []
        vote_counts: dict[int, int] = {}
        for _, target_user_id in game.spy_votes.items():
            if target_user_id in game.players:
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
        top_votes = max(vote_counts.values(), default=0)
        majority_needed = len(game.players) // 2 + 1 if game.players else 1
        current_target_user_id = game.spy_votes.get(user_id)
        winner_user_ids = game_router_module._winner_ids_for_spy(game) if game.status == "finished" else set()
        rows: list[dict[str, object]] = []
        ordered_players = sorted(
            game.players.items(),
            key=lambda item: (-vote_counts.get(item[0], 0), item[1].lower(), item[0]),
        )
        for player_user_id, label in ordered_players:
            votes = vote_counts.get(player_user_id, 0)
            badges: list[dict[str, str]] = []
            if player_user_id == user_id:
                badges.append({"label": "вы", "tone": "self"})
            if current_target_user_id == player_user_id and game.status == "started":
                badges.append({"label": "ваша цель", "tone": "vote"})
            if votes and votes == top_votes and game.status == "started":
                badges.append({"label": "лидер", "tone": "leader"})
            if game.status == "finished":
                role = game.roles.get(player_user_id, "-")
                badges.append(
                    {
                        "label": "шпион" if role == "Шпион" else "мирный",
                        "tone": "spy" if role == "Шпион" else "civilian",
                    }
                )
                if player_user_id in winner_user_ids:
                    badges.append({"label": "победа", "tone": "winner"})
            tone = "spy" if game.status == "finished" and game.roles.get(player_user_id) == "Шпион" else ("leader" if votes and votes == top_votes else "regular")
            meter = 0
            if votes > 0:
                meter = min(100, max(14, int(votes * 100 / max(majority_needed, 1))))
            rows.append(
                {
                    "label": label,
                    "votes": str(votes),
                    "meter": str(meter),
                    "tone": tone,
                    "badges": badges,
                }
            )
        return rows

    def _build_spy_view(
        game: GroupGame,
        *,
        user_id: int,
        is_member: bool,
        grouped_board_buttons: dict[str, list[dict[str, str]]],
    ) -> dict[str, object] | None:
        if game.kind != "spy" or game.status == "lobby":
            return None
        role_card = _spy_role_briefing(game, user_id=user_id, is_member=is_member)
        action_buttons = [
            button
            for button in grouped_board_buttons["main_buttons"]
            if button.get("callback_data", "").startswith("gspy:")
        ]
        vote_counts: dict[int, int] = {}
        for _, target_user_id in game.spy_votes.items():
            if target_user_id in game.players:
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
        majority_needed = len(game.players) // 2 + 1 if game.players else 1
        leader_user_id: int | None = None
        leader_votes = 0
        if vote_counts:
            leader_votes = max(vote_counts.values())
            leaders = [candidate for candidate, votes in vote_counts.items() if votes == leader_votes]
            if len(leaders) == 1:
                leader_user_id = leaders[0]
        current_target_user_id = game.spy_votes.get(user_id)
        winner_user_ids = game_router_module._winner_ids_for_spy(game) if game.status == "finished" else set()
        role = game.roles.get(user_id) if is_member else None

        status_title = "Статус раунда"
        status_text = "Сцена Шпиона активна."
        status_tone = "observer"
        action_title = ""
        action_text = ""
        guess_form: dict[str, object] | None = None

        if game.status == "finished":
            status_title = "Развязка раунда"
            if is_member and user_id in winner_user_ids:
                status_text = "Финал закрылся в вашу пользу. Ниже остался полный reveal ролей и подозрений."
                status_tone = "ready"
            elif is_member:
                status_text = "Партия закрыта. Ниже можно посмотреть, как стол пришёл к финалу и кто оказался шпионом."
                status_tone = "locked"
            else:
                status_text = "Игра завершена. Роли раскрыты на сцене, так что веб-карточка стала полноценным aftermath-экраном."
                status_tone = "observer"
        else:
            action_title = "Кого подозреваете?"
            action_text = "Выберите подозреваемого прямо здесь. Если кто-то набирает большинство, игра закроется сразу."
            if not is_member:
                status_title = "Режим ведущего"
                status_text = "Вы видите расклад подозрений и можете вести сцену, но голосовать могут только участники."
                status_tone = "observer"
            elif role == "Шпион":
                status_title = "Ваш шанс на победу"
                status_text = (
                    "Можно голосовать как все или рискнуть и самому назвать локацию. "
                    "Верная догадка сразу приносит победу шпиону."
                )
                status_tone = "ready"
                action_text = "Отдайте голос для маскировки или держите стол в напряжении, пока не будете уверены в локации."
                guess_form = {
                    "game_id": game.game_id,
                    "locations": list(game_state_module.spy_locations_for_category(game.spy_category)),
                    "placeholder": "Начните вводить локацию из выбранной темы",
                    "button_label": "Назвать локацию",
                }
            elif current_target_user_id is None:
                status_title = "Ваше подозрение"
                status_text = "Голос ещё не отдан. Выберите того, кто хуже всех встраивается в разговор о локации."
                status_tone = "waiting"
            else:
                status_title = "Ваше подозрение"
                status_text = f"Сейчас вы давите на {_player_label(game, current_target_user_id)}. До финала голос можно поменять."
                status_tone = "ready"

        leader_label = "пока нет"
        if leader_user_id is not None:
            leader_label = _player_label(game, leader_user_id)
        elif leader_votes > 0:
            leader_label = "ничья по лидерам"

        return {
            "role_card": role_card,
            "suspect_rows": _build_spy_suspect_rows(game, user_id=user_id),
            "status_title": status_title,
            "status_text": status_text,
            "status_tone": status_tone,
            "action_title": action_title,
            "action_text": action_text,
            "action_buttons": action_buttons,
            "guess_form": guess_form,
            "summary": {
                "category": _spy_category_label(game),
                "votes": f"{len(game.spy_votes)}/{len(game.players)}",
                "majority": str(majority_needed),
                "leader": leader_label,
            },
        }

    def _build_whoami_view(
        game: GroupGame,
        *,
        user_id: int,
        is_member: bool,
        grouped_board_buttons: dict[str, list[dict[str, str]]],
    ) -> dict[str, object] | None:
        if game.kind != "whoami" or game.status == "lobby":
            return None

        winner_user_ids = game_router_module._winner_ids_for_whoami(game) if game.status == "finished" else set()
        solved_user_ids = set(game.whoami_solved_user_ids)
        current_actor_user_id = game.whoami_current_actor_user_id
        current_actor_label = _player_label(game, current_actor_user_id) if current_actor_user_id is not None else "-"
        action_buttons = [
            button
            for button in grouped_board_buttons["main_buttons"]
            if button.get("callback_data", "").startswith("gwho:")
        ]

        table_rows: list[dict[str, object]] = []
        for player_user_id, label in sorted(game.players.items(), key=lambda item: (item[1].lower(), item[0])):
            badges: list[dict[str, str]] = []
            if player_user_id == user_id:
                badges.append({"label": "вы", "tone": "self"})
            if current_actor_user_id == player_user_id and game.status == "started":
                badges.append({"label": "ходит", "tone": "turn"})
            if player_user_id in solved_user_ids and game.status == "started":
                badges.append({"label": "разгадал", "tone": "winner"})
            if player_user_id in winner_user_ids:
                badges.append({"label": "победа", "tone": "winner"})

            if game.status == "finished":
                identity = game.roles.get(player_user_id, "-")
                tone = "winner" if player_user_id in winner_user_ids else "known"
            elif not is_member:
                identity = "скрыто до финала"
                tone = "locked"
            elif player_user_id == user_id and player_user_id in solved_user_ids:
                identity = "разгадано"
                tone = "winner"
            elif player_user_id == user_id:
                identity = "???"
                tone = "self"
            else:
                identity = game.roles.get(player_user_id, "-")
                tone = "winner" if player_user_id in solved_user_ids else "known"

            table_rows.append(
                {
                    "label": label,
                    "identity": identity,
                    "tone": tone,
                    "badges": badges,
                    "title": label,
                }
            )

        history_rows: list[dict[str, str]] = []
        for entry in game.whoami_history[-8:]:
            actor_label = _player_label(game, entry.actor_user_id)
            if entry.question_text and entry.answer_label:
                responder_label = _player_label(game, entry.responder_user_id or 0) if entry.responder_user_id is not None else "-"
                history_rows.append(
                    {
                        "title": f"{actor_label} спросил",
                        "text": entry.question_text,
                        "meta": f"{entry.answer_label} · {responder_label}",
                        "tone": entry.answer_code or "unknown",
                    }
                )
                continue
            if entry.guessed_correctly is not None and entry.guess_text:
                history_rows.append(
                    {
                        "title": f"{actor_label} сделал догадку",
                        "text": "Проверка карточки без публичного раскрытия.",
                        "meta": "угадал" if entry.guessed_correctly else "мимо",
                        "tone": "guess-hit" if entry.guessed_correctly else "guess-miss",
                    }
                )

        status_title = "Сцена игры"
        status_text = (
            f"Категория: {game.whoami_category or 'случайная'}. "
            f"Ходит {current_actor_label}. Разгадано {len(solved_user_ids)}/{len(game.players)}."
        )
        status_tone = "observer"
        question_form: dict[str, str] | None = None
        guess_form: dict[str, str] | None = None
        pending_question = game.whoami_pending_question_text
        user_is_solved = user_id in solved_user_ids

        if game.status == "finished":
            status_title = "Партия завершена"
            status_text = game.winner_text or "Финальный reveal уже ниже."
            status_tone = "ready"
        elif not is_member:
            status_title = "Режим наблюдения"
            status_text = "Во время партии чужие карточки скрыты для наблюдателей. Открыты только ход, история вопросов и статус сцены."
            status_tone = "observer"
        elif user_is_solved and game.phase == "whoami_answer" and user_id != current_actor_user_id:
            status_title = "Карточка уже разгадана"
            status_text = "Свой круг вы закончили. Сейчас можно помочь столу и зафиксировать ответ на чужой вопрос."
            status_tone = "ready"
        elif user_is_solved:
            status_title = "Карточка уже разгадана"
            status_text = "Свой круг вы закончили. Дальше можно следить за партией и отвечать столу, когда открыт вопрос."
            status_tone = "locked"
        elif user_id == current_actor_user_id and game.phase == "whoami_ask":
            status_title = "Ваш ход"
            status_text = "Задайте вопрос о себе или сразу попробуйте угадать карточку. Если стол ответит «да», ход останется у вас."
            status_tone = "ready"
            question_form = {
                "game_id": game.game_id,
                "placeholder": "Например: Я человек?",
                "button_label": "Задать вопрос",
            }
            guess_form = {
                "game_id": game.game_id,
                "placeholder": "Я думаю, что я...",
                "button_label": "Проверить догадку",
            }
        elif user_id == current_actor_user_id and game.phase == "whoami_answer":
            status_title = "Ждём ответ стола"
            status_text = f"Ваш вопрос уже на сцене: «{game.whoami_pending_question_text or '-'}»."
            status_tone = "waiting"
        elif game.phase == "whoami_answer":
            status_title = "Ответьте столом"
            status_text = "Первый игрок, который ответит кнопкой ниже, зафиксирует вердикт для текущего вопроса."
            status_tone = "ready"
        else:
            status_title = "Ожидание хода"
            status_text = f"Сейчас задаёт вопрос {current_actor_label}. Пока можно смотреть чужие карточки и историю сцены."
            status_tone = "observer"

        return {
            "category": game.whoami_category or "случайная",
            "current_actor": current_actor_label,
            "pending_question": pending_question,
            "solved_count": str(len(solved_user_ids)),
            "players_total": str(len(game.players)),
            "table_rows": table_rows,
            "history_rows": history_rows,
            "status_title": status_title,
            "status_text": status_text,
            "status_tone": status_tone,
            "action_buttons": action_buttons,
            "question_form": question_form,
            "guess_form": guess_form,
        }

    def _build_zlob_submission_rows(game: GroupGame) -> list[dict[str, str]]:
        if game.kind != "zlobcards" or game.status != "started" or game.phase != "private_answers":
            return []
        rows: list[dict[str, str]] = []
        for player_user_id, label in sorted(game.players.items(), key=lambda item: (item[1].lower(), item[0])):
            submitted = player_user_id in game.zlob_submissions
            rows.append(
                {
                    "label": label,
                    "state": "ready" if submitted else "waiting",
                    "state_label": "сдал" if submitted else "ждём",
                }
            )
        return rows

    def _build_zlob_option_rows(game: GroupGame) -> list[dict[str, str | bool]]:
        if game.kind != "zlobcards":
            return []
        options: tuple[str, ...] = ()
        owners: tuple[int | None, ...] = ()
        winner_option_indexes: tuple[int, ...] = ()
        hide_authors = False
        if game.status == "started" and game.phase == "public_vote" and game.zlob_options:
            options = game.zlob_options
            owners = game.zlob_option_owner_user_ids
            hide_authors = True
            vote_tally = [0 for _ in options]
            for voter_user_id in game.players:
                voted_option_index = game.zlob_votes.get(voter_user_id)
                if voted_option_index is not None and 0 <= voted_option_index < len(vote_tally):
                    vote_tally[voted_option_index] += 1
            top_votes = max(vote_tally) if vote_tally else 0
            if top_votes > 0:
                winner_option_indexes = tuple(index for index, count in enumerate(vote_tally) if count == top_votes)
        elif game.zlob_last_options:
            options = game.zlob_last_options
            owners = game.zlob_last_option_owner_user_ids
            winner_option_indexes = game.zlob_last_winner_option_indexes
            vote_tally = list(game.zlob_last_vote_tally)
        else:
            return []

        rows: list[dict[str, str | bool]] = []
        for option_index, option_text in enumerate(options):
            owner_user_id = owners[option_index] if option_index < len(owners) else None
            votes = vote_tally[option_index] if option_index < len(vote_tally) else 0
            author_label = (
                "анонимно"
                if hide_authors
                else (game.players.get(owner_user_id, f"user:{owner_user_id}") if owner_user_id is not None else "-")
            )
            is_winner = option_index in winner_option_indexes
            rows.append(
                {
                    "slot": game_router_module._quiz_choice_label(option_index),
                    "text": option_text,
                    "author": author_label,
                    "votes": str(votes),
                    "is_winner": is_winner,
                    "tone": "winner" if is_winner else "lie",
                }
            )
        return rows

    def _build_zlob_view(
        game: GroupGame,
        *,
        user_id: int,
        is_member: bool,
        private_buttons: list[dict[str, str]],
        grouped_board_buttons: dict[str, list[dict[str, str]]],
    ) -> dict[str, object] | None:
        if game.kind != "zlobcards" or game.status == "lobby":
            return None

        black_text = game.zlob_black_text or game.zlob_last_black_text
        black_slots = game.zlob_black_slots if game.zlob_black_text else game.zlob_last_black_slots
        hand = list(game.zlob_hands.get(user_id, ())) if is_member else []
        submission = game.zlob_submissions.get(user_id)
        voted_option_index = game.zlob_votes.get(user_id) if is_member else None

        action_buttons = [
            button
            for button in private_buttons
            if button.get("callback_data", "").startswith("gzlobp:")
        ]
        vote_buttons = [
            button
            for button in grouped_board_buttons["vote_buttons"]
            if button.get("callback_data", "").startswith("gzlobv:")
        ]

        status_title = "Статус раунда"
        status_text = "Раунд активен."
        status_tone = "observer"

        if game.status == "finished":
            status_title = "Партия завершена"
            status_text = game.winner_text or "Итог зафиксирован."
            status_tone = "ready"
        elif game.phase == "private_answers":
            submitted_count = len({player_user_id for player_user_id in game.players if player_user_id in game.zlob_submissions})
            if not is_member:
                status_title = "Режим наблюдения"
                status_text = f"Игроки сдают карты в приватной фазе: {submitted_count}/{len(game.players)}."
                status_tone = "observer"
            elif submission:
                status_title = "Ваш выбор сохранён"
                status_text = f"Можно сменить набор до конца фазы. Прогресс: {submitted_count}/{len(game.players)}."
                status_tone = "ready"
            else:
                status_title = "Выберите карточки"
                status_text = f"Сдайте {max(1, int(game.zlob_black_slots))} карточк(у/и). Прогресс: {submitted_count}/{len(game.players)}."
                status_tone = "waiting"
        elif game.phase == "public_vote":
            voted_count = len({player_user_id for player_user_id in game.players if player_user_id in game.zlob_votes})
            if not is_member:
                status_title = "Идёт голосование"
                status_text = f"Прогресс: {voted_count}/{len(game.players)}."
                status_tone = "observer"
            elif voted_option_index is None:
                status_title = "Ваш голос ждут"
                status_text = f"Голосуйте за самый сильный вариант. Нельзя голосовать за свою карточку. Прогресс: {voted_count}/{len(game.players)}."
                status_tone = "waiting"
            else:
                status_title = "Голос принят"
                status_text = (
                    f"Вы выбрали вариант {game_router_module._quiz_choice_label(voted_option_index)}. "
                    f"До закрытия этапа выбор можно менять."
                )
                status_tone = "ready"

        submit_form = None
        if is_member and game.status == "started" and game.phase == "private_answers" and hand:
            submit_form = {
                "game_id": game.game_id,
                "slots": max(1, int(game.zlob_black_slots)),
                "hand": [{"index": str(index), "text": card_text} for index, card_text in enumerate(hand)],
            }

        return {
            "category": _zlob_category_label(game),
            "round_label": f"{max(1, game.round_no)}/{game.zlob_rounds}",
            "target_score": str(game.zlob_target_score),
            "black_text": black_text or "Чёрная карточка будет показана на следующем этапе.",
            "black_slots": max(1, int(black_slots)),
            "status_title": status_title,
            "status_text": status_text,
            "status_tone": status_tone,
            "submit_buttons": action_buttons,
            "vote_buttons": vote_buttons,
            "submit_form": submit_form,
            "submission_rows": _build_zlob_submission_rows(game),
            "option_rows": _build_zlob_option_rows(game),
            "voted_option_label": game_router_module._quiz_choice_label(voted_option_index) if voted_option_index is not None else None,
            "show_vote": game.status == "started" and game.phase == "public_vote",
        }

    def _build_secret_role_reveal_rows(game: GroupGame) -> list[dict[str, str | bool]]:
        if game.kind not in {"spy", "mafia", "whoami"} or game.status != "finished":
            return []

        if game.kind == "spy":
            winner_user_ids = game_router_module._winner_ids_for_spy(game)
        elif game.kind == "mafia":
            winner_user_ids = game_router_module._winner_ids_for_mafia(game)
        else:
            winner_user_ids = game_router_module._winner_ids_for_whoami(game)

        rows: list[dict[str, str | bool]] = []
        for player_user_id, label in sorted(game.players.items(), key=lambda item: (item[1].lower(), item[0])):
            role = game.roles.get(player_user_id, "-")
            if game.kind == "spy":
                tone = "spy" if role == "Шпион" else "civilian"
                team_label = "шпион" if role == "Шпион" else "мирный"
            elif game.kind == "mafia":
                team_code = GAME_STORE._mafia_team_for_user(game, player_user_id)
                tone = {
                    game_state_module.MAFIA_TEAM_MAFIA: "mafia",
                    game_state_module.MAFIA_TEAM_CIVILIAN: "civilian",
                    game_state_module.MAFIA_TEAM_VAMPIRE: "vampire",
                    game_state_module.MAFIA_TEAM_NEUTRAL: "neutral",
                }.get(team_code, "observer")
                team_label = GAME_STORE._human_team_name(team_code)
            else:
                tone = "whoami"
                team_label = game.whoami_category or "карточка"
            rows.append(
                {
                    "player": label,
                    "role": role,
                    "team": team_label,
                    "tone": tone,
                    "winner": player_user_id in winner_user_ids,
                }
            )
        return rows

    def _build_recent_score_rows(game: GroupGame, *, limit: int = 4) -> list[dict[str, str]]:
        scores: dict[int, int] | None = None
        if game.kind == "bredovukha" and game.bred_scores:
            scores = game.bred_scores
        elif game.kind == "zlobcards" and game.zlob_scores:
            scores = game.zlob_scores
        elif game.kind == "quiz" and game.quiz_scores:
            scores = game.quiz_scores
        elif game.kind == "dice" and game.dice_scores:
            scores = game.dice_scores
        if not scores:
            return []

        ranking = sorted(
            scores.items(),
            key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
        )
        return [
            {
                "position": f"{position:02d}",
                "label": game.players.get(player_user_id, f"user:{player_user_id}"),
                "value": str(score),
            }
            for position, (player_user_id, score) in enumerate(ranking[:limit], start=1)
        ]

    def _build_recent_personal_notes(game: GroupGame, *, user_id: int) -> list[str]:
        notes: list[str] = []
        if user_id not in game.players:
            return notes
        if game.kind == "spy":
            role = game.roles.get(user_id, "-")
            notes.append(f"Ваша роль: {role}")
            notes.append(f"Тема: {_spy_category_label(game)}")
            notes.append(f"Локация: {game.spy_location or '-'}")
            return notes
        if game.kind == "mafia":
            role = game.roles.get(user_id, "-")
            team_label = GAME_STORE._human_team_name(GAME_STORE._mafia_team_for_user(game, user_id)) if role != "-" else "-"
            notes.append(f"Ваша роль: {role}")
            notes.append(f"Фракция: {team_label}")
            return notes
        if game.kind == "whoami":
            notes.append(f"Ваша карточка: {game.roles.get(user_id, '-')}")
            notes.append(f"Категория: {game.whoami_category or 'случайная тема'}")
            return notes
        if game.kind == "bredovukha":
            notes.append(f"Ваш счёт: {game.bred_scores.get(user_id, 0)}")
            notes.append(f"Раундов: {game.bred_rounds}")
            return notes
        if game.kind == "zlobcards":
            notes.append(f"Ваш счёт: {game.zlob_scores.get(user_id, 0)}")
            notes.append(f"Раундов: {game.zlob_rounds} · цель: {game.zlob_target_score}")
            notes.append(f"Тема: {_zlob_category_label(game)}")
            return notes
        if game.kind == "quiz":
            notes.append(f"Ваш счёт: {game.quiz_scores.get(user_id, 0)}")
            notes.append(f"Вопросов: {len(game.quiz_questions)}")
            return notes
        if game.kind == "dice":
            roll_value = game.dice_scores.get(user_id)
            notes.append(f"Ваш бросок: {roll_value if roll_value is not None else '-'}")
            notes.append(f"Игроков: {len(game.players)}")
            return notes
        if game.kind == "bunker":
            notes.append("Вы в бункере" if user_id in game.alive_player_ids else "Вы не прошли в бункер")
            notes.append(f"Мест: {game.bunker_seats}")
            return notes
        return notes

    def _build_game_catalog() -> list[dict[str, str]]:
        order = tuple(
            kind
            for kind in ("zlobcards", "bredovukha", "whoami", "quiz", "spy", "mafia", "bunker", "dice")
            if kind in GAME_LAUNCHABLE_KINDS
        )
        notes = {
            "zlobcards": "Чёрная карта, приватные белые ответы и анонимное голосование каждый раунд.",
            "bredovukha": "Блеф, ложь и угадывание правильного факта.",
            "whoami": "Карточки на лбу, вопросы только с «да / нет» и догадка на своём ходу.",
            "quiz": "Быстрые раунды с вариантами ответов и табло.",
            "spy": "Социальная дедукция с одной скрытой ролью.",
            "mafia": "Ночь, день, роли и длинная партия на обсуждение.",
            "bunker": "Жёсткий спор за место в убежище.",
            "dice": "Моментальная партия на один экран.",
        }
        tones = {
            "zlobcards": "magenta",
            "bredovukha": "gold",
            "whoami": "blue",
            "quiz": "cyan",
            "spy": "pink",
            "mafia": "violet",
            "bunker": "green",
            "dice": "orange",
        }
        catalog: list[dict[str, str]] = []
        for key in order:
            definition = GAME_DEFINITIONS[key]  # type: ignore[index]
            catalog.append(
                {
                    "key": definition.key,
                    "title": definition.title,
                    "description": definition.short_description,
                    "min_players_label": f"от {definition.min_players} игроков",
                    "mode_label": "скрытые роли" if definition.secret_roles else "общий экран",
                    "note": notes.get(definition.key, definition.short_description),
                    "tone": tones.get(definition.key, "gold"),
                }
            )
        return catalog

    def _build_whoami_category_options(*, actions_18_enabled: bool | None = None) -> list[dict[str, object]]:
        allowed_categories = None
        if actions_18_enabled is not None:
            allowed_categories = set(game_state_module.allowed_whoami_categories(actions_18_enabled=actions_18_enabled))
        options = [
            {
                "value": "",
                "label": "Случайная тема",
                "note": "Без фиксации заранее",
                "count": "",
                "is_18_plus": False,
            }
        ]
        for category in WHOAMI_CATEGORIES:
            if allowed_categories is not None and category not in allowed_categories:
                continue
            options.append(
                {
                    "value": category,
                    "label": category,
                    "note": "Готовая тема для партии",
                    "count": str(len(WHOAMI_CARDS_BY_CATEGORY.get(category, ()))),
                    "is_18_plus": game_state_module.is_whoami_category_explicit(category),
                }
            )
        return options

    def _build_spy_category_options() -> list[dict[str, object]]:
        options = [
            {
                "value": "",
                "label": "Случайная тема",
                "note": "Без фиксации заранее",
                "count": "",
            }
        ]
        for category in game_state_module.SPY_CATEGORIES:
            options.append(
                {
                    "value": category,
                    "label": category,
                    "note": "Готовая тема для партии",
                    "count": str(len(game_state_module.SPY_LOCATIONS_BY_CATEGORY.get(category, ()))),
                }
            )
        return options

    def _build_zlob_category_options(*, actions_18_enabled: bool | None = None) -> list[dict[str, object]]:
        allowed_categories = None
        if actions_18_enabled is not None:
            allowed_categories = set(game_state_module.allowed_zlob_categories(actions_18_enabled=actions_18_enabled))
        options = [
            {
                "value": "",
                "label": "Случайная тема",
                "note": "Без фиксации заранее",
                "count": "",
                "is_18_plus": False,
            }
        ]
        for category in ZLOBCARDS_CATEGORIES:
            if allowed_categories is not None and category not in allowed_categories:
                continue
            white_count = len(ZLOBCARDS_WHITE_BY_CATEGORY.get(category, ()))
            black_count = len(ZLOBCARDS_BLACK_BY_CATEGORY.get(category, ()))
            options.append(
                {
                    "value": category,
                    "label": category,
                    "note": "Готовая тема для партии",
                    "count": f"{white_count}/{black_count}",
                    "is_18_plus": game_state_module.is_zlob_category_explicit(category),
                }
            )
        return options

    async def _collect_manageable_game_chats(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user_id: int,
    ) -> list[UserChatOverview]:
        return await activity_repo.list_user_manageable_game_chats(user_id=user_id)

    def _build_bred_reveal_card(game: GroupGame) -> dict[str, str] | None:
        if game.kind != "bredovukha":
            return None
        if game.bred_last_round_no is None or not game.bred_last_correct_answer:
            return None
        return {
            "eyebrow": f"Итог раунда {game.bred_last_round_no}",
            "title": f"Правильный ответ: {game.bred_last_correct_answer}",
            "text": game.bred_last_fact_text or game.bred_last_question_prompt or game.bred_last_correct_answer,
            "note": f"Категория: {game.bred_last_category or '-'}",
        }

    def _build_game_spotlight(game: GroupGame) -> dict[str, object]:
        if game.kind == "bredovukha":
            reveal_card = _build_bred_reveal_card(game) if game.phase == "category_pick" or game.status == "finished" else None
            round_value = f"{max(1, game.round_no)}/{game.bred_rounds}"
            if game.status == "finished":
                return {
                    "eyebrow": "Финал",
                    "title": "Партия завершена",
                    "description": game.winner_text or "Финальный счёт уже зафиксирован, а ниже можно пересмотреть последний reveal.",
                    "prompt_title": None,
                    "prompt_text": None,
                    "metrics": [
                        {"label": "Раундов", "value": str(game.bred_rounds)},
                        {"label": "Игроки", "value": str(len(game.players))},
                        {"label": "Статус", "value": "финал"},
                    ],
                    "reveal_card": reveal_card,
                }
            if game.phase == "category_pick":
                selector_label = "-"
                if game.bred_current_selector_user_id is not None:
                    selector_label = game.players.get(game.bred_current_selector_user_id, f"user:{game.bred_current_selector_user_id}")
                return {
                    "eyebrow": "Текущий этап",
                    "title": "Выбор категории",
                    "description": "На сцене остались только актуальные темы, а truth-reveal прошлого раунда висит рядом до следующего выбора.",
                    "prompt_title": None,
                    "prompt_text": None,
                    "metrics": [
                        {"label": "Раунд", "value": round_value},
                        {"label": "Выбирает", "value": selector_label},
                        {"label": "Категорий", "value": str(len(game.bred_category_options))},
                    ],
                    "reveal_card": reveal_card,
                }

            if game.phase == "private_answers":
                submitted_count = len({player_user_id for player_user_id in game.bred_lies if player_user_id in game.players})
                return {
                    "eyebrow": "Факт с пропуском",
                    "title": "Сбор ответов",
                    "description": "Придумайте правдоподобную ложь и сохраните её прямо здесь. Ниже видно, кто уже сдал вариант, без раскрытия самих ответов.",
                    "prompt_title": "Текущий факт",
                    "prompt_text": game.bred_question_prompt,
                    "metrics": [
                        {"label": "Раунд", "value": round_value},
                        {"label": "Категория", "value": game.bred_current_category or "-"},
                        {"label": "Ответы", "value": f"{submitted_count}/{len(game.players)}"},
                    ],
                }

            if game.phase == "public_vote":
                voted_count = len({player_user_id for player_user_id in game.bred_votes if player_user_id in game.players})
                return {
                    "eyebrow": "Раунд продолжается",
                    "title": "Голосование",
                    "description": "Варианты вынесены в отдельный блок ниже, чтобы не дублировать их в тексте и кнопках одновременно.",
                    "prompt_title": "Факт с пропуском",
                    "prompt_text": game.bred_question_prompt,
                    "metrics": [
                        {"label": "Раунд", "value": round_value},
                        {"label": "Категория", "value": game.bred_current_category or "-"},
                        {"label": "Голоса", "value": f"{voted_count}/{len(game.players)}"},
                    ],
                }

        if game.kind == "zlobcards":
            round_value = f"{max(1, game.round_no)}/{game.zlob_rounds}"
            if game.status == "finished":
                return {
                    "eyebrow": "Финал",
                    "title": "Партия завершена",
                    "description": game.winner_text or "Финальные очки зафиксированы.",
                    "prompt_title": "Последняя чёрная карточка" if game.zlob_last_black_text else None,
                    "prompt_text": game.zlob_last_black_text,
                    "metrics": [
                        {"label": "Раундов", "value": str(game.zlob_rounds)},
                        {"label": "Цель", "value": str(game.zlob_target_score)},
                        {"label": "Тема", "value": _zlob_category_label(game)},
                    ],
                }
            if game.phase == "private_answers":
                submitted_count = len({player_user_id for player_user_id in game.players if player_user_id in game.zlob_submissions})
                return {
                    "eyebrow": "Чёрная карточка",
                    "title": "Приватная сдача карт",
                    "description": "Каждый игрок выбирает карточки из руки. После полной сдачи автоматически открывается общее голосование.",
                    "prompt_title": f"Чёрная карточка ({max(1, int(game.zlob_black_slots))})",
                    "prompt_text": game.zlob_black_text or "Карточка подгружается...",
                    "metrics": [
                        {"label": "Раунд", "value": round_value},
                        {"label": "Тема", "value": _zlob_category_label(game)},
                        {"label": "Сдали", "value": f"{submitted_count}/{len(game.players)}"},
                    ],
                }
            if game.phase == "public_vote":
                voted_count = len({player_user_id for player_user_id in game.players if player_user_id in game.zlob_votes})
                return {
                    "eyebrow": "Анонимный reveal",
                    "title": "Голосование",
                    "description": "Все варианты опубликованы без авторов. Игроки голосуют за самый сильный ответ.",
                    "prompt_title": f"Чёрная карточка ({max(1, int(game.zlob_black_slots))})",
                    "prompt_text": game.zlob_black_text or "Карточка подгружается...",
                    "metrics": [
                        {"label": "Раунд", "value": round_value},
                        {"label": "Тема", "value": _zlob_category_label(game)},
                        {"label": "Голоса", "value": f"{voted_count}/{len(game.players)}"},
                    ],
                }

        if game.kind == "quiz" and game.status == "started":
            question = GAME_STORE._current_quiz_question(game)
            question_index = game.quiz_current_question_index or 0
            answered_count = len({player_user_id for player_user_id in game.quiz_answers if player_user_id in game.players})
            total_questions = len(game.quiz_questions)
            question_no = min(total_questions, question_index + 1) if total_questions else 0
            return {
                "eyebrow": "На сцене вопрос",
                "title": "Викторина",
                "description": "Сначала показывается сам вопрос, ниже лежат варианты ответа и текущее табло по очкам.",
                "prompt_title": f"Вопрос {question_no}/{total_questions}" if question is not None and total_questions else None,
                "prompt_text": question.prompt if question is not None else "Ждём следующий вопрос.",
                "metrics": [
                    {"label": "Ответили", "value": f"{answered_count}/{len(game.players)}"},
                    {"label": "Вариантов", "value": str(len(question.options) if question is not None else 0)},
                    {"label": "Раунд", "value": str(question_no or max(1, game.round_no))},
                ],
            }

        if game.kind == "dice" and game.status == "started":
            rolled_count = len(game.dice_scores)
            total_players = len(game.players)
            leader_label = "пока нет"
            leader_score: int | None = None
            if game.dice_scores:
                ranking = sorted(
                    game.dice_scores.items(),
                    key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
                )
                leader_user_id, leader_score = ranking[0]
                tied = [user_id for user_id, score in ranking if score == leader_score]
                leader_label = "ничья" if len(tied) > 1 else _player_label(game, leader_user_id)
            return {
                "eyebrow": "Раунд удачи",
                "title": "Дуэль кубиков",
                "description": "Каждый участник бросает один раз. Как только кидает последний игрок, партия сразу фиксирует победителя.",
                "prompt_title": "Лидер броска" if leader_score is not None else "Старт раунда",
                "prompt_text": (
                    f"{leader_label} · {leader_score}"
                    if leader_score is not None
                    else "Ещё никто не бросил кубик."
                ),
                "metrics": [
                    {"label": "Бросили", "value": f"{rolled_count}/{total_players}"},
                    {"label": "Ждём", "value": str(max(0, total_players - rolled_count))},
                    {"label": "Максимум", "value": str(leader_score) if leader_score is not None else "-"},
                ],
            }

        if game.kind == "spy" and game.status == "started":
            total_players = len(game.players)
            voted_count = len(game.spy_votes)
            majority = total_players // 2 + 1 if total_players else 1
            leader_label = "пока нет"
            leader_votes = 0
            vote_counts: dict[int, int] = {}
            for _, target_user_id in game.spy_votes.items():
                if target_user_id in game.players:
                    vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
            if vote_counts:
                leader_votes = max(vote_counts.values())
                leaders = [candidate for candidate, votes in vote_counts.items() if votes == leader_votes]
                if len(leaders) == 1:
                    leader_label = _player_label(game, leaders[0])
                else:
                    leader_label = "ничья"
            return {
                "eyebrow": "Дедуктивная сцена",
                "title": "Охота на шпиона",
                "description": "Мирные знают локацию, шпион нет. Сайт собрал роль, подозрения и выбор подозреваемого в одном экране.",
                "prompt_title": "Лидер подозрений",
                "prompt_text": f"{leader_label} · {leader_votes} голосов" if leader_votes else "Пока стол только разогревается",
                "metrics": [
                    {"label": "Тема", "value": _spy_category_label(game)},
                    {"label": "Голоса", "value": f"{voted_count}/{total_players}"},
                    {"label": "Большинство", "value": str(majority)},
                ],
            }
        if game.kind == "spy" and game.status == "finished":
            winners = game_router_module._winner_ids_for_spy(game)
            return {
                "eyebrow": "Финальный reveal",
                "title": "Шпион раскрыт",
                "description": game.winner_text or "Партия завершена. Ниже осталась полная карта подозрений и ролей.",
                "prompt_title": None,
                "prompt_text": None,
                "metrics": [
                    {"label": "Тема", "value": _spy_category_label(game)},
                    {"label": "Локация", "value": game.spy_location or "-"},
                    {"label": "Победители", "value": str(len(winners))},
                ],
            }

        if game.kind == "whoami" and game.status == "started":
            current_actor = "-"
            if game.whoami_current_actor_user_id is not None:
                current_actor = _player_label(game, game.whoami_current_actor_user_id)
            prompt_title = None
            prompt_text = None
            if game.phase == "whoami_answer" and game.whoami_pending_question_text:
                prompt_title = "Активный вопрос"
                prompt_text = game.whoami_pending_question_text
            return {
                "eyebrow": "Party round",
                "title": "Кто я?",
                "description": "Свою карточку вы не видите. Угадавший игрок выходит из круга вопросов, но остаётся отвечать столу.",
                "prompt_title": prompt_title,
                "prompt_text": prompt_text,
                "metrics": [
                    {"label": "Категория", "value": game.whoami_category or "случайная"},
                    {"label": "Ходит", "value": current_actor},
                    {"label": "Разгадано", "value": f"{len(game.whoami_solved_user_ids)}/{len(game.players)}"},
                ],
            }
        if game.kind == "whoami" and game.status == "finished":
            winners = game_router_module._winner_ids_for_whoami(game)
            return {
                "eyebrow": "Финал",
                "title": "Все карточки раскрыты",
                "description": game.winner_text or "Партия завершена. Ниже остался reveal всех карточек.",
                "prompt_title": None,
                "prompt_text": None,
                "metrics": [
                    {"label": "Категория", "value": game.whoami_category or "случайная"},
                    {"label": "Игроки", "value": str(len(game.players))},
                    {"label": "Победители", "value": str(len(winners))},
                ],
            }

        if game.kind == "mafia" and game.status == "started":
            alive_count = len(game.alive_player_ids)
            eliminated_count = max(0, len(game.players) - alive_count)
            if game.phase == "night":
                return {
                    "eyebrow": "Скрытая фаза",
                    "title": f"Ночь {max(1, game.round_no)}",
                    "description": "Ночные роли ходят скрытно. Сайт показывает вашу личную панель и общий стол без пересылки в Telegram.",
                    "prompt_title": None,
                    "prompt_text": None,
                    "metrics": [
                        {"label": "Живы", "value": str(alive_count)},
                        {"label": "Выбыли", "value": str(eliminated_count)},
                        {"label": "Раунд", "value": str(max(1, game.round_no))},
                    ],
                }
            if game.phase == "day_discussion":
                return {
                    "eyebrow": "Открытая фаза",
                    "title": "День: обсуждение",
                    "description": "Ночь уже закрыта. Сейчас важно обсуждение и чтение стола перед публичным голосом.",
                    "prompt_title": None,
                    "prompt_text": None,
                    "metrics": [
                        {"label": "Живы", "value": str(alive_count)},
                        {"label": "Раунд", "value": str(max(1, game.round_no))},
                        {"label": "Фаза", "value": "разбор"},
                    ],
                }
            if game.phase == "day_vote":
                voted_count = len({voter for voter in game.day_votes if voter in game.alive_player_ids})
                return {
                    "eyebrow": "Открытая фаза",
                    "title": "День: голосование",
                    "description": "Каждый живой игрок выбирает кандидата на выбывание. Голос можно менять прямо на сайте.",
                    "prompt_title": None,
                    "prompt_text": None,
                    "metrics": [
                        {"label": "Голоса", "value": f"{voted_count}/{alive_count}"},
                        {"label": "Живы", "value": str(alive_count)},
                        {"label": "Раунд", "value": str(max(1, game.round_no))},
                    ],
                }
            if game.phase == "day_execution_confirm":
                voted_count, _, yes_count, no_count = game_router_module._count_alive_execution_confirm_votes(game)
                candidate_label = "-"
                if game.mafia_execution_candidate_user_id is not None:
                    candidate_label = _player_label(game, game.mafia_execution_candidate_user_id)
                return {
                    "eyebrow": "Развязка дня",
                    "title": "Подтверждение казни",
                    "description": "Стол решает, уходит ли кандидат. Это отдельный короткий экран, а не спрятанная inline-клавиатура.",
                    "prompt_title": "Кандидат дня",
                    "prompt_text": candidate_label,
                    "metrics": [
                        {"label": "Да", "value": str(yes_count)},
                        {"label": "Нет", "value": str(no_count)},
                        {"label": "Прогресс", "value": f"{voted_count}/{alive_count}"},
                    ],
                }
        if game.kind == "mafia" and game.status == "finished":
            winner_count = len(game_router_module._winner_ids_for_mafia(game))
            return {
                "eyebrow": "Финал",
                "title": "Партия завершена",
                "description": game.winner_text or "Финальный исход уже определён. Ниже остался полный расклад живых и выбывших.",
                "prompt_title": None,
                "prompt_text": None,
                "metrics": [
                    {"label": "Игроки", "value": str(len(game.players))},
                    {"label": "Победители", "value": str(winner_count)},
                    {"label": "Раунд", "value": str(max(1, game.round_no))},
                ],
            }

        if game.status == "lobby":
            metrics = [{"label": "Игроки", "value": str(len(game.players))}]
            if game.kind == "bredovukha":
                metrics.append({"label": "Раундов", "value": str(game.bred_rounds)})
            if game.kind == "zlobcards":
                metrics.append({"label": "Раундов", "value": str(game.zlob_rounds)})
                metrics.append({"label": "Цель", "value": str(game.zlob_target_score)})
                metrics.append({"label": "Тема", "value": _zlob_category_label(game)})
            if game.kind == "spy":
                metrics.append({"label": "Тема", "value": _spy_category_label(game)})
            if game.kind == "whoami":
                metrics.append({"label": "Тема", "value": game.whoami_category or "случайная"})
            if game.kind == "bunker":
                metrics.append({"label": "Мест", "value": str(game.bunker_seats)})
            return {
                "eyebrow": "Подготовка",
                "title": "Лобби открыто",
                "description": "Оставлены только полезные действия: вход, параметры и запуск. Остальной шум убран.",
                "prompt_title": None,
                "prompt_text": None,
                "metrics": metrics,
            }

        metrics = [
            {"label": "Этап", "value": game_router_module._phase_title(game)},
            {"label": "Игроки", "value": str(len(game.players))},
        ]
        if game.round_no:
            metrics.append({"label": "Раунд", "value": str(max(1, game.round_no))})
        return {
            "eyebrow": "Состояние игры",
            "title": game_router_module._phase_title(game),
            "description": GAME_DEFINITIONS[game.kind].short_description,
            "prompt_title": None,
            "prompt_text": None,
            "metrics": metrics,
        }

    def _build_secret_lines(game: GroupGame, *, user_id: int) -> list[str]:
        lines: list[str] = []
        if game.kind in {"spy", "mafia"}:
            role = game.roles.get(user_id)
            if not role:
                return []
            lines.append(f"Роль: {role}")
            if game.kind == "spy":
                lines.append(f"Тема: {_spy_category_label(game)}")
                if role == "Шпион":
                    lines.append("Локация: неизвестна")
                else:
                    lines.append(f"Локация: {game.spy_location or '-'}")
            return lines

        if game.kind == "bunker":
            card = game.bunker_cards.get(user_id)
            if card is None:
                return []
            labels = {
                "profession": "Профессия",
                "age": "Возраст",
                "gender": "Пол",
                "health_condition": "Здоровье",
                "skill": "Навык",
                "hobby": "Хобби",
                "phobia": "Фобия",
                "trait": "Особенность",
                "item": "Предмет",
            }
            for field in game_router_module.BUNKER_CARD_FIELDS:
                lines.append(f"{labels.get(field, field)}: {getattr(card, field)}")
            return lines
        return []

    async def _build_active_game_cards(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user: UserSnapshot,
        visible_groups: dict[int, UserChatOverview],
        active_games: list[GroupGame],
        manageable_chat_ids: set[int],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for game in active_games:
            is_member = user.telegram_user_id in game.players
            is_owner = game.owner_user_id == user.telegram_user_id
            can_manage_games = game.chat_id in manageable_chat_ids
            can_view_game = game.chat_id in visible_groups or is_owner or can_manage_games
            if not can_view_game:
                continue
            chat = visible_groups.get(game.chat_id) or _group_overview_from_game(game)
            current_chat_settings = await _chat_settings_for_game(activity_repo, chat_id=chat.chat_id)
            board_buttons = _keyboard_to_buttons(
                game_router_module._build_game_controls(game=game, bot_username=bot_username),
                game=game,
                user_id=user.telegram_user_id,
                can_manage_games=can_manage_games,
                is_member=is_member,
            )
            private_buttons = _keyboard_to_buttons(
                game_router_module._build_private_phase_keyboard(game, actor_user_id=user.telegram_user_id),
                game=game,
                user_id=user.telegram_user_id,
                can_manage_games=can_manage_games,
                is_member=is_member,
            )
            grouped_board_buttons = _group_game_buttons(game, board_buttons)
            manage_buttons = grouped_board_buttons["manage_buttons"]
            if game.kind == "spy":
                manage_buttons = [
                    button
                    for button in manage_buttons
                    if button.get("callback_data") != f"gcfg:{game.game_id}:spy_cat_next"
                ]
            if game.kind == "whoami":
                manage_buttons = [
                    button
                    for button in manage_buttons
                    if button.get("callback_data") != f"gcfg:{game.game_id}:whoami_cat_next"
                ]
            if game.kind == "zlobcards":
                manage_buttons = [
                    button
                    for button in manage_buttons
                    if button.get("callback_data") != f"gcfg:{game.game_id}:zlob_cat_next"
                ]
            spy_view = _build_spy_view(
                game,
                user_id=user.telegram_user_id,
                is_member=is_member,
                grouped_board_buttons=grouped_board_buttons,
            )
            whoami_view = _build_whoami_view(
                game,
                user_id=user.telegram_user_id,
                is_member=is_member,
                grouped_board_buttons=grouped_board_buttons,
            )
            mafia_view = _build_mafia_view(
                game,
                user_id=user.telegram_user_id,
                is_member=is_member,
                private_buttons=private_buttons,
                grouped_board_buttons=grouped_board_buttons,
            )
            zlob_view = _build_zlob_view(
                game,
                user_id=user.telegram_user_id,
                is_member=is_member,
                private_buttons=private_buttons,
                grouped_board_buttons=grouped_board_buttons,
            )
            secret_lines = _build_secret_lines(game, user_id=user.telegram_user_id) if is_member and game.status == "started" and game.kind not in {"mafia", "spy"} else []
            players = sorted(game.players.values(), key=lambda value: value.lower())
            mafia_started = game.kind == "mafia" and game.status != "lobby"
            spy_started = game.kind == "spy" and game.status != "lobby"
            whoami_started = game.kind == "whoami" and game.status != "lobby"
            zlob_started = game.kind == "zlobcards" and game.status != "lobby"
            spy_theme_picker = None
            if game.kind == "spy" and game.status == "lobby" and can_manage_games and game_state_module.SPY_CATEGORIES:
                spy_theme_picker = {
                    "game_id": game.game_id,
                    "current_value": game.spy_category or "",
                    "current_label": _spy_category_label(game),
                    "options": _build_spy_category_options(),
                }
            whoami_theme_picker = None
            if game.kind == "whoami" and game.status == "lobby" and can_manage_games and WHOAMI_CATEGORIES:
                whoami_theme_picker = {
                    "game_id": game.game_id,
                    "current_value": game.whoami_category or "",
                    "current_label": game.whoami_category or "Случайная тема",
                    "options": _build_whoami_category_options(
                        actions_18_enabled=current_chat_settings.actions_18_enabled
                    ),
                }
            zlob_theme_picker = None
            if game.kind == "zlobcards" and game.status == "lobby" and can_manage_games and ZLOBCARDS_CATEGORIES:
                zlob_theme_picker = {
                    "game_id": game.game_id,
                    "current_value": game.zlob_category or "",
                    "current_label": _zlob_category_label(game),
                    "options": _build_zlob_category_options(
                        actions_18_enabled=current_chat_settings.actions_18_enabled
                    ),
                }
            cards.append(
                {
                    "game_id": game.game_id,
                    "chat_id": str(game.chat_id),
                    "chat_title": chat.chat_title or f"chat:{chat.chat_id}",
                    "title": GAME_DEFINITIONS[game.kind].title,
                    "kind": game.kind,
                    "status": game_router_module._phase_title(game),
                    "status_badge": "lobby" if game.status == "lobby" else ("active" if game.status == "started" else "done"),
                    "description": GAME_DEFINITIONS[game.kind].short_description,
                    "players_count": len(game.players),
                    "round_no": str(max(1, game.round_no)),
                    "created_at": format_datetime(game.created_at),
                    "started_at": format_datetime(game.started_at),
                    "is_member": is_member,
                    "is_owner": is_owner,
                    "can_manage_games": can_manage_games,
                    "players_preview": players[:8],
                    "players_hidden": max(0, len(players) - 8),
                    "winner_text": game.winner_text,
                    "spotlight": _build_game_spotlight(game),
                    "main_buttons": [] if mafia_started or spy_started or whoami_started or zlob_started else grouped_board_buttons["main_buttons"],
                    "manage_buttons": manage_buttons,
                    "category_buttons": grouped_board_buttons["category_buttons"],
                    "vote_buttons": grouped_board_buttons["vote_buttons"],
                    "telegram_buttons": [] if mafia_started or spy_started or whoami_started or zlob_started else grouped_board_buttons["telegram_buttons"],
                    "private_buttons": [] if mafia_started or zlob_started else private_buttons,
                    "spy_theme_picker": spy_theme_picker,
                    "whoami_theme_picker": whoami_theme_picker,
                    "zlob_theme_picker": zlob_theme_picker,
                    "show_number_guess": game.kind == "number" and game.kind in GAME_LAUNCHABLE_KINDS and game.status == "started" and is_member,
                    "show_bred_answer": game.kind == "bredovukha" and game.status == "started" and game.phase == "private_answers" and is_member,
                    "bred_submission_rows": _build_bred_submission_rows(game),
                    "bred_reveal_rows": _build_bred_reveal_rows(game),
                    "spy_view": spy_view,
                    "whoami_view": whoami_view,
                    "mafia_view": mafia_view,
                    "zlob_view": zlob_view,
                    "role_reveal_rows": _build_secret_role_reveal_rows(game),
                    "role_reveal_note": (
                        f"Тема раунда: {_spy_category_label(game)}. Локация: {game.spy_location or '-'}."
                        if game.kind == "spy" and game.status == "finished"
                        else (
                            f"Категория раунда: {game.whoami_category or 'случайная'}."
                            if game.kind == "whoami" and game.status == "finished"
                            else (
                                f"Тема раунда: {_zlob_category_label(game)}."
                                if game.kind == "zlobcards" and game.status == "finished"
                                else "Роли и фракции раскрыты, победители подсвечены отдельно."
                            )
                        )
                    ),
                    "secret_lines": secret_lines,
                    "score_rows": _build_live_score_rows(game),
                    "_sort_ts": game.started_at or game.created_at,
                }
            )

        cards.sort(
            key=lambda item: (0 if item["status_badge"] == "active" else 1, item["_sort_ts"]),  # type: ignore[index]
            reverse=True,
        )
        for item in cards:
            item.pop("_sort_ts", None)
        return cards

    async def _build_games_dashboard_context(
        activity_repo: SqlAlchemyActivityRepository,
        *,
        user: UserSnapshot,
    ) -> dict[str, object]:
        visible_groups, manageable_chats, manageable_chat_ids, active_games, recent_games_raw = await _collect_game_groups(
            activity_repo,
            user=user,
            recent_limit=6,
        )
        game_cards = await _build_active_game_cards(
            activity_repo,
            user=user,
            visible_groups=visible_groups,
            active_games=active_games,
            manageable_chat_ids=manageable_chat_ids,
        )
        recent_game_cards: list[dict[str, object]] = []
        for game in recent_games_raw:
            chat = visible_groups.get(game.chat_id)
            recent_game_cards.append(
                {
                    "game_id": game.game_id,
                    "kind": game.kind,
                    "title": GAME_DEFINITIONS[game.kind].title,
                    "chat_title": (chat.chat_title if chat is not None else (game.chat_title or f"chat:{game.chat_id}")),
                    "chat_id": str(game.chat_id),
                    "started_at": format_datetime(game.started_at),
                    "result_text": game.winner_text or "Партия завершена.",
                    "personal_notes": _build_recent_personal_notes(game, user_id=user.telegram_user_id),
                    "score_rows": _build_recent_score_rows(game),
                    "role_reveal_rows": _build_secret_role_reveal_rows(game),
                    "role_reveal_note": (
                        f"Тема раунда: {_spy_category_label(game)}. Локация: {game.spy_location or '-'}."
                        if game.kind == "spy"
                        else (f"Категория раунда: {game.whoami_category or 'случайная'}." if game.kind == "whoami" else "Роли и фракции раскрыты, победители подсвечены отдельно.")
                    ),
                    "bred_reveal_rows": _build_bred_reveal_rows(game) if game.kind == "bredovukha" else [],
                }
            )
        game_catalog = _build_game_catalog()
        active_game_chat_ids = {
            int(str(item["chat_id"]))
            for item in game_cards
            if str(item.get("chat_id", "")).lstrip("-").isdigit()
        }
        available_create_chat_options: list[dict[str, str]] = []
        busy_create_chat_options: list[dict[str, str]] = []
        for chat in manageable_chats:
            current_chat_settings = await _chat_settings_for_game(activity_repo, chat_id=chat.chat_id)
            option = {
                "chat_id": str(chat.chat_id),
                "title": chat.chat_title or f"chat:{chat.chat_id}",
                "actions_18_enabled": "true" if current_chat_settings.actions_18_enabled else "false",
            }
            if chat.chat_id in active_game_chat_ids:
                busy_create_chat_options.append(option)
            else:
                available_create_chat_options.append(option)

        default_create_game = game_catalog[0] if game_catalog else None

        return {
            "metrics": [
                {"label": "Активные сессии", "value": str(len(game_cards)), "note": "лобби и запущенные игры", "tone": "violet"},
                {
                    "label": "Где вы игрок",
                    "value": str(sum(1 for item in game_cards if bool(item.get("is_member")))),
                    "note": "игры, где вы уже в составе участников",
                    "tone": "cyan",
                },
                {
                    "label": "Где вы ведущий",
                    "value": str(sum(1 for item in game_cards if bool(item.get("can_manage_games")))),
                    "note": "чаты, где доступно управление фазами",
                    "tone": "magenta",
                },
                {
                    "label": "Чаты для старта",
                    "value": str(len(available_create_chat_options)),
                    "note": "где можно открыть новую игру прямо сейчас",
                    "tone": "indigo",
                },
            ],
            "game_cards": game_cards,
            "recent_game_cards": recent_game_cards,
            "game_catalog": game_catalog,
            "spy_category_options": _build_spy_category_options(),
            "whoami_category_options": _build_whoami_category_options(),
            "zlob_category_options": _build_zlob_category_options(),
            "default_create_kind": default_create_game["key"] if default_create_game else "",
            "default_create_game": default_create_game,
            "create_chat_options": available_create_chat_options,
            "busy_create_chat_options": busy_create_chat_options,
            "has_manageable_chats": bool(manageable_chats),
        }

    def _render_games_dashboard_fragment(context: dict[str, object]) -> str:
        return template_environment.get_template("_games_dashboard.html").render(**context)

    def _games_live_signature(fragment_html: str) -> str:
        return hashlib.sha1(fragment_html.encode("utf-8")).hexdigest()[:16]

    async def _publish_chat_live_event(chat_id: int, *, event_type: str = "chat_refresh") -> None:
        try:
            await GAME_STORE.publish_event(event_type=event_type, scope="chat", chat_id=chat_id)
        except Exception:
            logger.exception("Failed to publish chat live event", extra={"chat_id": chat_id, "event_type": event_type})

    @app.get("/api/live/stream")
    async def live_stream(request: Request):
        scope = (request.query_params.get("scope") or "games").strip().lower()
        chat_id_raw = (request.query_params.get("chat_id") or "").strip()
        chat_id = int(chat_id_raw) if chat_id_raw.lstrip("-").isdigit() else None

        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                raise StarletteHTTPException(status_code=401, detail="Сессия истекла. Войдите снова.")

            if scope not in {"games", "chat"}:
                await session.commit()
                raise StarletteHTTPException(status_code=400, detail="Неизвестная live-область.")

            if scope == "chat":
                if chat_id is None:
                    await session.commit()
                    raise StarletteHTTPException(status_code=400, detail="Для chat-live нужен chat_id.")
                activity_repo = SqlAlchemyActivityRepository(session)
                chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
                await session.commit()
                if chat is None:
                    raise StarletteHTTPException(status_code=403, detail="Нет доступа к этой группе.")
                return await _stream_live_events(scope="chat", chat_id=chat_id)

            await session.commit()
            return await _stream_live_events(scope="games")

    @app.websocket("/api/live/ws/game/{game_id}")
    async def live_game_socket(websocket: WebSocket, game_id: str):
        await websocket.accept()
        live_broker = getattr(GAME_STORE, "live_broker", None)
        if live_broker is None:
            await websocket.send_json({"ok": False, "message": "Live updates are not configured."})
            await websocket.close(code=1013)
            return

        async with session_factory() as session:
            user = await _load_user_from_websocket(session, websocket, touch=True)
            if user is None:
                await session.commit()
                await websocket.send_json({"ok": False, "message": "Сессия истекла. Войдите снова."})
                await websocket.close(code=4401)
                return

            game = await GAME_STORE.get_game(game_id=game_id)
            if game is None:
                await session.commit()
                await websocket.send_json({"ok": False, "message": "Игра не найдена."})
                await websocket.close(code=4404)
                return

            activity_repo = SqlAlchemyActivityRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=game.chat_id)
            allowed = (
                chat is not None
                or user.telegram_user_id == game.owner_user_id
                or user.telegram_user_id in game.players
            )
            await session.commit()
            if not allowed:
                await websocket.send_json({"ok": False, "message": "Нет доступа к этой игре."})
                await websocket.close(code=4403)
                return

        try:
            async for event in live_broker.subscribe(scope="games", chat_id=game.chat_id, game_id=game_id):
                if event is None:
                    await websocket.send_json({"type": "ping"})
                    continue
                await websocket.send_json(event.to_payload())
        except WebSocketDisconnect:
            return

    async def _execute_web_callback(
        callback_data: str,
        *,
        bot: Bot,
        game: GroupGame,
        chat: UserChatOverview,
        user: UserSnapshot,
        activity_repo: SqlAlchemyActivityRepository,
        chat_settings,
        actor_label: str,
        can_manage_games: bool,
        economy_repo: SqlAlchemyEconomyRepository,
    ) -> tuple[bool, str]:
        parts = callback_data.split(":")
        if len(parts) != 3:
            return False, "Некорректные параметры игрового действия."

        prefix = parts[0]
        payload = parts[2]

        if prefix == "gcfg":
            if not can_manage_games:
                return False, "Недостаточно прав для изменения настроек игры."
            option = payload
            if option == "reveal_elim":
                updated_game, error = await GAME_STORE.set_mafia_reveal_eliminated_role(
                    game_id=game.game_id,
                    reveal_eliminated_role=not game.reveal_eliminated_role,
                )
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._persist_mafia_reveal_default(
                    activity_repo,
                    chat_id=chat.chat_id,
                    chat_type=chat.chat_type,
                    chat_title=chat.chat_title,
                    chat_settings=chat_settings,
                    reveal_eliminated_role=updated_game.reveal_eliminated_role,
                )
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, "Режим показа роли выбывшего обновлён."

            if option.startswith("bred_rounds_"):
                delta = 1 if option == "bred_rounds_inc" else -1
                if option == "bred_rounds_noop":
                    return True, f"Раундов: {game.bred_rounds}"
                if option not in {"bred_rounds_inc", "bred_rounds_dec"}:
                    return False, "Неизвестная настройка раундов."
                updated_game, error = await GAME_STORE.set_bred_rounds(game_id=game.game_id, rounds=game.bred_rounds + delta)
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Раундов: {updated_game.bred_rounds}"

            if option.startswith("zlob_rounds_"):
                delta = 1 if option == "zlob_rounds_inc" else -1
                if option == "zlob_rounds_noop":
                    return True, f"Раундов: {game.zlob_rounds}"
                if option not in {"zlob_rounds_inc", "zlob_rounds_dec"}:
                    return False, "Неизвестная настройка раундов."
                updated_game, error = await GAME_STORE.set_zlob_rounds(game_id=game.game_id, rounds=game.zlob_rounds + delta)
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Раундов: {updated_game.zlob_rounds}"

            if option.startswith("zlob_target_"):
                delta = 1 if option == "zlob_target_inc" else -1
                if option == "zlob_target_noop":
                    return True, f"Цель: {game.zlob_target_score}"
                if option not in {"zlob_target_inc", "zlob_target_dec"}:
                    return False, "Неизвестная настройка цели."
                updated_game, error = await GAME_STORE.set_zlob_target_score(
                    game_id=game.game_id,
                    target_score=game.zlob_target_score + delta,
                )
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Цель: {updated_game.zlob_target_score}"

            if option.startswith("bunker_seats_"):
                delta = 1 if option == "bunker_seats_inc" else -1
                if option == "bunker_seats_noop":
                    return True, f"Мест в бункере: {game.bunker_seats}"
                if option not in {"bunker_seats_inc", "bunker_seats_dec"}:
                    return False, "Неизвестная настройка мест в бункере."
                updated_game, error = await GAME_STORE.set_bunker_seats(game_id=game.game_id, seats=game.bunker_seats + delta)
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Мест в бункере: {updated_game.bunker_seats}"

            if option == "spy_cat_next":
                updated_game, error = await GAME_STORE.cycle_spy_category(game_id=game.game_id)
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Тема: {_spy_category_label(updated_game)}"

            if option == "whoami_cat_next":
                updated_game, error = await GAME_STORE.cycle_whoami_category(
                    game_id=game.game_id,
                    actions_18_enabled=chat_settings.actions_18_enabled,
                )
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Тема: {updated_game.whoami_category or 'случайная'}"

            if option == "zlob_cat_next":
                updated_game, error = await GAME_STORE.cycle_zlob_category(
                    game_id=game.game_id,
                    actions_18_enabled=chat_settings.actions_18_enabled,
                )
                if updated_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, f"Тема: {_zlob_category_label(updated_game)}"

            return False, "Неизвестная настройка игры."

        if prefix == "game":
            action = parts[1]
            if action == "join":
                updated_game, status = await GAME_STORE.join(game_id=game.game_id, user_id=user.telegram_user_id, user_label=actor_label)
                if updated_game is None:
                    return False, "Игра не найдена."
                if status == "already_joined":
                    return True, "Вы уже в игре."
                if status == "not_lobby":
                    return False, "Лобби закрыто, игра уже началась."
                await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                return True, "Вы присоединились к игре."

            if action == "start":
                can_start = await game_router_module._actor_can_start_game(
                    activity_repo,
                    game=game,
                    chat_type=chat.chat_type,
                    chat_title=chat.chat_title,
                    user=user,
                    bootstrap_if_missing_owner=False,
                )
                if not can_start:
                    return False, "Старт доступен создателю лобби или ведущему с правом manage_games."
                started_game, error = await GAME_STORE.start(
                    game_id=game.game_id,
                    actions_18_enabled=chat_settings.actions_18_enabled,
                )
                if started_game is None:
                    return False, "Игра не найдена."
                if error:
                    return False, error

                await game_router_module._safe_edit_or_send_game_board(bot, started_game, chat_settings)
                success_message = "Игра запущена."
                if started_game.kind in {"spy", "mafia", "bunker", "whoami", "zlobcards"}:
                    failed_dm = await game_router_module._send_roles_to_private(bot, started_game)
                    if failed_dm > 0:
                        await game_router_module._notify_private_delivery_warning(bot, started_game, failed_dm)
                        success_message = (
                            f"{success_message} {game_router_module._build_private_delivery_warning_text(failed_dm)}"
                        )
                if started_game.kind == "mafia":
                    game_router_module._schedule_phase_timer(bot, started_game, chat_settings)
                    await game_router_module._notify_mafia_night_actions(bot, started_game)
                    await game_router_module._send_game_feed_event(
                        bot,
                        started_game,
                        text=game_router_module.build_mafia_start_text(
                            round_no=started_game.round_no,
                            night_seconds=chat_settings.mafia_night_seconds,
                        ),
                    )
                    return True, success_message
                if started_game.kind == "spy":
                    await game_router_module._send_game_feed_event(
                        bot,
                        started_game,
                        text=game_router_module.build_spy_start_text(category=_spy_category_label(started_game)),
                    )
                    return True, success_message
                if started_game.kind == "whoami":
                    await game_router_module._send_game_feed_event(
                        bot,
                        started_game,
                        text=game_router_module.build_whoami_start_text(category=started_game.whoami_category or "случайная"),
                    )
                    return True, success_message
                if started_game.kind == "zlobcards":
                    game_router_module._schedule_phase_timer(bot, started_game, chat_settings)
                    await game_router_module._send_game_feed_event(
                        bot,
                        started_game,
                        text=game_router_module.build_zlobcards_start_text(category=_zlob_category_label(started_game)),
                    )
                    return True, success_message
                if started_game.kind == "number":
                    await game_router_module._send_game_feed_event(bot, started_game, text=game_router_module.build_number_start_text())
                    return True, success_message
                if started_game.kind == "dice":
                    await game_router_module._send_game_feed_event(bot, started_game, text=game_router_module.build_dice_start_text())
                    return True, success_message
                if started_game.kind == "quiz":
                    await game_router_module._sync_quiz_feed_message(bot, started_game, question_no=1)
                    return True, success_message
                if started_game.kind == "bunker":
                    await game_router_module._send_game_feed_event(bot, started_game, text=game_router_module.build_bunker_start_text())
                    actor = started_game.players.get(started_game.bunker_current_actor_user_id or 0, "-")
                    await game_router_module._safe_edit_or_send_game_board(
                        bot,
                        started_game,
                        chat_settings,
                        note=f"<b>Первый ход раскрытия:</b> {escape(actor)} раскрывает характеристику в ЛС.",
                    )
                    await game_router_module._notify_bunker_reveal_turn(bot, started_game)
                    return True, success_message
                return True, success_message

            if action in {"cancel", "advance", "reveal"} and not can_manage_games:
                return False, "Недостаточно прав для управления игрой."

            if action == "cancel":
                game_router_module._cancel_phase_timer(game.game_id)
                if game.kind == "quiz":
                    await game_router_module._sync_quiz_feed_message(bot, game, question_no=None)
                finished_game = await GAME_STORE.finish(game_id=game.game_id, winner_text="Игра остановлена ведущим.")
                if finished_game is None:
                    return False, "Игра не найдена."
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    finished_game,
                    chat_settings,
                    include_reveal=(finished_game.kind in {"spy", "mafia"}),
                )
                await game_router_module._send_game_feed_event(bot, finished_game, text="<b>Ведущий:</b> Игра остановлена ведущим.")
                return True, "Игра завершена."

            if action == "reveal":
                if game.kind != "spy" or game.status != "started":
                    return False, "Раскрытие доступно только в активной игре «Шпион»."
                finished_game = await GAME_STORE.finish(game_id=game.game_id, winner_text="Игра завершена по решению ведущего.")
                if finished_game is None:
                    return False, "Игра не найдена."
                await game_router_module._safe_edit_or_send_game_board(bot, finished_game, chat_settings, include_reveal=True)
                await game_router_module._send_game_feed_event(
                    bot,
                    finished_game,
                    text="<b>Ведущий:</b> Игра «Шпион» завершена, роли раскрыты.",
                )
                return True, "Роли раскрыты."

            if action == "advance":
                if game.status != "started":
                    return False, "Игра уже завершена."
                if game.kind == "quiz":
                    _, error = await game_router_module._resolve_quiz_round(
                        bot,
                        game.game_id,
                        chat_settings,
                        economy_repo=economy_repo,
                        force=True,
                        triggered_by_auto=False,
                    )
                    return (error is None), (error or "Вопрос закрыт и обработан.")
                if game.kind == "bredovukha":
                    if game.phase == "category_pick":
                        opened_game, category, error = await GAME_STORE.bred_force_pick_category(game_id=game.game_id)
                        if opened_game is None:
                            return False, "Игра не найдена."
                        if error:
                            return False, error
                        failed_dm = await game_router_module._notify_bred_private_answers(bot, opened_game)
                        note = f"<b>Категория раунда:</b> {escape(category or '-')}\n<b>Этап:</b> сбор ответов в ЛС."
                        if failed_dm > 0:
                            note = f"{note}\n<b>ЛС недоступно:</b> {failed_dm} игрок(ов)."
                        await game_router_module._safe_edit_or_send_game_board(bot, opened_game, chat_settings, note=note)
                        return True, "Категория выбрана случайно."
                    if game.phase == "private_answers":
                        opened_game, error = await GAME_STORE.bred_open_vote(game_id=game.game_id, force=True)
                        if opened_game is None:
                            return False, "Игра не найдена."
                        if error:
                            return False, error
                        await game_router_module._safe_edit_or_send_game_board(
                            bot,
                            opened_game,
                            chat_settings,
                            note="<b>Этап:</b> сбор ответов завершён, открыто голосование.",
                        )
                        return True, "Открыто голосование."
                    if game.phase == "public_vote":
                        _, error = await game_router_module._resolve_bred_round(
                            bot,
                            game.game_id,
                            chat_settings,
                            economy_repo=economy_repo,
                            force=True,
                            triggered_by_auto=False,
                        )
                        return (error is None), (error or "Голосование завершено.")
                    return False, "Сейчас нет шага для переключения."
                if game.kind == "bunker":
                    if game.phase == "bunker_reveal":
                        updated_game, result, error = await GAME_STORE.bunker_force_advance_reveal(game_id=game.game_id)
                        if updated_game is None:
                            return False, "Игра не найдена."
                        if error or result is None:
                            return False, error or "Не удалось переключить ход."
                        note = f"<b>Ход пропущен:</b> {escape(result.actor_user_label)}."
                        if result.vote_opened:
                            note = f"{note}\n<b>Этап:</b> открыто голосование на выбывание."
                            await game_router_module._notify_bunker_vote_private(bot, updated_game)
                        elif result.next_actor_label is not None:
                            note = f"{note}\n<b>Следующий ход:</b> {escape(result.next_actor_label)} раскрывает характеристику."
                            await game_router_module._notify_bunker_reveal_turn(bot, updated_game)
                        await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings, note=note)
                        return True, "Ход переключён."
                    if game.phase == "bunker_vote":
                        _, error = await game_router_module._resolve_bunker_vote(
                            bot,
                            game.game_id,
                            chat_settings,
                            economy_repo=economy_repo,
                            force=True,
                            triggered_by_auto=False,
                        )
                        return (error is None), (error or "Голосование завершено.")
                    return False, "Сейчас нет шага для переключения."
                if game.kind == "zlobcards":
                    if game.phase == "private_answers":
                        opened_game, error = await game_router_module._open_zlob_vote_phase(
                            bot,
                            game.game_id,
                            chat_settings,
                            force=True,
                            triggered_by_auto=False,
                        )
                        if opened_game is None:
                            return False, "Игра не найдена."
                        return (error is None), (error or "Открыто голосование.")
                    if game.phase == "public_vote":
                        _, error = await game_router_module._resolve_zlob_round(
                            bot,
                            game.game_id,
                            chat_settings,
                            economy_repo=economy_repo,
                            force=True,
                            triggered_by_auto=False,
                        )
                        return (error is None), (error or "Раунд обработан.")
                    return False, "Сейчас нет шага для переключения."
                if game.kind != "mafia":
                    return False, "Авто-переход доступен для мафии, викторины, Бредовухи, Бункера и Злобных Карт."
                if game.phase == "night":
                    game_router_module._cancel_phase_timer(game.game_id)
                    await game_router_module._advance_mafia_night(
                        bot,
                        game.game_id,
                        chat_settings,
                        economy_repo=economy_repo,
                        triggered_by_timer=False,
                    )
                    return True, "Ночь завершена."
                if game.phase == "day_discussion":
                    game_router_module._cancel_phase_timer(game.game_id)
                    await game_router_module._open_mafia_day_vote(
                        bot,
                        game.game_id,
                        chat_settings,
                        triggered_by_timer=False,
                    )
                    return True, "Обсуждение завершено, открыто голосование."
                if game.phase == "day_vote":
                    game_router_module._cancel_phase_timer(game.game_id)
                    await game_router_module._resolve_mafia_day_vote(
                        bot,
                        game.game_id,
                        chat_settings,
                        economy_repo=economy_repo,
                        triggered_by_timer=False,
                    )
                    return True, "Дневное голосование завершено."
                if game.phase == "day_execution_confirm":
                    game_router_module._cancel_phase_timer(game.game_id)
                    await game_router_module._resolve_mafia_execution_confirm(
                        bot,
                        game.game_id,
                        chat_settings,
                        economy_repo=economy_repo,
                        triggered_by_timer=False,
                    )
                    return True, "Подтверждение казни завершено."
                return False, "Сейчас нет шага для переключения."

            return False, "Неизвестное игровое действие."

        if prefix == "gdice":
            if payload != "roll":
                return False, "Неизвестное действие кубиков."
            current, result, error = await GAME_STORE.dice_register_roll(game_id=game.game_id, user_id=user.telegram_user_id)
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."
            note = (
                f"<b>Последний бросок:</b> {escape(current.players.get(user.telegram_user_id, actor_label))} -> "
                f"<code>{result.roll_value}</code> ({result.rolled_count}/{result.total_players})"
            )
            if result.finished:
                reward_line = await game_router_module._grant_game_rewards_if_needed(
                    current,
                    economy_repo=economy_repo,
                    chat_settings=chat_settings,
                )
                if reward_line:
                    note = f"{note}\n{reward_line}"
                await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                await game_router_module._send_game_feed_event(
                    bot,
                    current,
                    text=f"<b>Ведущий:</b> Раунд кубиков завершён.\n{note}",
                )
                return True, f"Бросок: {result.roll_value}. Раунд завершён."
            await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
            return True, f"Бросок принят: {result.roll_value}"

        if prefix == "gquiz":
            if not payload.isdigit():
                return False, "Некорректный вариант ответа."
            option_index = int(payload)
            current, result, error = await GAME_STORE.quiz_submit_answer(
                game_id=game.game_id,
                user_id=user.telegram_user_id,
                option_index=option_index,
            )
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."
            snapshot_game, answered_count, total_players = await GAME_STORE.quiz_get_answer_snapshot(game_id=game.game_id)
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    snapshot_game,
                    chat_settings,
                    note=f"<b>Прогресс вопроса:</b> {answered_count}/{total_players}",
                )
            if result.all_answered:
                await game_router_module._resolve_quiz_round(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=False,
                    triggered_by_auto=True,
                )
                return True, "Ответ принят. Все ответили, раунд обработан."
            if result.previous_answer_index == option_index:
                return True, "Этот вариант уже был выбран."
            return True, "Ответ принят."

        if prefix == "gspy":
            if not payload.isdigit():
                return False, "Некорректная цель голосования."
            target_user_id = int(payload)
            current, resolution, previous_target_user_id, error = await GAME_STORE.spy_register_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                target_user_id=target_user_id,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            target_label = current.players.get(target_user_id, f"user:{target_user_id}")
            if resolution is None:
                game_snapshot, voted_count, total_players, _, _ = await GAME_STORE.spy_get_vote_snapshot(game_id=current.game_id)
                if game_snapshot is not None:
                    await game_router_module._safe_edit_or_send_game_board(
                        bot,
                        game_snapshot,
                        chat_settings,
                        note=f"<b>Подозрение:</b> {escape(actor_label)} -> {escape(target_label)}.\n<b>Прогресс:</b> {voted_count}/{total_players}.",
                    )
                if previous_target_user_id == target_user_id:
                    return True, "Этот голос уже учтён."
                return True, f"Голос принят: {target_label}"
            reward_line = await game_router_module._grant_game_rewards_if_needed(
                current,
                economy_repo=economy_repo,
                chat_settings=chat_settings,
            )
            result_text = game_router_module._format_spy_vote_resolution(current, resolution)
            note = result_text if not reward_line else f"{result_text}\n{reward_line}"
            await game_router_module._safe_edit_or_send_game_board(
                bot,
                current,
                chat_settings,
                note=note,
                include_reveal=True,
            )
            await game_router_module._send_game_feed_event(
                bot,
                current,
                text=(
                    f"<b>Ведущий:</b> {result_text}"
                    + (f"\n{reward_line}" if reward_line else "")
                    + f"\n{game_router_module._render_roles_reveal(current)}"
                ),
            )
            return True, "Голос принят. Игра завершена."

        if prefix == "gwho":
            if payload not in {"yes", "no", "unknown", "irrelevant"}:
                return False, "Некорректный ответ."
            current, resolution, error = await GAME_STORE.whoami_answer_question(
                game_id=game.game_id,
                responder_user_id=user.telegram_user_id,
                answer_code=payload,  # type: ignore[arg-type]
            )
            if error:
                return False, error
            if current is None or resolution is None:
                return False, "Игра не найдена."

            note = game_router_module._format_whoami_answer_resolution(current, resolution)
            await game_router_module._safe_edit_or_send_game_board(
                bot,
                current,
                chat_settings,
                note=note,
            )
            return True, f"Ответ: {resolution.answer_label}"

        if prefix == "gzlobp":
            if payload == "noop":
                snapshot_game, submitted_count, total_players = await GAME_STORE.zlob_get_submit_snapshot(game_id=game.game_id)
                if snapshot_game is not None:
                    await game_router_module._safe_edit_or_send_game_board(
                        bot,
                        snapshot_game,
                        chat_settings,
                        note=f"<b>Сдача карточек:</b> {submitted_count}/{total_players}",
                    )
                return True, f"Сдано: {submitted_count}/{total_players}"

            card_indexes: tuple[int, ...]
            if "-" in payload:
                first_raw, second_raw = payload.split("-", maxsplit=1)
                if not first_raw.isdigit() or not second_raw.isdigit():
                    return False, "Некорректный выбор карточек."
                card_indexes = (int(first_raw), int(second_raw))
            else:
                if not payload.isdigit():
                    return False, "Некорректный выбор карточек."
                card_indexes = (int(payload),)

            current, result, error = await GAME_STORE.zlob_submit_cards(
                game_id=game.game_id,
                user_id=user.telegram_user_id,
                card_indexes=card_indexes,
            )
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."

            if result.vote_opened:
                game_router_module._cancel_phase_timer(current.game_id)
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    current,
                    chat_settings,
                    note="<b>Этап:</b> все карточки сданы, открыто голосование.",
                )
                game_router_module._schedule_phase_timer(bot, current, chat_settings)
                return True, "Карточки приняты. Открыто голосование."

            await game_router_module._safe_edit_or_send_game_board(
                bot,
                current,
                chat_settings,
                note=f"<b>Сдача карточек:</b> {result.submitted_count}/{result.total_players}",
            )
            if result.previous_submission is None:
                return True, "Карточки отправлены."
            return True, "Выбор обновлён."

        if prefix == "gzlobv":
            if payload == "noop":
                snapshot_game, voted_count, total_players, vote_tally = await GAME_STORE.zlob_get_vote_snapshot(game_id=game.game_id)
                if snapshot_game is not None:
                    leader_text = "пока нет"
                    if vote_tally:
                        top_votes = max(vote_tally)
                        if top_votes > 0:
                            leaders = [index for index, count in enumerate(vote_tally) if count == top_votes]
                            if len(leaders) == 1:
                                leader_index = leaders[0]
                                leader_text = f"{game_router_module._quiz_choice_label(leader_index)}. {snapshot_game.zlob_options[leader_index]} ({top_votes})"
                            else:
                                leader_text = f"ничья по {top_votes} голос(ам)"
                    await game_router_module._safe_edit_or_send_game_board(
                        bot,
                        snapshot_game,
                        chat_settings,
                        note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}. Лидер: {escape(leader_text)}.",
                    )
                return True, f"Голосов: {voted_count}/{total_players}"

            if not payload.isdigit():
                return False, "Некорректный вариант."
            option_index = int(payload)
            current, result, error = await GAME_STORE.zlob_register_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                option_index=option_index,
            )
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."

            if result.previous_option_index == option_index:
                return True, "Этот голос уже учтён."

            snapshot_game, voted_count, total_players, _ = await GAME_STORE.zlob_get_vote_snapshot(game_id=current.game_id)
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    snapshot_game,
                    chat_settings,
                    note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}",
                )

            if result.all_voted:
                game_router_module._cancel_phase_timer(current.game_id)
                await game_router_module._resolve_zlob_round(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=False,
                    triggered_by_auto=True,
                )
                return True, "Голос принят. Раунд обработан."

            if result.previous_option_index is None:
                return True, "Голос принят."
            return True, "Голос обновлён."

        if prefix == "gbredcat":
            if not payload.isdigit():
                return False, "Некорректный выбор категории."
            option_index = int(payload)
            current, category, error = await GAME_STORE.bred_choose_category(
                game_id=game.game_id,
                actor_user_id=user.telegram_user_id,
                option_index=option_index,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            failed_dm = await game_router_module._notify_bred_private_answers(bot, current)
            note = (
                f"<b>Категория выбрана:</b> {escape(category or '-')}\n"
                f"<b>Выбрал:</b> {escape(actor_label)}\n"
                "<b>Этап:</b> сбор ответов в ЛС."
            )
            if failed_dm > 0:
                note = f"{note}\n<b>ЛС недоступно:</b> {failed_dm} игрок(ов)."
            await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
            return True, "Категория выбрана."

        if prefix == "gbred":
            if not payload.isdigit():
                return False, "Некорректный вариант."
            option_index = int(payload)
            current, result, error = await GAME_STORE.bred_register_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                option_index=option_index,
            )
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."
            snapshot_game, voted_count, total_players, _ = await GAME_STORE.bred_get_vote_snapshot(game_id=current.game_id)
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    snapshot_game,
                    chat_settings,
                    note=f"<b>Прогресс голосования:</b> {voted_count}/{total_players}",
                )
            if result.all_voted:
                await game_router_module._resolve_bred_round(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=False,
                    triggered_by_auto=True,
                )
                return True, "Голос принят. Раунд обработан."
            return True, "Голос принят."

        if prefix == "gbkr":
            current, result, error = await GAME_STORE.bunker_register_reveal(
                game_id=game.game_id,
                actor_user_id=user.telegram_user_id,
                field_key=payload,
            )
            if error:
                return False, error
            if current is None or result is None:
                return False, "Игра не найдена."
            note = (
                f"<b>Раскрытие:</b> {escape(result.actor_user_label)} открыл(а) "
                f"<b>{escape(result.field_label or '-')}</b>: <code>{escape(result.revealed_value or '-')}</code>.\n"
                f"<b>Лично открыто:</b> {result.revealed_count_for_actor}/{result.total_fields_for_actor}"
            )
            if result.vote_opened:
                await game_router_module._notify_bunker_vote_private(bot, current)
                note = f"{note}\n<b>Этап:</b> открыто голосование на выбывание."
            elif result.next_actor_label is not None:
                await game_router_module._notify_bunker_reveal_turn(bot, current)
                note = f"{note}\n<b>Следующий ход:</b> {escape(result.next_actor_label)}."
            await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
            await game_router_module._send_game_feed_event(bot, current, text=note)
            return True, "Характеристика раскрыта."

        if prefix == "gbkv":
            if not payload.isdigit():
                return False, "Некорректная цель."
            target_user_id = int(payload)
            current, previous_target_user_id, error = await GAME_STORE.bunker_register_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                target_user_id=target_user_id,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            snapshot_game, voted_count, total_alive, _, _ = await GAME_STORE.bunker_get_vote_snapshot(game_id=current.game_id)
            target_label = current.players.get(target_user_id, f"user:{target_user_id}")
            note = f"<b>Голос:</b> {escape(actor_label)} против {escape(target_label)}.\n<b>Прогресс:</b> {voted_count}/{total_alive}"
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(bot, snapshot_game, chat_settings, note=note)
            await game_router_module._send_game_feed_event(bot, current, text=note)
            if total_alive > 0 and voted_count == total_alive:
                await game_router_module._resolve_bunker_vote(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    force=False,
                    triggered_by_auto=True,
                )
                return True, "Голос принят. Подведён итог этапа."
            if previous_target_user_id == target_user_id:
                return True, "Этот голос уже учтён."
            return True, "Голос принят."

        if prefix == "gmact":
            if not payload.isdigit():
                return False, "Некорректная цель."
            target_user_id = int(payload)
            current, error = await GAME_STORE.mafia_register_night_action(
                game_id=game.game_id,
                actor_user_id=user.telegram_user_id,
                target_user_id=target_user_id,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            _, ready, _ = await GAME_STORE.mafia_is_night_ready(game_id=current.game_id)
            if ready:
                game_router_module._cancel_phase_timer(current.game_id)
                await game_router_module._advance_mafia_night(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    triggered_by_timer=False,
                )
                return True, "Действие сохранено. Все готовы, ночь завершена."
            return True, "Ночное действие сохранено."

        if prefix == "gmvote":
            if not payload.isdigit():
                return False, "Некорректная цель."
            target_user_id = int(payload)
            current, _, error = await GAME_STORE.mafia_register_day_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                target_user_id=target_user_id,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            snapshot_game, voted_count, alive_count = await GAME_STORE.mafia_get_vote_snapshot(game_id=current.game_id)
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    snapshot_game,
                    chat_settings,
                    note=f"<b>Текущий прогресс голосования:</b> {voted_count}/{alive_count}",
                )
            if alive_count > 0 and voted_count == alive_count:
                game_router_module._cancel_phase_timer(current.game_id)
                await game_router_module._resolve_mafia_day_vote(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    triggered_by_timer=False,
                )
                return True, "Голос принят. Все проголосовали, этап закрыт."
            return True, "Голос принят."

        if prefix == "gmconfirm":
            if payload not in {"yes", "no"}:
                return False, "Некорректное решение."
            approve = payload == "yes"
            current, _, error = await GAME_STORE.mafia_register_execution_confirm_vote(
                game_id=game.game_id,
                voter_user_id=user.telegram_user_id,
                approve=approve,
            )
            if error:
                return False, error
            if current is None:
                return False, "Игра не найдена."
            snapshot_game, voted_count, alive_count, yes_count, no_count = await GAME_STORE.mafia_get_execution_confirm_snapshot(game_id=current.game_id)
            if snapshot_game is not None:
                await game_router_module._safe_edit_or_send_game_board(
                    bot,
                    snapshot_game,
                    chat_settings,
                    note=f"<b>Подтверждение:</b> да={yes_count}, нет={no_count}, проголосовали {voted_count}/{alive_count}",
                )
                await game_router_module._sync_execution_confirm_message(bot, snapshot_game, force_new=False)
            if alive_count > 0 and voted_count == alive_count:
                game_router_module._cancel_phase_timer(current.game_id)
                await game_router_module._resolve_mafia_execution_confirm(
                    bot,
                    current.game_id,
                    chat_settings,
                    economy_repo=economy_repo,
                    triggered_by_timer=False,
                )
                return True, "Подтверждение принято, этап закрыт."
            return True, "Решение сохранено."

        return False, "Неизвестное действие."

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=False)
            await session.commit()
        page_context = build_landing_context(
            bot_username=bot_username,
            bot_dm_url=f"https://t.me/{bot_username}",
            user=user,
            flash=request.query_params.get("flash"),
            error=request.query_params.get("error"),
        )
        page_context.update(
            _landing_layout_context(
                user=user,
                flash=page_context["flash"],
                error=page_context["error"],
            )
        )
        return _render_template("landing.html", **page_context)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=False)
            await session.commit()
        if user is not None:
            return _redirect("/app")
        return _render_template(
            "login.html",
            **_login_context(
                flash=request.query_params.get("flash"),
                error=request.query_params.get("error"),
            ),
        )

    @app.post("/login")
    async def login_submit(request: Request):
        now = _now_utc()
        host = request.client.host if request.client is not None and request.client.host else "unknown"
        if _check_rate_limit(host, now):
            return _redirect(
                _with_message(
                    "/login",
                    key="error",
                    text="Слишком много попыток. Подождите и запросите новый код у бота.",
                )
            )

        form = await _parse_form(request)
        code = normalize_login_code(form.get("code"))
        if len(code) != 6:
            _register_failed_attempt(host, now)
            return _redirect(_with_message("/login", key="error", text="Введите корректный шестизначный код."))

        async with session_factory() as session:
            auth_repo = SqlAlchemyWebAuthRepository(session)
            await auth_repo.purge_expired_state(now=now)
            user = await auth_repo.consume_login_code(
                code_digest=digest_login_code(secret=settings.resolved_web_auth_secret, code=code),
                now=now,
            )
            if user is None:
                await session.commit()
                _register_failed_attempt(host, now)
                return _redirect(
                    _with_message(
                        "/login",
                        key="error",
                        text="Код не найден, уже использован или истёк. Запросите новый через /login у бота.",
                    )
                )

            token = generate_session_token()
            await auth_repo.create_session(
                user_id=user.telegram_user_id,
                session_digest=digest_session_token(secret=settings.resolved_web_auth_secret, token=token),
                expires_at=now + timedelta(hours=max(1, settings.web_session_ttl_hours)),
                now=now,
            )
            await session.commit()
            failed_attempts.pop(host, None)

        response = _redirect(_with_message("/app", key="flash", text="Вход выполнен."))
        response.set_cookie(
            settings.web_session_cookie_name,
            token,
            httponly=True,
            secure=settings.web_session_cookie_secure,
            samesite="lax",
            max_age=max(3600, settings.web_session_ttl_hours * 3600),
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        token = request.cookies.get(settings.web_session_cookie_name)
        if token:
            async with session_factory() as session:
                auth_repo = SqlAlchemyWebAuthRepository(session)
                await auth_repo.revoke_session(
                    session_digest=digest_session_token(secret=settings.resolved_web_auth_secret, token=token),
                    now=_now_utc(),
                )
                await session.commit()

        response = _redirect(_with_message("/login", key="flash", text="Сессия завершена."))
        response.delete_cookie(settings.web_session_cookie_name)
        return response

    @app.get("/app", response_class=HTMLResponse)
    async def app_home(request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)

            merged: dict[int, UserChatOverview] = {group.chat_id: group for group in admin_groups}
            for group in activity_groups:
                merged.setdefault(group.chat_id, group)
            ordered_groups = sorted(
                merged.values(),
                key=lambda item: item.last_seen_at or datetime.min.replace(tzinfo=_UTC),
                reverse=True,
            )

            global_dashboard = await _load_dashboard_if_exists(
                economy_repo,
                mode="global",
                chat_id=None,
                user_id=user.telegram_user_id,
            )
            await session.commit()

        page_context = build_home_context(
            user=user,
            admin_groups=admin_groups,
            activity_groups=ordered_groups,
            global_dashboard=global_dashboard,
            flash=request.query_params.get("flash"),
            error=request.query_params.get("error"),
        )
        page_context.update(
            _home_layout_context(
                flash=page_context["flash"],
                error=page_context["error"],
            )
        )
        return _render_template("home.html", **page_context)

    @app.get("/app/games", response_class=HTMLResponse)
    async def games_page(request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            dashboard_context = await _build_games_dashboard_context(activity_repo, user=user)
            await session.commit()

        dashboard_html = _render_games_dashboard_fragment(dashboard_context)

        page_context: dict[str, object] = {
            "page_title": "Selara • Активные игры",
            "page_name": "games",
            "hero_title": "Активные игры",
            "hero_subtitle": (
                "Сессии из Telegram отображаются здесь в реальном времени. "
                "Можно запускать новые лобби прямо отсюда, а внутри партии интерфейс живёт отдельными экранами вместо копии клавиатуры бота."
            ),
            "live_signature": _games_live_signature(dashboard_html),
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        }
        page_context.update(dashboard_context)
        page_context.update(
            _games_layout_context(
                flash=page_context["flash"],  # type: ignore[index]
                error=page_context["error"],  # type: ignore[index]
            )
        )
        return _render_template("games.html", **page_context)

    @app.get("/app/games/live")
    async def games_live(request: Request):
        signature = (request.query_params.get("signature") or "").strip()
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                redirect_path = _with_message("/login", key="error", text="Сессия истекла. Войдите снова.")
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect=redirect_path)

            activity_repo = SqlAlchemyActivityRepository(session)
            dashboard_context = await _build_games_dashboard_context(activity_repo, user=user)
            await session.commit()

        dashboard_html = _render_games_dashboard_fragment(dashboard_context)
        fresh_signature = _games_live_signature(dashboard_html)
        if signature and signature == fresh_signature:
            return JSONResponse(content={"ok": True, "changed": False, "signature": fresh_signature}, status_code=200)
        return JSONResponse(
            content={"ok": True, "changed": True, "signature": fresh_signature, "html": dashboard_html},
            status_code=200,
        )

    @app.post("/app/games/create")
    async def game_create_submit(request: Request):
        prefers_json = _prefers_json(request)
        form = await _parse_form(request)
        kind_raw = (form.get("kind") or "").strip()
        chat_id_raw = (form.get("chat_id") or "").strip()
        spy_category_raw = (form.get("spy_category") or "").strip()
        whoami_category_raw = (form.get("whoami_category") or "").strip()
        zlob_category_raw = (form.get("zlob_category") or "").strip()

        definition = GAME_DEFINITIONS.get(kind_raw)  # type: ignore[arg-type]
        if definition is None:
            redirect_path = _with_message("/app/games", key="error", text="Выберите игру для запуска.")
            if prefers_json:
                return _json_result(ok=False, message="Выберите игру для запуска.", status_code=400, redirect=redirect_path)
            return _redirect(redirect_path)
        if definition.key not in GAME_LAUNCHABLE_KINDS:
            redirect_path = _with_message("/app/games", key="error", text="Эта игра больше не доступна для новых запусков.")
            if prefers_json:
                return _json_result(ok=False, message="Эта игра больше не доступна для новых запусков.", status_code=400, redirect=redirect_path)
            return _redirect(redirect_path)

        if not chat_id_raw or not chat_id_raw.lstrip("-").isdigit():
            redirect_path = _with_message("/app/games", key="error", text="Выберите чат для создания игры.")
            if prefers_json:
                return _json_result(ok=False, message="Выберите чат для создания игры.", status_code=400, redirect=redirect_path)
            return _redirect(redirect_path)

        chat_id = int(chat_id_raw)

        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                redirect_path = _with_message("/login", key="error", text="Сессия истекла. Войдите снова.")
                if prefers_json:
                    return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect=redirect_path)
                return _redirect(redirect_path)

            activity_repo = SqlAlchemyActivityRepository(session)
            manageable_chats = await _collect_manageable_game_chats(
                activity_repo,
                user_id=user.telegram_user_id,
            )
            chat = next((item for item in manageable_chats if item.chat_id == chat_id), None)
            if chat is None:
                await session.commit()
                redirect_path = _with_message(
                    "/app/games",
                    key="error",
                    text="Недостаточно прав для запуска игры в этом чате.",
                )
                if prefers_json:
                    return _json_result(
                        ok=False,
                        message="Недостаточно прав для запуска игры в этом чате.",
                        status_code=403,
                        redirect=redirect_path,
                    )
                return _redirect(redirect_path)

            chat_settings = await _chat_settings_for_game(activity_repo, chat_id=chat.chat_id)
            actor_label = await game_router_module._resolve_chat_player_label(
                activity_repo,
                chat_id=chat.chat_id,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            game, error = await GAME_STORE.create_lobby(
                kind=definition.key,
                chat_id=chat.chat_id,
                chat_title=chat.chat_title,
                owner_user_id=user.telegram_user_id,
                owner_label=actor_label,
                reveal_eliminated_role=chat_settings.mafia_reveal_eliminated_role,
                spy_category=spy_category_raw or None,
                whoami_category=whoami_category_raw or None,
                zlob_category=zlob_category_raw or None,
                actions_18_enabled=chat_settings.actions_18_enabled,
            )
            if error:
                await session.commit()
                redirect_path = _with_message("/app/games", key="error", text=error)
                if prefers_json:
                    return _json_result(ok=False, message=error, status_code=400, redirect=redirect_path)
                return _redirect(redirect_path)
            if game is None:
                await session.commit()
                redirect_path = _with_message("/app/games", key="error", text="Не удалось создать игру.")
                if prefers_json:
                    return _json_result(ok=False, message="Не удалось создать игру.", status_code=500, redirect=redirect_path)
                return _redirect(redirect_path)

            bot = await _get_game_bot()
            await game_router_module._safe_edit_or_send_game_board(bot, game, chat_settings)
            await session.commit()

        chat_title = chat.chat_title or str(chat.chat_id)
        message = f"{definition.title} создана в чате «{chat_title}»."
        redirect_path = _with_message("/app/games", key="flash", text=message)
        if prefers_json:
            return _json_result(ok=True, message=message, status_code=200, redirect=redirect_path)
        return _redirect(redirect_path)

    @app.post("/app/games/action")
    async def game_action_submit(request: Request):
        prefers_json = _prefers_json(request)
        form = await _parse_form(request)
        callback_data = (form.get("callback_data") or "").strip()
        form_action = (form.get("action") or "").strip()
        game_id = _game_id_from_callback_data(callback_data) or (form.get("game_id") or "").strip()
        if not game_id:
            redirect_path = _with_message("/app/games", key="error", text="Не удалось определить игру.")
            if prefers_json:
                return _json_result(ok=False, message="Не удалось определить игру.", status_code=400, redirect=redirect_path)
            return _redirect(redirect_path)

        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                redirect_path = _with_message("/login", key="error", text="Сессия истекла. Войдите снова.")
                if prefers_json:
                    return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect=redirect_path)
                return _redirect(redirect_path)

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)

            game = await GAME_STORE.get_game(game_id)
            if game is None or game.status == "finished":
                await session.commit()
                redirect_path = _with_message("/app/games", key="error", text="Игра не найдена или уже завершена.")
                if prefers_json:
                    return _json_result(ok=False, message="Игра не найдена или уже завершена.", status_code=404, redirect=redirect_path)
                return _redirect(redirect_path)

            visible_groups, _manageable_chats, manageable_chat_ids, _active_games, _recent_games = await _collect_game_groups(
                activity_repo,
                user=user,
                extra_games=(game,),
                recent_limit=6,
            )
            chat = visible_groups.get(game.chat_id) or _group_overview_from_game(game)
            can_view_game = game.chat_id in visible_groups
            can_manage_games = game.chat_id in manageable_chat_ids
            is_member = user.telegram_user_id in game.players or game.owner_user_id == user.telegram_user_id
            if not (can_view_game or is_member or can_manage_games):
                await session.commit()
                redirect_path = _with_message("/app/games", key="error", text="Эта игра недоступна вашему аккаунту.")
                if prefers_json:
                    return _json_result(ok=False, message="Эта игра недоступна вашему аккаунту.", status_code=403, redirect=redirect_path)
                return _redirect(redirect_path)

            chat_settings = await _chat_settings_for_game(activity_repo, chat_id=game.chat_id)
            actor_label = await game_router_module._resolve_chat_player_label(
                activity_repo,
                chat_id=game.chat_id,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            await GAME_STORE.set_player_label(
                chat_id=game.chat_id,
                user_id=user.telegram_user_id,
                user_label=actor_label,
            )
            bot = await _get_game_bot()

            success = False
            message = "Неизвестная операция."

            if callback_data:
                success, message = await _execute_web_callback(
                    callback_data,
                    bot=bot,
                    game=game,
                    chat=chat,
                    user=user,
                    activity_repo=activity_repo,
                    chat_settings=chat_settings,
                    actor_label=actor_label,
                    can_manage_games=can_manage_games,
                    economy_repo=economy_repo,
                )
            elif form_action == "spy_set_category":
                if not can_manage_games:
                    success, message = False, "Недостаточно прав для изменения темы."
                else:
                    updated_game, error = await GAME_STORE.set_spy_category(
                        game_id=game.game_id,
                        category=(form.get("spy_category") or "").strip() or None,
                    )
                    if updated_game is None:
                        success, message = False, "Игра не найдена."
                    elif error:
                        success, message = False, error
                    else:
                        await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                        success, message = True, f"Тема: {_spy_category_label(updated_game)}"
            elif form_action == "whoami_set_category":
                if not can_manage_games:
                    success, message = False, "Недостаточно прав для изменения темы."
                else:
                    updated_game, error = await GAME_STORE.set_whoami_category(
                        game_id=game.game_id,
                        category=(form.get("whoami_category") or "").strip() or None,
                        actions_18_enabled=chat_settings.actions_18_enabled,
                    )
                    if updated_game is None:
                        success, message = False, "Игра не найдена."
                    elif error:
                        success, message = False, error
                    else:
                        await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                        success, message = True, f"Тема: {updated_game.whoami_category or 'случайная'}"
            elif form_action == "zlob_set_category":
                if not can_manage_games:
                    success, message = False, "Недостаточно прав для изменения темы."
                else:
                    updated_game, error = await GAME_STORE.set_zlob_category(
                        game_id=game.game_id,
                        category=(form.get("zlob_category") or "").strip() or None,
                        actions_18_enabled=chat_settings.actions_18_enabled,
                    )
                    if updated_game is None:
                        success, message = False, "Игра не найдена."
                    elif error:
                        success, message = False, error
                    else:
                        await game_router_module._safe_edit_or_send_game_board(bot, updated_game, chat_settings)
                        success, message = True, f"Тема: {_zlob_category_label(updated_game)}"
            elif form_action == "number_guess":
                guess_raw = (form.get("guess") or "").strip()
                if not guess_raw or not guess_raw.lstrip("-").isdigit():
                    success, message = False, "Введите целое число."
                else:
                    guess = int(guess_raw)
                    current, result, error = await GAME_STORE.number_register_guess(
                        game_id=game.game_id,
                        user_id=user.telegram_user_id,
                        guess=guess,
                    )
                    if error:
                        success, message = False, error
                    elif current is None or result is None:
                        success, message = False, "Игра не найдена."
                    elif result.direction == "correct":
                        reward_line = await game_router_module._grant_game_rewards_if_needed(
                            current,
                            economy_repo=economy_repo,
                            chat_settings=chat_settings,
                            winner_user_ids_override={result.winner_user_id} if result.winner_user_id is not None else None,
                        )
                        note = f"<b>Финиш:</b> {escape(result.winner_text or 'игра завершена')}"
                        if reward_line:
                            note = f"{note}\n{reward_line}"
                        await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                        success, message = True, "Число угадано, игра завершена."
                    else:
                        direction = "нужно больше" if result.direction == "up" else "нужно меньше"
                        note = (
                            f"<b>Последняя попытка:</b> {escape(actor_label)} -> <code>{result.guess}</code>, {direction}.\n"
                            f"<b>Попыток:</b> {result.attempts_for_user} (лично) / {result.attempts_total} (всего)"
                        )
                        await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                        success, message = True, "Попытка сохранена."
            elif form_action == "spy_guess":
                guessed_location = (form.get("guess_location") or "").strip()
                current, resolution, error = await GAME_STORE.spy_guess_location(
                    game_id=game.game_id,
                    actor_user_id=user.telegram_user_id,
                    guessed_location=guessed_location,
                )
                if error:
                    success, message = False, error
                elif current is None or resolution is None:
                    success, message = False, "Игра не найдена."
                else:
                    reward_line = await game_router_module._grant_game_rewards_if_needed(
                        current,
                        economy_repo=economy_repo,
                        chat_settings=chat_settings,
                    )
                    verdict_text = "угадал" if resolution.guessed_correctly else "ошибся"
                    result_text = (
                        "<b>Шпион называет локацию</b>\n"
                        f"<b>Ответ шпиона:</b> <code>{escape(resolution.guessed_location)}</code>\n"
                        f"<b>Вердикт:</b> {verdict_text}\n"
                        f"<b>Настоящая локация:</b> <code>{escape(resolution.actual_location or '-')}</code>\n"
                        f"<b>Победа:</b> {escape(resolution.winner_text)}"
                    )
                    note = result_text if not reward_line else f"{result_text}\n{reward_line}"
                    await game_router_module._safe_edit_or_send_game_board(
                        bot,
                        current,
                        chat_settings,
                        note=note,
                        include_reveal=True,
                    )
                    await game_router_module._send_game_feed_event(
                        bot,
                        current,
                        text=(
                            f"<b>Ведущий:</b> {result_text}"
                            + (f"\n{reward_line}" if reward_line else "")
                            + f"\n{game_router_module._render_roles_reveal(current)}"
                        ),
                    )
                    success = True
                    if resolution.guessed_correctly:
                        message = "Локация угадана. Шпион победил."
                    else:
                        message = "Локация названа неверно. Мирные победили."
            elif form_action == "whoami_ask":
                question_text = (form.get("question_text") or "").strip()
                current, result, error = await GAME_STORE.whoami_submit_question(
                    game_id=game.game_id,
                    actor_user_id=user.telegram_user_id,
                    question_text=question_text,
                )
                if error:
                    success, message = False, error
                elif current is None or result is None:
                    success, message = False, "Игра не найдена."
                else:
                    note = (
                        f"<b>Вопрос от:</b> {escape(result.actor_user_label or actor_label)}\n"
                        f"<b>Текст:</b> {escape(result.question_text)}"
                    )
                    await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                    success, message = True, "Вопрос отправлен."
            elif form_action == "whoami_guess":
                guess_text = (form.get("guess_text") or "").strip()
                current, resolution, error = await GAME_STORE.whoami_guess_identity(
                    game_id=game.game_id,
                    actor_user_id=user.telegram_user_id,
                    guess_text=guess_text,
                )
                if error:
                    success, message = False, error
                elif current is None or resolution is None:
                    success, message = False, "Игра не найдена."
                else:
                    note = game_router_module._format_whoami_guess_resolution(current, resolution)
                    if resolution.guessed_correctly:
                        reward_line = await game_router_module._grant_game_rewards_if_needed(
                            current,
                            economy_repo=economy_repo,
                            chat_settings=chat_settings,
                        )
                        if reward_line:
                            note = f"{note}\n{reward_line}"
                        await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                        if resolution.finished:
                            await game_router_module._send_game_feed_event(
                                bot,
                                current,
                                text=note + "\n" + game_router_module._render_roles_reveal(current),
                            )
                            success, message = True, "Все карточки разгаданы. Партия завершена."
                        else:
                            success, message = True, "Карточка разгадана. Вы выходите из круга вопросов."
                    else:
                        await game_router_module._safe_edit_or_send_game_board(bot, current, chat_settings, note=note)
                        success, message = True, "Догадка принята. Ход перешёл дальше."
            elif form_action == "bred_submit":
                lie_text = (form.get("lie_text") or "").strip()
                current, result, error = await GAME_STORE.bred_submit_lie(
                    game_id=game.game_id,
                    user_id=user.telegram_user_id,
                    lie_text=lie_text,
                )
                if error:
                    success, message = False, error
                elif current is None or result is None:
                    success, message = False, "Игра не найдена."
                else:
                    if result.vote_opened:
                        await game_router_module._safe_edit_or_send_game_board(
                            bot,
                            current,
                            chat_settings,
                            note="<b>Этап:</b> все ответы получены, открыто голосование.",
                        )
                    else:
                        await game_router_module._safe_edit_or_send_game_board(
                            bot,
                            current,
                            chat_settings,
                            note=f"<b>Ответов в ЛС:</b> {result.submitted_count}/{result.total_players}",
                        )
                    success = True
                    message = "Ответ сохранён."
            elif form_action == "zlob_submit":
                first_index_raw = (form.get("card_index") or "").strip()
                second_index_raw = (form.get("card_index_second") or "").strip()
                if not first_index_raw.isdigit():
                    success, message = False, "Выберите хотя бы одну карточку."
                else:
                    if second_index_raw and not second_index_raw.isdigit():
                        success, message = False, "Некорректный второй выбор."
                    else:
                        indexes = (int(first_index_raw), int(second_index_raw)) if second_index_raw else (int(first_index_raw),)
                        current, result, error = await GAME_STORE.zlob_submit_cards(
                            game_id=game.game_id,
                            user_id=user.telegram_user_id,
                            card_indexes=indexes,
                        )
                        if error:
                            success, message = False, error
                        elif current is None or result is None:
                            success, message = False, "Игра не найдена."
                        else:
                            if result.vote_opened:
                                game_router_module._cancel_phase_timer(current.game_id)
                                await game_router_module._safe_edit_or_send_game_board(
                                    bot,
                                    current,
                                    chat_settings,
                                    note="<b>Этап:</b> все карточки сданы, открыто голосование.",
                                )
                                game_router_module._schedule_phase_timer(bot, current, chat_settings)
                                success, message = True, "Карточки приняты. Открыто голосование."
                            else:
                                await game_router_module._safe_edit_or_send_game_board(
                                    bot,
                                    current,
                                    chat_settings,
                                    note=f"<b>Сдача карточек:</b> {result.submitted_count}/{result.total_players}",
                                )
                                success, message = True, "Карточки отправлены."

            await session.commit()

        key = "flash" if success else "error"
        redirect_path = _with_message("/app/games", key=key, text=message)
        if prefers_json:
            return _json_result(
                ok=success,
                message=message,
                status_code=200 if success else 400,
                redirect=redirect_path,
            )
        return _redirect(redirect_path)

    @app.get("/app/chat/{chat_id}", response_class=HTMLResponse)
    async def chat_page(chat_id: int, request: Request):
        requested_tab = (request.query_params.get("tab") or "overview").strip().lower()
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)

            visible_groups: dict[int, UserChatOverview] = {group.chat_id: group for group in activity_groups}
            for group in admin_groups:
                visible_groups[group.chat_id] = group

            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _render_template(
                    "error.html",
                    response_status_code=403,
                    **_error_context(
                        status_code=403,
                        headline="Нет доступа",
                        message="Эта группа не связана с вашей активностью или ролью бота.",
                        user=user,
                    ),
                )

            defaults = default_chat_settings(settings)
            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or defaults
            role_definition = await activity_repo.get_effective_role_definition(chat_id=chat_id, user_id=user.telegram_user_id)
            can_manage_settings, _, _ = await has_permission(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
                permission="manage_settings",
                bootstrap_if_missing_owner=False,
            )

            summary = await activity_repo.get_chat_activity_summary(chat_id=chat_id)
            stats = await get_my_stats(activity_repo, chat_id=chat_id, user_id=user.telegram_user_id)
            rep_stats = await get_rep_stats(
                activity_repo,
                chat_id=chat_id,
                user_id=user.telegram_user_id,
                limit=max(current_settings.top_limit_max, 50),
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
                days=current_settings.leaderboard_7d_days,
            )
            roles = await activity_repo.list_chat_role_definitions(chat_id=chat_id)
            command_rules = await activity_repo.list_command_access_rules(chat_id=chat_id)
            aliases = await activity_repo.list_chat_aliases(chat_id=chat_id)
            triggers = await activity_repo.list_chat_triggers(chat_id=chat_id)
            audit_entries = await activity_repo.list_audit_logs(chat_id=chat_id, limit=20)
            top_activity = await activity_repo.get_top(chat_id=chat_id, limit=8)
            top_mix = await activity_repo.get_leaderboard(
                chat_id=chat_id,
                mode="mix",
                period="all",
                since=None,
                limit=8,
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
            )
            top_karma = await activity_repo.get_leaderboard(
                chat_id=chat_id,
                mode="karma",
                period="all",
                since=None,
                limit=8,
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
            )
            top_mix_7d = await activity_repo.get_leaderboard(
                chat_id=chat_id,
                mode="mix",
                period="7d",
                since=_now_utc() - timedelta(days=current_settings.leaderboard_7d_days),
                limit=8,
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
            )
            global_dashboard = await _load_dashboard_if_exists(
                economy_repo,
                mode="global",
                chat_id=None,
                user_id=user.telegram_user_id,
            )
            local_dashboard = await _load_dashboard_if_exists(
                economy_repo,
                mode="local",
                chat_id=chat_id,
                user_id=user.telegram_user_id,
            )
            achievement_sections = await _build_achievement_sections(
                activity_repo,
                settings=settings,
                chat_id=chat_id,
                user_id=user.telegram_user_id,
            )
            await session.commit()

        page_context = build_chat_context(
            user=user,
            chat=chat,
            summary=summary,
            stats=stats,
            rep_stats=rep_stats,
            role_definition=role_definition,
            current_settings=current_settings,
            defaults=defaults,
            can_manage_settings=can_manage_settings,
            roles=roles,
            command_rules=command_rules,
            aliases=aliases,
            triggers=triggers,
            audit_entries=audit_entries,
            global_dashboard=global_dashboard,
            local_dashboard=local_dashboard,
            top_activity=top_activity,
            top_mix=top_mix,
            top_karma=top_karma,
            top_mix_7d=top_mix_7d,
            achievement_sections=achievement_sections,
            flash=request.query_params.get("flash"),
            error=request.query_params.get("error"),
        )
        if requested_tab == "settings" and not can_manage_settings:
            requested_tab = "overview"
        page_context["active_tab"] = "settings" if requested_tab == "settings" else "overview"
        page_context.update(
            _chat_layout_context(
                chat_id=chat_id,
                flash=page_context["flash"],
                error=page_context["error"],
            )
        )
        return _render_template("chat.html", **page_context)

    @app.get("/api/chat/{chat_id}/overview")
    async def chat_overview_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            summary = await activity_repo.get_chat_activity_summary(chat_id=chat_id)
            activity_series = await _build_chat_daily_activity_series(session, chat_id=chat_id, days=7)
            hero_candidates = await activity_repo.get_leaderboard(
                chat_id=chat_id,
                mode="activity",
                period="day",
                since=_now_utc() - timedelta(days=1),
                limit=1,
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
            )

            richest_payload: dict[str, object] | None = None
            if current_settings.economy_enabled:
                scope, _ = await economy_repo.resolve_scope(
                    mode=current_settings.economy_mode,
                    chat_id=chat_id,
                    user_id=user.telegram_user_id,
                )
                if scope is not None:
                    richest_payload = await _build_richest_user_payload(session, scope_id=scope.scope_id, chat_id=chat_id)

            await session.commit()

        hero = hero_candidates[0] if hero_candidates else None
        return JSONResponse(
            content={
                "ok": True,
                "summary": {
                    "participants_count": summary.participants_count,
                    "total_messages": summary.total_messages,
                    "last_activity_at": format_datetime(summary.last_activity_at),
                },
                "daily_activity": activity_series,
                "hero_of_day": (
                    {
                        "label": user_label(hero),
                        "messages": hero.activity_value,
                        "karma": hero.karma_value,
                    }
                    if hero is not None
                    else None
                ),
                "richest_of_day": richest_payload,
            }
        )

    @app.get("/api/chat/{chat_id}/leaderboard")
    async def chat_leaderboard_api(chat_id: int, request: Request):
        mode = _chat_hub_mode(request.query_params.get("mode"))
        page_raw = (request.query_params.get("page") or "1").strip()
        query = (request.query_params.get("q") or "").strip()
        find_me = (request.query_params.get("find_me") or "").strip().lower() in {"1", "true", "yes", "on"}
        page = int(page_raw) if page_raw.isdigit() else 1
        page = max(1, page)

        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            leaderboard_items = await activity_repo.get_leaderboard(
                chat_id=chat_id,
                mode=mode,
                period="all",
                since=None,
                limit=_CHAT_HUB_MAX_ROWS,
                karma_weight=current_settings.leaderboard_hybrid_karma_weight,
                activity_weight=current_settings.leaderboard_hybrid_activity_weight,
            )
            await session.commit()

        ranked_items = list(enumerate(leaderboard_items, start=1))
        if query:
            query_norm = normalize_text_command(query)
            ranked_items = [
                (position, item)
                for position, item in ranked_items
                if query_norm in _leaderboard_item_search_text(item)
            ]

        if find_me and ranked_items:
            for index, (_, item) in enumerate(ranked_items):
                if item.user_id == user.telegram_user_id:
                    page = (index // _CHAT_HUB_PAGE_SIZE) + 1
                    break

        total_rows = len(ranked_items)
        total_pages = max(1, (total_rows + _CHAT_HUB_PAGE_SIZE - 1) // _CHAT_HUB_PAGE_SIZE)
        page = min(page, total_pages)
        start_index = (page - 1) * _CHAT_HUB_PAGE_SIZE
        page_rows = ranked_items[start_index:start_index + _CHAT_HUB_PAGE_SIZE]

        my_rank = next(
            (position for position, item in ranked_items if item.user_id == user.telegram_user_id),
            None,
        )
        return JSONResponse(
            content={
                "ok": True,
                "mode": mode,
                "query": query,
                "page": page,
                "page_size": _CHAT_HUB_PAGE_SIZE,
                "total_rows": total_rows,
                "total_pages": total_pages,
                "my_rank": my_rank,
                "truncated": len(leaderboard_items) >= _CHAT_HUB_MAX_ROWS,
                "rows": [
                    _leaderboard_row_payload(
                        position=position,
                        item=item,
                        viewer_user_id=user.telegram_user_id,
                    )
                    for position, item in page_rows
                ],
            }
        )

    @app.get("/app/family/{chat_id}", response_class=HTMLResponse)
    async def family_page(chat_id: int, request: Request, user_id: int | None = None):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups = _merge_visible_groups(admin_groups, activity_groups)
            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _render_template(
                    "error.html",
                    response_status_code=403,
                    **_error_context(
                        status_code=403,
                        headline="Нет доступа",
                        message="Эта группа недоступна для вашего аккаунта.",
                        user=user,
                    ),
                )

            focus_user_id = int(user_id or user.telegram_user_id)
            bundle = await activity_repo.list_family_bundle(chat_id=chat_id, user_id=focus_user_id)
            graph = await activity_repo.list_family_graph(chat_id=chat_id, user_id=focus_user_id)
            role_map: dict[int, str] = {focus_user_id: "subject"}
            for relation_name, user_ids in (
                ("spouse", (bundle.spouse_user_id,) if bundle.spouse_user_id is not None else ()),
                ("parent", bundle.parents),
                ("grandparent", bundle.grandparents),
                ("step_parent", bundle.step_parents),
                ("sibling", bundle.siblings),
                ("child", bundle.children),
                ("pet", bundle.pets),
            ):
                for related_user_id in user_ids:
                    role_map[int(related_user_id)] = relation_name

            nodes: list[dict[str, object]] = []
            for node_user_id in graph.node_user_ids:
                nodes.append(
                    {
                        "id": int(node_user_id),
                        "label": await _resolve_chat_member_label(activity_repo, chat_id=chat_id, user_id=int(node_user_id)),
                        "role": role_map.get(int(node_user_id), "relative"),
                        "href": f"/app/family/{chat_id}?user_id={int(node_user_id)}",
                    }
                )
            edges = [
                {
                    "source": int(edge.source_user_id),
                    "target": int(edge.target_user_id),
                    "label": edge.label,
                    "relation_type": edge.relation_type,
                    "is_direct": edge.is_direct,
                }
                for edge in graph.edges
            ]
            await session.commit()

        return _render_template(
            "family.html",
            page_title=f"Selara Family • {chat.chat_title or chat.chat_id}",
            page_name="family",
            chat_id=chat.chat_id,
            chat_title=chat.chat_title or f"chat:{chat.chat_id}",
            focus_user_id=focus_user_id,
            focus_label=next((node["label"] for node in nodes if node["id"] == focus_user_id), f"user:{focus_user_id}"),
            family_nodes=nodes,
            family_nodes_json=json.dumps(nodes, ensure_ascii=False),
            family_edges_json=json.dumps(edges, ensure_ascii=False),
            bundle_summary=[
                {"label": "Родители", "value": str(len(bundle.parents))},
                {"label": "Супруг(а)", "value": "есть" if bundle.spouse_user_id is not None else "нет"},
                {"label": "Дети", "value": str(len(bundle.children))},
                {"label": "Питомцы", "value": str(len(bundle.pets))},
            ],
            **_chat_layout_context(chat.chat_id, flash=request.query_params.get("flash"), error=request.query_params.get("error")),
        )

    @app.get("/app/chat/{chat_id}/economy", response_class=HTMLResponse)
    async def chat_economy_page(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups = _merge_visible_groups(admin_groups, activity_groups)
            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _render_template(
                    "error.html",
                    response_status_code=403,
                    **_error_context(
                        status_code=403,
                        headline="Нет доступа",
                        message="Эта группа недоступна для вашего аккаунта.",
                        user=user,
                    ),
                )

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            if not current_settings.economy_enabled:
                await session.commit()
                return _render_template(
                    "error.html",
                    response_status_code=403,
                    **_error_context(
                        status_code=403,
                        headline="Экономика отключена",
                        message="Для этой группы веб-экономика сейчас выключена.",
                        user=user,
                    ),
                )

            mode = current_settings.economy_mode
            dashboard, error = await _load_dashboard_if_exists(
                economy_repo,
                mode=mode,
                chat_id=chat_id,
                user_id=user.telegram_user_id,
            )
            scope, scope_error = await economy_repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=user.telegram_user_id)
            listings = [] if scope is None else await economy_repo.list_market_open(scope=scope, limit=100)
            trades = [] if scope is None else await economy_repo.list_market_trades(
                scope=scope,
                since=_now_utc() - timedelta(days=7),
                limit=200,
            )
            await session.commit()

        if dashboard is None or scope is None:
            return _render_template(
                "error.html",
                response_status_code=400,
                **_error_context(
                    status_code=400,
                    headline="Экономика недоступна",
                    message=error or scope_error or "Не удалось открыть экономический кабинет.",
                    user=user,
                ),
            )

        now = _now_utc()
        plots = []
        for plot in dashboard.plots:
            state = "empty"
            note = "Пусто"
            if plot.crop_code is not None:
                if plot.ready_at is not None and plot.ready_at <= now:
                    state = "ready"
                    note = "Готово к сбору"
                elif plot.ready_at is not None:
                    state = "growing"
                    note = f"Созреет: {format_datetime(plot.ready_at)}"
                else:
                    state = "growing"
                    note = "Растёт"
            plots.append(
                {
                    "plot_no": plot.plot_no,
                    "state": state,
                    "crop_code": plot.crop_code,
                    "crop_label": localize_crop_code(plot.crop_code),
                    "note": note,
                }
            )

        inventory_items = [
            {
                "item_code": item.item_code,
                "label": localize_item_code(item.item_code),
                "quantity": item.quantity,
                "target": _economy_inventory_target(item.item_code),
            }
            for item in dashboard.inventory
            if item.quantity > 0
        ]

        trade_points: dict[str, list[dict[str, object]]] = defaultdict(list)
        for trade in reversed(trades):
            trade_points[trade.item_code].append(
                {
                    "when": format_datetime(trade.created_at),
                    "quantity": trade.quantity,
                    "unit_price": trade.unit_price,
                    "total_price": trade.total_price,
                }
            )

        market_rows = []
        for listing in listings:
            market_rows.append(
                {
                    "id": listing.id,
                    "label": localize_item_code(listing.item_code),
                    "item_code": listing.item_code,
                    "qty_left": listing.qty_left,
                    "qty_total": listing.qty_total,
                    "unit_price": listing.unit_price,
                    "seller_label": str(listing.seller_user_id),
                    "filter_group": _market_filter_group(listing.item_code),
                    "is_own": listing.seller_user_id == user.telegram_user_id,
                }
            )

        return _render_template(
            "economy.html",
            page_title=f"Selara Economy • {chat.chat_title or chat.chat_id}",
            page_name="economy",
            chat_id=chat.chat_id,
            chat_title=chat.chat_title or f"chat:{chat.chat_id}",
            scope_id=scope.scope_id,
            economy_mode=mode,
            dashboard=dashboard,
            plot_cards=plots,
            inventory_items=inventory_items,
            market_rows=market_rows,
            market_rows_json=json.dumps(market_rows, ensure_ascii=False),
            trade_points_json=json.dumps(trade_points, ensure_ascii=False),
            last_crop_label=localize_crop_code(dashboard.farm.last_planted_crop_code),
            **_chat_layout_context(chat.chat_id, flash=request.query_params.get("flash"), error=request.query_params.get("error")),
        )

    @app.get("/api/chat/{chat_id}/economy/market")
    async def market_data_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            scope, error = await economy_repo.resolve_scope(mode=current_settings.economy_mode, chat_id=chat_id, user_id=user.telegram_user_id)
            if scope is None:
                await session.commit()
                return _json_result(ok=False, message=error or "Не удалось открыть рынок.", status_code=400)

            listings = await economy_repo.list_market_open(scope=scope, limit=100)
            trades = await economy_repo.list_market_trades(scope=scope, since=_now_utc() - timedelta(days=7), limit=200)
            await session.commit()

        return JSONResponse(
            content={
                "ok": True,
                "listings": [
                    {
                        "id": listing.id,
                        "item_code": listing.item_code,
                        "label": localize_item_code(listing.item_code),
                        "qty_left": listing.qty_left,
                        "qty_total": listing.qty_total,
                        "unit_price": listing.unit_price,
                        "filter_group": _market_filter_group(listing.item_code),
                        "is_own": listing.seller_user_id == user.telegram_user_id,
                    }
                    for listing in listings
                ],
                "trades": [
                    {
                        "item_code": trade.item_code,
                        "when": format_datetime(trade.created_at),
                        "quantity": trade.quantity,
                        "unit_price": trade.unit_price,
                        "total_price": trade.total_price,
                    }
                    for trade in trades
                ],
            }
        )

    @app.post("/api/chat/{chat_id}/economy/apply")
    async def apply_economy_item_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            form = await _parse_form(request)
            item_code = (form.get("item_code") or "").strip().lower()
            target_type = (form.get("target_type") or "").strip().lower()
            plot_no = int(form["plot_no"]) if (form.get("plot_no") or "").isdigit() else None

            if item_code.startswith("seed:") and target_type == "plot-empty" and plot_no is not None:
                result = await plant_crop(
                    economy_repo,
                    economy_mode=current_settings.economy_mode,
                    chat_id=chat_id,
                    user_id=user.telegram_user_id,
                    crop_code=item_code.removeprefix("seed:"),
                    plot_no=plot_no,
                )
                await session.commit()
                if not result.accepted:
                    return _json_result(ok=False, message=result.reason or "Не удалось посадить культуру.", status_code=400)
                await _publish_chat_live_event(chat_id)
                return _json_result(ok=True, message="Культура посажена.", status_code=200)

            if item_code.startswith("item:"):
                if target_type not in {"plot-occupied", "self"}:
                    await session.commit()
                    return _json_result(ok=False, message="Этот предмет нельзя применить к выбранной цели.", status_code=400)
                result = await use_item(
                    economy_repo,
                    economy_mode=current_settings.economy_mode,
                    chat_id=chat_id,
                    user_id=user.telegram_user_id,
                    item_code=item_code,
                    plot_no=plot_no if target_type == "plot-occupied" else None,
                )
                await session.commit()
                if not result.accepted:
                    return _json_result(ok=False, message=result.reason or "Не удалось применить предмет.", status_code=400)
                await _publish_chat_live_event(chat_id)
                return _json_result(ok=True, message=result.details or "Предмет применён.", status_code=200)

            await session.commit()
            return _json_result(ok=False, message="Неподдерживаемое действие.", status_code=400)

    @app.post("/api/chat/{chat_id}/economy/market/create")
    async def create_market_listing_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            form = await _parse_form(request)
            item_code = (form.get("item_code") or "").strip().lower()
            quantity = int(form["quantity"]) if (form.get("quantity") or "").isdigit() else 0
            unit_price = int(form["unit_price"]) if (form.get("unit_price") or "").isdigit() else 0
            result = await market_create_listing(
                economy_repo,
                economy_mode=current_settings.economy_mode,
                chat_id=chat_id,
                user_id=user.telegram_user_id,
                item_code=item_code,
                quantity=quantity,
                unit_price=unit_price,
                market_fee_percent=current_settings.economy_market_fee_percent,
            )
            await session.commit()
            if not result.accepted or result.listing is None:
                return _json_result(ok=False, message=result.reason or "Не удалось создать лот.", status_code=400)
            await _publish_chat_live_event(chat_id)
            return _json_result(ok=True, message=f"Лот #{result.listing.id} создан.", status_code=200)

    @app.post("/api/chat/{chat_id}/economy/market/buy")
    async def buy_market_listing_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            form = await _parse_form(request)
            listing_id = int(form["listing_id"]) if (form.get("listing_id") or "").isdigit() else 0
            quantity = int(form["quantity"]) if (form.get("quantity") or "").isdigit() else 0
            result = await market_buy_listing(
                economy_repo,
                economy_mode=current_settings.economy_mode,
                chat_id=chat_id,
                buyer_user_id=user.telegram_user_id,
                listing_id=listing_id,
                quantity=quantity,
                seller_tax_percent=current_settings.economy_transfer_tax_percent,
            )
            await session.commit()
            if not result.accepted:
                return _json_result(ok=False, message=result.reason or "Не удалось купить лот.", status_code=400)
            await _publish_chat_live_event(chat_id)
            return _json_result(ok=True, message="Покупка выполнена.", status_code=200)

    @app.post("/api/chat/{chat_id}/economy/market/cancel")
    async def cancel_market_listing_api(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _json_result(ok=False, message="Сессия истекла. Войдите снова.", status_code=401, redirect="/login")

            activity_repo = SqlAlchemyActivityRepository(session)
            economy_repo = SqlAlchemyEconomyRepository(session)
            chat = await _ensure_chat_visible_or_none(activity_repo, user_id=user.telegram_user_id, chat_id=chat_id)
            if chat is None:
                await session.commit()
                return _json_result(ok=False, message="Группа недоступна.", status_code=403, redirect="/app")

            form = await _parse_form(request)
            listing_id = int(form["listing_id"]) if (form.get("listing_id") or "").isdigit() else 0
            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or chat_settings_defaults
            result = await market_cancel_listing(
                economy_repo,
                economy_mode=current_settings.economy_mode,
                chat_id=chat_id,
                seller_user_id=user.telegram_user_id,
                listing_id=listing_id,
            )
            await session.commit()
            if not result.accepted:
                return _json_result(ok=False, message=result.reason or "Не удалось снять лот.", status_code=400)
            await _publish_chat_live_event(chat_id)
            return _json_result(ok=True, message="Лот снят с рынка.", status_code=200)

    @app.get("/app/docs/admin", response_class=HTMLResponse)
    async def admin_docs_page(request: Request, chat_id: int | None = None):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            chat: UserChatOverview | None = None
            if chat_id is not None:
                activity_repo = SqlAlchemyActivityRepository(session)
                admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
                visible_groups = _merge_visible_groups(admin_groups, activity_groups)
                chat = visible_groups.get(chat_id)
            await session.commit()

        page_context = build_admin_docs_context(chat=chat)
        page_context["flash"] = request.query_params.get("flash")
        page_context["error"] = request.query_params.get("error")
        page_context.update(
            _docs_layout_context(
                chat_id=chat.chat_id if chat is not None else None,
                flash=page_context["flash"],
                error=page_context["error"],
            )
        )
        return _render_template("admin_docs.html", **page_context)

    @app.get("/app/docs/user", response_class=HTMLResponse)
    async def user_docs_page(request: Request, chat_id: int | None = None):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            chat: UserChatOverview | None = None
            if chat_id is not None:
                activity_repo = SqlAlchemyActivityRepository(session)
                admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
                visible_groups = _merge_visible_groups(admin_groups, activity_groups)
                chat = visible_groups.get(chat_id)
            await session.commit()

        page_context = build_user_docs_context(chat=chat)
        page_context["flash"] = request.query_params.get("flash")
        page_context["error"] = request.query_params.get("error")
        page_context.update(
            _user_docs_layout_context(
                chat_id=chat.chat_id if chat is not None else None,
                flash=page_context["flash"],
                error=page_context["error"],
            )
        )
        return _render_template("user_docs.html", **page_context)

    @app.get("/app/chat/{chat_id}/audit", response_class=HTMLResponse)
    async def chat_audit_page(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups = _merge_visible_groups(admin_groups, activity_groups)
            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _render_template(
                    "error.html",
                    response_status_code=403,
                    **_error_context(
                        status_code=403,
                        headline="Нет доступа",
                        message="Эта группа недоступна для вашего аккаунта.",
                        user=user,
                    ),
                )

            entries = await activity_repo.list_audit_logs(chat_id=chat_id, limit=200)
            await session.commit()

        return _render_template(
            "audit.html",
            page_title=f"Selara Audit • {chat.chat_title or chat.chat_id}",
            page_name="audit",
            chat_id=chat.chat_id,
            chat_title=chat.chat_title or f"chat:{chat.chat_id}",
            audit_rows=[
                {
                    "when": format_datetime(entry.created_at),
                    "action": entry.action_code,
                    "description": entry.description,
                    "actor": str(entry.actor_user_id) if entry.actor_user_id is not None else "system",
                    "target": str(entry.target_user_id) if entry.target_user_id is not None else "—",
                }
                for entry in entries
            ],
            **_audit_layout_context(chat_id, flash=request.query_params.get("flash"), error=request.query_params.get("error")),
        )

    @app.post("/app/chat/{chat_id}/triggers")
    async def update_chat_trigger(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups = _merge_visible_groups(admin_groups, activity_groups)
            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _redirect(_with_message("/app", key="error", text="Группа недоступна."))

            allowed, _, _ = await has_permission(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
                permission="manage_settings",
                bootstrap_if_missing_owner=False,
            )
            if not allowed:
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text="Недостаточно прав."))

            form = await _parse_form(request)
            action = (form.get("action") or "save").strip().lower()
            trigger_id = int(form["trigger_id"]) if (form.get("trigger_id") or "").isdigit() else None
            if action == "delete":
                if trigger_id is None or not await activity_repo.remove_chat_trigger(chat_id=chat_id, trigger_id=trigger_id):
                    await session.commit()
                    return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text="Триггер не найден."))
                await log_chat_action(
                    activity_repo,
                    chat_id=chat_id,
                    chat_type=chat.chat_type,
                    chat_title=chat.chat_title,
                    action_code="web_trigger_deleted",
                    description=f"Через веб удалён smart-trigger #{trigger_id}.",
                    actor_user_id=user.telegram_user_id,
                )
                invalidate_chat_feature_cache(chat_id)
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="flash", text="Триггер удалён."))

            try:
                await activity_repo.upsert_chat_trigger(
                    chat=ChatSnapshot(telegram_chat_id=chat_id, chat_type=chat.chat_type, title=chat.chat_title),
                    trigger_id=trigger_id,
                    keyword=form.get("keyword") or "",
                    match_type=(form.get("match_type") or "contains").strip().lower(),
                    response_text=form.get("response_text"),
                    media_file_id=form.get("media_file_id"),
                    media_type=form.get("media_type"),
                    actor_user_id=user.telegram_user_id,
                )
            except ValueError as exc:
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text=str(exc)))

            await log_chat_action(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                action_code="web_trigger_saved",
                description=f"Через веб сохранён smart-trigger «{(form.get('keyword') or '').strip()}».",
                actor_user_id=user.telegram_user_id,
            )
            invalidate_chat_feature_cache(chat_id)
            await session.commit()
            return _redirect(_with_message(f"/app/chat/{chat_id}", key="flash", text="Триггер сохранён."))

    @app.post("/app/chat/{chat_id}/aliases")
    async def update_chat_alias(chat_id: int, request: Request):
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                return _redirect(_with_message("/login", key="error", text="Сессия истекла. Войдите снова."))

            activity_repo = SqlAlchemyActivityRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups = _merge_visible_groups(admin_groups, activity_groups)
            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                return _redirect(_with_message("/app", key="error", text="Группа недоступна."))

            allowed, _, _ = await has_permission(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
                permission="manage_settings",
                bootstrap_if_missing_owner=False,
            )
            if not allowed:
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text="Недостаточно прав."))

            form = await _parse_form(request)
            action = (form.get("action") or "save").strip().lower()
            alias_norm = normalize_text_command(form.get("alias_text") or "")
            if action == "delete":
                removed = await activity_repo.remove_chat_alias(chat_id=chat_id, alias_text_norm=alias_norm)
                if removed:
                    await log_chat_action(
                        activity_repo,
                        chat_id=chat_id,
                        chat_type=chat.chat_type,
                        chat_title=chat.chat_title,
                        action_code="web_alias_deleted",
                        description=f"Через веб удалён алиас «{alias_norm}».",
                        actor_user_id=user.telegram_user_id,
                    )
                await session.commit()
                key = "flash" if removed else "error"
                text = "Алиас удалён." if removed else "Алиас не найден."
                return _redirect(_with_message(f"/app/chat/{chat_id}", key=key, text=text))

            source_raw = form.get("source_trigger") or ""
            command_key = resolve_builtin_command_key(source_raw)
            source_norm = normalize_text_command(source_raw)
            if command_key is None or not alias_norm:
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text="Некорректный source или alias."))

            try:
                await activity_repo.upsert_chat_alias(
                    chat=ChatSnapshot(telegram_chat_id=chat_id, chat_type=chat.chat_type, title=chat.chat_title),
                    command_key=command_key,
                    source_trigger_norm=source_norm,
                    alias_text_norm=alias_norm,
                    actor_user_id=user.telegram_user_id,
                    force=True,
                )
            except ValueError as exc:
                await session.commit()
                return _redirect(_with_message(f"/app/chat/{chat_id}", key="error", text=str(exc)))

            await log_chat_action(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                action_code="web_alias_saved",
                description=f"Через веб сохранён алиас «{alias_norm}» для команды «{command_key}».",
                actor_user_id=user.telegram_user_id,
            )
            await session.commit()
            return _redirect(_with_message(f"/app/chat/{chat_id}", key="flash", text="Алиас сохранён."))

    @app.post("/app/chat/{chat_id}/settings")
    async def update_chat_setting(chat_id: int, request: Request):
        prefers_json = _prefers_json(request)
        async with session_factory() as session:
            user = await _load_user_from_request(session, request, touch=True)
            if user is None:
                await session.commit()
                redirect_path = _with_message("/login", key="error", text="Сессия истекла. Войдите снова.")
                if prefers_json:
                    return _json_result(
                        ok=False,
                        message="Сессия истекла. Войдите снова.",
                        status_code=401,
                        redirect=redirect_path,
                    )
                return _redirect(redirect_path)

            activity_repo = SqlAlchemyActivityRepository(session)
            admin_groups, activity_groups = await _collect_visible_groups(activity_repo, user_id=user.telegram_user_id)
            visible_groups: dict[int, UserChatOverview] = {group.chat_id: group for group in activity_groups}
            for group in admin_groups:
                visible_groups[group.chat_id] = group

            chat = visible_groups.get(chat_id)
            if chat is None:
                await session.commit()
                redirect_path = _with_message("/app", key="error", text="Группа недоступна для вашего аккаунта.")
                if prefers_json:
                    return _json_result(
                        ok=False,
                        message="Группа недоступна для вашего аккаунта.",
                        status_code=404,
                        redirect=redirect_path,
                    )
                return _redirect(redirect_path)

            allowed, _, _ = await has_permission(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
                permission="manage_settings",
                bootstrap_if_missing_owner=False,
            )
            if not allowed:
                await session.commit()
                redirect_path = _with_message(
                    f"/app/chat/{chat_id}",
                    key="error",
                    text="У вас нет права manage_settings для этой группы.",
                )
                if prefers_json:
                    return _json_result(
                        ok=False,
                        message="У вас нет права manage_settings для этой группы.",
                        status_code=403,
                        redirect=redirect_path,
                    )
                return _redirect(redirect_path)

            form = await _parse_form(request)
            key = (form.get("key") or "").strip()
            raw_value = form.get("value") or ""
            defaults = default_chat_settings(settings)
            current_settings = await activity_repo.get_chat_settings(chat_id=chat_id) or defaults
            current_map = settings_to_dict(current_settings)
            default_map = settings_to_dict(defaults)
            updated, error = apply_setting_update(
                key=key,
                raw_value=raw_value,
                current=current_map,
                defaults=default_map,
            )
            if error is not None or updated is None:
                await session.commit()
                message = error or "Не удалось обновить настройку."
                redirect_path = _with_message(
                    f"/app/chat/{chat_id}",
                    key="error",
                    text=message,
                )
                if prefers_json:
                    return _json_result(ok=False, message=message, status_code=400, redirect=redirect_path)
                return _redirect(redirect_path)

            await activity_repo.upsert_chat_settings(
                chat=ChatSnapshot(
                    telegram_chat_id=chat_id,
                    chat_type=chat.chat_type,
                    title=chat.chat_title,
                ),
                values=updated,
            )
            await log_chat_action(
                activity_repo,
                chat_id=chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.chat_title,
                action_code="web_setting_updated",
                description=(
                    f"Через веб обновлена настройка «{setting_title_ru(key)}»: "
                    f"{current_map.get(key, default_map.get(key, ''))} -> {updated.get(key, default_map.get(key, ''))}"
                ),
                actor_user_id=user.telegram_user_id,
            )
            await session.commit()
            setting_title = setting_title_ru(key)
            message = f"Настройка «{setting_title}» обновлена."
            if prefers_json:
                return _json_result(
                    ok=True,
                    message=message,
                    status_code=200,
                    setting={
                        "key": key,
                        "title": setting_title,
                        "current_value": str(updated.get(key, default_map.get(key, ""))),
                        "default_value": str(default_map.get(key, "")),
                    },
                )
            return _redirect(
                _with_message(
                    f"/app/chat/{chat_id}",
                    key="flash",
                    text=message,
                )
            )

    return app


def _now_utc() -> datetime:
    return datetime.now(_UTC)
