import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from selara.application.achievements import get_achievement_catalog_from_settings
from selara.core.config import get_settings
from selara.core.logging import configure_logging
from selara.infrastructure.db.activity_event_sync import run_message_event_backfill
from selara.infrastructure.db.session import create_engine, create_session_factory
from selara.presentation.game_state import GAME_STORE
from selara.presentation.routers import build_router

logger = logging.getLogger(__name__)


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Открыть ЛС-панель"),
        BotCommand(command="login", description="Код для входа в веб-панель"),
        BotCommand(command="me", description="Моя статистика в чате"),
        BotCommand(command="iris_perenos", description="Перенос профиля из Iris в текущем чате"),
        BotCommand(command="achievements", description="Мои достижения в текущем чате"),
        BotCommand(command="rep", description="Мой профиль репутации"),
        BotCommand(command="top", description="Интерактивный топ (гибрид/актив/карма)"),
        BotCommand(command="active", description="Топ по активности"),
        BotCommand(command="game", description="Выбрать и запустить игру в чате"),
        BotCommand(command="role", description="Показать мою роль в игре (ЛС)"),
        BotCommand(command="naming", description="Задать имя, как бот зовёт вас в этом чате"),
        BotCommand(command="relation", description="Статус отношений и брака"),
        BotCommand(command="pair", description="Предложить отношения (пара)"),
        BotCommand(command="marry", description="Сделать предложение брака"),
        BotCommand(command="breakup", description="Расстаться (пара)"),
        BotCommand(command="love", description="Поднять уровень любви в браке"),
        BotCommand(command="care", description="Забота о партнёре"),
        BotCommand(command="date", description="Свидание с партнёром"),
        BotCommand(command="gift", description="Подарок партнёру"),
        BotCommand(command="support", description="Поддержать партнёра"),
        BotCommand(command="flirt", description="Флирт (только для пары)"),
        BotCommand(command="surprise", description="Сюрприз (только для пары)"),
        BotCommand(command="vow", description="Семейная клятва (только для брака)"),
        BotCommand(command="divorce", description="Развод"),
        BotCommand(command="eco", description="Панель экономики"),
        BotCommand(command="farm", description="Ферма: посадка и сбор урожая"),
        BotCommand(command="shop", description="Магазин предметов"),
        BotCommand(command="inventory", description="Инвентарь и ресурсы"),
        BotCommand(command="craft", description="Крафт предметов из ресурсов"),
        BotCommand(command="tap", description="Кликер с кулдауном"),
        BotCommand(command="daily", description="Ежедневная награда"),
        BotCommand(command="article", description="Моя статья дня"),
        BotCommand(command="growth", description="Механика роста (профиль/действие)"),
        BotCommand(command="lottery", description="Лотерея"),
        BotCommand(command="market", description="Рынок игроков"),
        BotCommand(command="pay", description="Перевод монет игроку"),
        BotCommand(command="auction", description="Live-аукцион в чате"),
        BotCommand(command="bid", description="Сделать ставку на активный аукцион"),
        BotCommand(command="roles", description="Роли бота в этом чате"),
        BotCommand(command="roleadd", description="Выдать роль бота пользователю"),
        BotCommand(command="roleremove", description="Снять роль бота у пользователя"),
        BotCommand(command="roledefs", description="Список ролей и прав в этом чате"),
        BotCommand(command="roletemplates", description="Системные шаблоны ролей"),
        BotCommand(command="rolecreate", description="Создать кастомную роль из шаблона"),
        BotCommand(command="rolesettitle", description="Переименовать кастомную роль"),
        BotCommand(command="rolesetrank", description="Изменить ранг кастомной роли"),
        BotCommand(command="roleperms", description="Редактировать права кастомной роли"),
        BotCommand(command="roledelete", description="Удалить кастомную роль"),
        BotCommand(command="pred", description="Выдать пред (3 преда = 1 варн)"),
        BotCommand(command="warn", description="Выдать варн (3 варна = 1 бан)"),
        BotCommand(command="unwarn", description="Снять один варн"),
        BotCommand(command="ban", description="Внутренний бан пользователя"),
        BotCommand(command="unban", description="Снять внутренний бан"),
        BotCommand(command="modstat", description="Статус предов/варнов/бана"),
        BotCommand(command="settings", description="Настройки бота для текущей группы"),
        BotCommand(command="setcfg", description="Изменить настройку группы"),
        BotCommand(command="setrank", description="Установить минимальный ранг для команды"),
        BotCommand(command="ranks", description="Показать настроенные ранги команд"),
        BotCommand(command="setalias", description='Задать кастомный алиас: /setalias "стандартный" "новый"'),
        BotCommand(command="aliases", description="Список кастомных алиасов и текущий режим"),
        BotCommand(command="unalias", description='Удалить алиас: /unalias "алиас"'),
        BotCommand(command="aliasmode", description="Режим алиасов: aliases_if_exists|both|standard_only"),
        BotCommand(command="settrigger", description='Добавить автоответ: /settrigger "ключ" "ответ"'),
        BotCommand(command="triggers", description="Список смарт-триггеров группы"),
        BotCommand(command="triggervars", description="Список {переменных} для триггеров и RP"),
        BotCommand(command="deltrigger", description="Удалить смарт-триггер"),
        BotCommand(command="rpadd", description='Добавить RP-действие: /rpadd "куснуть" "..."'),
        BotCommand(command="rps", description="Список кастомных RP-действий"),
        BotCommand(command="rpdel", description="Удалить кастомное RP-действие"),
        BotCommand(command="title", description="Купить или сменить титул"),
        BotCommand(command="adopt", description="Запрос на усыновление"),
        BotCommand(command="pet", description="Стать питомцем пользователя"),
        BotCommand(command="family", description="Показать семейное древо"),
        BotCommand(command="lastseen", description="Когда пользователь был активен"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="achsync", description="Пересчитать achievement stats"),
    ]


async def _run_bot(settings, session_factory) -> None:
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(session_factory))

    await bot.set_my_commands(build_bot_commands())

    try:
        await dispatcher.start_polling(bot, settings=settings)
    finally:
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

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(run_message_event_backfill(session_factory))
            tg.create_task(_run_bot(settings, session_factory))
            tg.create_task(_run_web_panel(settings, session_factory))
    finally:
        await GAME_STORE.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
