from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.llm import LlmClient
from selara.infrastructure.stt import SttClient
from selara.presentation.handlers.autoconfig import router as autoconfig_router
from selara.presentation.handlers.aliases import router as aliases_router
from selara.presentation.handlers.admin_broadcasts import router as admin_broadcasts_router
from selara.presentation.handlers.chat_assistant import router as chat_assistant_router
from selara.presentation.handlers.clans import router as clans_router
from selara.presentation.handlers.daily_summary import router as daily_summary_router
from selara.presentation.handlers.economy import router as economy_router
from selara.presentation.handlers.engagement import router as engagement_router
from selara.presentation.handlers.feedback import router as feedback_router
from selara.presentation.handlers.game import router as game_router
from selara.presentation.handlers.help import router as help_router
from selara.presentation.handlers.llm_admin import router as llm_admin_router
from selara.presentation.handlers.message_archive import (
    router as message_archive_router,
)
from selara.presentation.handlers.moderation import router as moderation_router
from selara.presentation.handlers.personal_ai import router as personal_ai_router
from selara.presentation.handlers.private_panel import router as private_panel_router
from selara.presentation.handlers.premium import (
    build_payment_router,
    router as premium_router,
)
from selara.presentation.handlers.relationships import router as relationships_router
from selara.presentation.handlers.settings import router as settings_router
from selara.presentation.handlers.stats import router as stats_router
from selara.presentation.handlers.text_commands import router as text_commands_router
from selara.presentation.handlers.voice import router as voice_router
from selara.presentation.middlewares.activity_tracker import ActivityTrackerMiddleware
from selara.presentation.middlewares.bot_ban import BotBanMiddleware
from selara.presentation.middlewares.chat_migration import ChatMigrationMiddleware
from selara.presentation.middlewares.chat_settings import ChatSettingsMiddleware
from selara.presentation.middlewares.chat_write_lock import ChatWriteLockMiddleware
from selara.presentation.middlewares.command_access import CommandAccessMiddleware
from selara.presentation.middlewares.command_cleanup import CommandCleanupMiddleware
from selara.presentation.middlewares.db_session import DBSessionMiddleware
from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware


def build_router(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    activity_batcher: ActivityBatcher,
    stt_client: SttClient | None = None,
    llm_client: LlmClient | None = None,
) -> Router:
    root = Router(name="root")
    application = Router(name="application")

    application.message.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.message.outer_middleware(DBSessionMiddleware(session_factory))
    application.message.outer_middleware(ChatMigrationMiddleware())
    application.message.outer_middleware(BotBanMiddleware())
    application.message.outer_middleware(ChatSettingsMiddleware())
    application.message.outer_middleware(ChatWriteLockMiddleware())
    application.message.outer_middleware(CommandCleanupMiddleware())
    application.message.outer_middleware(CommandAccessMiddleware())
    application.message.outer_middleware(ActivityTrackerMiddleware(activity_batcher))

    application.edited_message.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.edited_message.outer_middleware(DBSessionMiddleware(session_factory))
    application.edited_message.outer_middleware(ChatSettingsMiddleware())
    application.edited_message.outer_middleware(ActivityTrackerMiddleware(activity_batcher))

    application.callback_query.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.callback_query.outer_middleware(DBSessionMiddleware(session_factory))
    application.callback_query.outer_middleware(BotBanMiddleware())
    application.callback_query.outer_middleware(ChatSettingsMiddleware())
    application.callback_query.outer_middleware(ChatWriteLockMiddleware())

    application.message_reaction.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.message_reaction.outer_middleware(DBSessionMiddleware(session_factory))

    application.message_reaction_count.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.message_reaction_count.outer_middleware(DBSessionMiddleware(session_factory))

    application.inline_query.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.inline_query.outer_middleware(DBSessionMiddleware(session_factory))

    application.chosen_inline_result.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.chosen_inline_result.outer_middleware(DBSessionMiddleware(session_factory))

    application.chat_member.outer_middleware(ErrorHandlerMiddleware(session_factory))
    application.chat_member.outer_middleware(DBSessionMiddleware(session_factory))

    # Payment updates bypass general catch-all handlers and their middleware.
    # Their handlers create their own DB sessions and keep polling blocked until
    # confirmed payments have a durable economic effect.
    root.include_router(build_payment_router())

    application.include_router(autoconfig_router)
    application.include_router(admin_broadcasts_router)
    application.include_router(message_archive_router)
    application.include_router(help_router)
    application.include_router(stats_router)
    application.include_router(chat_assistant_router)
    application.include_router(economy_router)
    application.include_router(game_router)
    application.include_router(clans_router)
    application.include_router(relationships_router)
    application.include_router(moderation_router)
    application.include_router(settings_router)
    application.include_router(aliases_router)
    application.include_router(engagement_router)
    application.include_router(feedback_router)
    application.include_router(premium_router)
    application.include_router(private_panel_router)
    # After the private panel and autoconfig (their pending inputs win), before the text-command catch-all.
    application.include_router(personal_ai_router)
    if llm_client is not None:
        application.include_router(llm_admin_router)
        application.include_router(daily_summary_router)
    application.include_router(text_commands_router)
    if stt_client is not None:
        application.include_router(voice_router)

    root.include_router(application)

    return root
