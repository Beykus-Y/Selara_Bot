from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.presentation.handlers.engagement import router as engagement_router
from selara.presentation.handlers.economy import router as economy_router
from selara.presentation.handlers.game import router as game_router
from selara.presentation.handlers.help import router as help_router
from selara.presentation.handlers.aliases import router as aliases_router
from selara.presentation.handlers.chat_assistant import router as chat_assistant_router
from selara.presentation.handlers.message_archive import router as message_archive_router
from selara.presentation.handlers.moderation import router as moderation_router
from selara.presentation.handlers.private_panel import router as private_panel_router
from selara.presentation.handlers.relationships import router as relationships_router
from selara.presentation.handlers.settings import router as settings_router
from selara.presentation.handlers.stats import router as stats_router
from selara.presentation.handlers.text_commands import router as text_commands_router
from selara.presentation.middlewares.activity_tracker import ActivityTrackerMiddleware
from selara.presentation.middlewares.bot_ban import BotBanMiddleware
from selara.presentation.middlewares.chat_migration import ChatMigrationMiddleware
from selara.presentation.middlewares.command_cleanup import CommandCleanupMiddleware
from selara.presentation.middlewares.chat_settings import ChatSettingsMiddleware
from selara.presentation.middlewares.command_access import CommandAccessMiddleware
from selara.presentation.middlewares.db_session import DBSessionMiddleware
from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware


def build_router(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    activity_batcher: ActivityBatcher,
) -> Router:
    root = Router(name="root")

    root.message.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.message.outer_middleware(DBSessionMiddleware(session_factory))
    root.message.outer_middleware(ChatMigrationMiddleware())
    root.message.outer_middleware(BotBanMiddleware())
    root.message.outer_middleware(ChatSettingsMiddleware())
    root.message.outer_middleware(CommandCleanupMiddleware())
    root.message.outer_middleware(CommandAccessMiddleware())
    root.message.outer_middleware(ActivityTrackerMiddleware(activity_batcher))

    root.edited_message.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.edited_message.outer_middleware(DBSessionMiddleware(session_factory))
    root.edited_message.outer_middleware(ChatSettingsMiddleware())
    root.edited_message.outer_middleware(ActivityTrackerMiddleware(activity_batcher))

    root.callback_query.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.callback_query.outer_middleware(DBSessionMiddleware(session_factory))
    root.callback_query.outer_middleware(BotBanMiddleware())
    root.callback_query.outer_middleware(ChatSettingsMiddleware())

    root.inline_query.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.inline_query.outer_middleware(DBSessionMiddleware(session_factory))

    root.chosen_inline_result.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.chosen_inline_result.outer_middleware(DBSessionMiddleware(session_factory))

    root.chat_member.outer_middleware(ErrorHandlerMiddleware(session_factory))
    root.chat_member.outer_middleware(DBSessionMiddleware(session_factory))

    root.include_router(message_archive_router)
    root.include_router(help_router)
    root.include_router(stats_router)
    root.include_router(chat_assistant_router)
    root.include_router(economy_router)
    root.include_router(game_router)
    root.include_router(relationships_router)
    root.include_router(moderation_router)
    root.include_router(settings_router)
    root.include_router(aliases_router)
    root.include_router(engagement_router)
    root.include_router(private_panel_router)
    root.include_router(text_commands_router)

    return root
