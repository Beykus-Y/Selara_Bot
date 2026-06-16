import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from selara.application.achievements import get_achievement_catalog_from_settings
from selara.core.config import get_settings
from selara.core.logging import configure_logging
from selara.infrastructure.backup import run_daily_backup_scheduler
from selara.infrastructure.stt import SttClient, SttConfig
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.activity_event_sync import run_message_event_backfill
from selara.infrastructure.db.session import create_engine, create_session_factory
from selara.presentation.game_state import GAME_STORE
from selara.presentation.handlers.game.router import restore_phase_timers
from selara.presentation.interesting_facts import run_interesting_facts_scheduler
from selara.presentation.routers import build_router

logger = logging.getLogger(__name__)


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="help", description="Справка"),
        BotCommand(command="top", description="Интерактивный топ (гибрид/актив/карма)"),
        BotCommand(command="active", description="Топ по активности"),
        BotCommand(command="game", description="Выбрать и запустить игру в чате"),
        BotCommand(command="role", description="Показать мою роль в игре (ЛС)"),
        BotCommand(command="relation", description="Статус отношений и брака"),
        BotCommand(command="pair", description="Предложить отношения (пара)"),
        BotCommand(command="marry", description="Сделать предложение брака"),
        BotCommand(command="breakup", description="Расстаться (пара)"),
        BotCommand(command="divorce", description="Развод"),
        BotCommand(command="love", description="Поднять уровень любви в браке"),
        BotCommand(command="care", description="Забота о партнёре"),
        BotCommand(command="date", description="Свидание с партнёром"),
        BotCommand(command="gift", description="Подарок партнёру"),
        BotCommand(command="support", description="Поддержать партнёра"),
        BotCommand(command="flirt", description="Флирт (только для пары)"),
        BotCommand(command="surprise", description="Сюрприз (только для пары)"),
        BotCommand(command="vow", description="Семейная клятва (только для брака)"),
    ]


def _build_stt_client(settings) -> SttClient | None:
    if not settings.stt_enabled:
        return None
    if not settings.stt_api_key.strip():
        logger.warning("STT включён (STT_ENABLED=true), но STT_API_KEY не задан — распознавание отключено.")
        return None
    try:
        config = SttConfig(
            api_key=settings.stt_api_key,
            model=settings.stt_model,
            base_url=settings.stt_base_url or None,
            timeout_seconds=settings.stt_timeout_seconds,
            language=settings.stt_language,
        )
        return SttClient(config)
    except ValueError as exc:
        logger.warning("STT: неверная конфигурация (%s) — распознавание отключено.", exc)
        return None


async def _run_bot(settings, session_factory) -> None:
    bot = Bot(token=settings.bot_token)
    achievement_catalog = get_achievement_catalog_from_settings(settings)
    activity_batcher = ActivityBatcher(
        session_factory=session_factory,
        catalog=achievement_catalog,
        flush_seconds=settings.activity_batch_flush_seconds,
        max_events=settings.activity_batch_max_events,
        live_event_publisher=GAME_STORE.publish_event,
    )
    stt_client = _build_stt_client(settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(session_factory, activity_batcher=activity_batcher, stt_client=stt_client))

    await bot.set_my_commands(build_bot_commands())
    if settings.web_enabled:
        miniapp_url = f"{settings.resolved_web_base_url}/miniapp/"
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Mini App",
                    web_app=WebAppInfo(url=miniapp_url),
                )
            )
        except Exception:
            logger.exception("Failed to configure Mini App menu button", extra={"miniapp_url": miniapp_url})
    await activity_batcher.start()
    await restore_phase_timers(bot, session_factory)
    backup_task = None
    interesting_facts_task = asyncio.create_task(
        run_interesting_facts_scheduler(bot=bot, session_factory=session_factory),
        name="interesting-facts",
    )
    if settings.admin_user_id is not None:
        backup_task = asyncio.create_task(run_daily_backup_scheduler(bot=bot, settings=settings), name="daily-backup")
    else:
        logger.warning("Daily backup scheduler is disabled because ADMIN_USER_ID is not configured.")

    polling_kwargs: dict = {"settings": settings}
    if stt_client is not None:
        polling_kwargs["stt_client"] = stt_client

    try:
        await dispatcher.start_polling(bot, **polling_kwargs)
    finally:
        interesting_facts_task.cancel()
        await asyncio.gather(interesting_facts_task, return_exceptions=True)
        if backup_task is not None:
            backup_task.cancel()
            await asyncio.gather(backup_task, return_exceptions=True)
        await activity_batcher.close()
        await bot.session.close()


async def _run_web_panel(settings, session_factory) -> None:
    if not settings.web_enabled:
        return

    try:
        import uvicorn

        from selara.web.app import create_web_app
    except ModuleNotFoundError as exc:
        logger.warning("Web panel disabled because dependencies are missing: %s", exc)
        return

    app = create_web_app(settings=settings, session_factory=session_factory)
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info("Starting web panel on %s", settings.resolved_web_base_url)
    try:
        await server.serve()
    except SystemExit as exc:
        if exc.code == 1:
            logger.error(
                "Web panel did not start on %s:%s. The port is likely already in use. "
                "Bot polling will continue without the web panel.",
                settings.web_host,
                settings.web_port,
            )
            return
        raise


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    get_achievement_catalog_from_settings(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    GAME_STORE.configure_runtime(redis_url=settings.redis_url, ttl_hours=settings.game_state_ttl_hours)

    from selara.presentation.renderer_service import PlaywrightRendererService
    renderer_service = PlaywrightRendererService.get_instance()
    await renderer_service.start()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(run_message_event_backfill(session_factory))
            tg.create_task(_run_bot(settings, session_factory))
            tg.create_task(_run_web_panel(settings, session_factory))
    finally:
        await renderer_service.stop()
        await GAME_STORE.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
