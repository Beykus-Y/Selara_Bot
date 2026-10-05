"""Regression tests for finding #36 in docs/STT_LLM_AUDIT_TODO.md:

None of LlmContextMessageModel/LlmContextSummaryModel/LlmAdminActionModel/
LlmChatGlossaryModel were migrated by chat_migration.py on a group ->
supergroup upgrade, unlike essentially every other chat-scoped table. All
prior LLM context/glossary/audit history became silently inaccessible under
the new chat_id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db import chat_migration
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import ChatMigrationResult, migrate_chat_id
from selara.infrastructure.db.models import (
    AdminRuntimeSettingsModel,
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatMemberCountSnapshotModel,
    ChatModel,
    EconomyPrivateContextModel,
    LlmAdminActionModel,
    LlmChatGlossaryModel,
    LlmContextMessageModel,
    LlmContextSummaryModel,
    DailySummaryRunModel,
    LlmUsageLogModel,
    MessageArchiveModel,
    MarriageModel,
    PairModel,
    RelationshipProposalModel,
    UserModel,
    UserKarmaVoteModel,
)
from selara.presentation.middlewares.error_alert_config import configure_error_alerts, get_error_alert_config
from selara.presentation import chat_migration as presentation_chat_migration


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_llm_context_messages_and_summaries_and_actions_and_glossary():
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id, admin_id = 501, 1501, 601
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(UserModel(telegram_user_id=admin_id, username="admin", is_bot=False))
            await session.commit()

            session.add(LlmContextMessageModel(
                chat_id=old_chat_id, role="user", content="привет", admin_user_id=admin_id,
            ))
            session.add(LlmContextSummaryModel(
                chat_id=old_chat_id, content="summary", period_start=datetime.now(timezone.utc),
                period_end=datetime.now(timezone.utc), messages_count=5, level=1,
            ))
            session.add(LlmAdminActionModel(
                chat_id=old_chat_id, admin_user_id=admin_id, tool_name="warn_user",
                action_description="Варн X", undo_payload_json=None,
            ))
            session.add(LlmChatGlossaryModel(chat_id=old_chat_id, term="рест", definition="отпуск от нормы"))
            await session.commit()

            result = await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()
            assert result.migrated is True

        async with session_factory() as session:
            from sqlalchemy import select

            messages = (await session.execute(
                select(LlmContextMessageModel).where(LlmContextMessageModel.chat_id == new_chat_id)
            )).scalars().all()
            summaries = (await session.execute(
                select(LlmContextSummaryModel).where(LlmContextSummaryModel.chat_id == new_chat_id)
            )).scalars().all()
            actions = (await session.execute(
                select(LlmAdminActionModel).where(LlmAdminActionModel.chat_id == new_chat_id)
            )).scalars().all()
            glossary = (await session.execute(
                select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == new_chat_id)
            )).scalars().all()

            assert len(messages) == 1, "LLM context message was not migrated to the new chat_id"
            assert len(summaries) == 1, "LLM context summary was not migrated to the new chat_id"
            assert len(actions) == 1, "LLM admin action (audit trail + rollback source) was not migrated"
            assert len(glossary) == 1, "LLM glossary entry was not migrated"

            old_messages = (await session.execute(
                select(LlmContextMessageModel).where(LlmContextMessageModel.chat_id == old_chat_id)
            )).scalars().all()
            assert old_messages == [], "old chat_id rows must not remain as orphaned duplicates"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_daily_summary_and_ai_accounting_records():
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id, user_id = 509, 1509, 609
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(UserModel(telegram_user_id=user_id, username="u", is_bot=False))
            await session.flush()
            archive = MessageArchiveModel(
                chat_id=old_chat_id, user_id=user_id, telegram_message_id=1, snapshot_kind="created",
                snapshot_at=now, sent_at=now, message_type="text", text="hello", raw_message_json={}, snapshot_hash="h",
            )
            session.add(archive)
            run = DailySummaryRunModel(
                chat_id=old_chat_id, summary_date=now.date(), window_from=now - timedelta(hours=24),
                window_to=now, trigger="manual", status="generated", claimed_at=now, lease_until=now,
            )
            session.add(run)
            await session.flush()
            invocation = AiFeatureInvocationModel(
                feature="daily_summary", trigger="manual", chat_id=old_chat_id,
                scope_type="chat", scope_id=str(old_chat_id), summary_run_id=run.id,
                status="succeeded",
            )
            session.add(invocation)
            await session.flush()
            session.add(LlmUsageLogModel(
                invocation_id=invocation.id, summary_run_id=run.id, message_archive_id=archive.id,
                chat_id=old_chat_id, feature="daily_summary", stage="segment_topics", model="gpt-4o-mini",
                prompt_tokens=10, completion_tokens=5, total_tokens=15, estimated_cost_usd=0.0000045,
                pricing_status="known", status="succeeded", attempt_number=1,
            ))
            await session.commit()

            await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()

        async with session_factory() as session:
            from sqlalchemy import select
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            invocation = (await session.execute(select(AiFeatureInvocationModel))).scalar_one()
            usage = (await session.execute(select(LlmUsageLogModel))).scalar_one()
            archive = (await session.execute(select(MessageArchiveModel))).scalar_one()
            assert {run.chat_id, invocation.chat_id, usage.chat_id, archive.chat_id} == {new_chat_id}
            assert invocation.scope_type == "chat"
            assert invocation.scope_id == str(new_chat_id)
            assert usage.invocation_id == invocation.id
            assert usage.summary_run_id == run.id
            assert usage.message_archive_id == archive.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_chat_id_merges_quota_usage_from_both_chat_ids_without_reset_or_loss():
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id = 519, 1519
        period_start = datetime(2026, 10, 5, tzinfo=timezone.utc)
        period_end = datetime(2026, 10, 6, tzinfo=timezone.utc)
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(ChatModel(telegram_chat_id=new_chat_id, type="supergroup", title="New chat"))
            await session.commit()
            session.add_all([
                AiFeatureQuotaUsageModel(
                    feature="llm_admin", chat_id=old_chat_id, trigger="telegram_message",
                    source_chat_id=old_chat_id, source_message_id=10,
                    idempotency_key=f"llm_admin:{old_chat_id}:10", period_start=period_start,
                    period_end=period_end, policy_key="llm_admin_free_daily_v1", quota_limit=10,
                    access_tier="free", owner_exempt=False, status="consumed",
                ),
                AiFeatureQuotaUsageModel(
                    feature="llm_admin", chat_id=new_chat_id, trigger="telegram_message",
                    source_chat_id=new_chat_id, source_message_id=10,
                    idempotency_key=f"llm_admin:{new_chat_id}:10", period_start=period_start,
                    period_end=period_end, policy_key="llm_admin_free_daily_v1", quota_limit=10,
                    access_tier="free", owner_exempt=False, status="consumed",
                ),
            ])
            await session.commit()

            result = await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()
            assert result.migrated

        async with session_factory() as session:
            from sqlalchemy import select

            usage = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
            assert len(usage) == 2
            assert {row.chat_id for row in usage} == {new_chat_id}
            assert {row.source_message_id for row in usage} == {10}
            assert {row.source_chat_id for row in usage} == {old_chat_id, new_chat_id}
            assert {row.idempotency_key for row in usage} == {
                f"llm_admin:{old_chat_id}:10", f"llm_admin:{new_chat_id}:10",
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_chat_id_merges_glossary_without_violating_unique_term_constraint():
    """New chat already has a 'рест' entry (unlikely in practice but must not
    crash the whole migration with a unique-constraint violation)."""
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id = 502, 1502
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(ChatModel(telegram_chat_id=new_chat_id, type="supergroup", title="New chat"))
            await session.commit()

            session.add(LlmChatGlossaryModel(chat_id=old_chat_id, term="рест", definition="old definition"))
            session.add(LlmChatGlossaryModel(chat_id=new_chat_id, term="рест", definition="existing definition"))
            await session.commit()

            result = await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()
            assert result.migrated is True

        async with session_factory() as session:
            from sqlalchemy import select

            glossary = (await session.execute(
                select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == new_chat_id)
            )).scalars().all()
            assert len(glossary) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_group_upgrade_preserves_artifacts_under_new_chat_boundary():
    from selara.infrastructure.db.artifact_repository import ArtifactRepository
    engine, factory = await _session_factory()
    try:
        async with factory() as session:
            session.add(ChatModel(telegram_chat_id=503, type="group", title="Old"))
            await session.commit()
            repo = ArtifactRepository(session)
            artifact = await repo.create(chat_id=503, thread_id=None, creator_id=10,
                title="Test", pages=["image"], source={})
            artifact_id = artifact.id
            await session.commit()
            await migrate_chat_id(session, old_chat_id=503, new_chat_id=1503)
            await session.commit()
            assert await repo.get(artifact_id=artifact_id, chat_id=503, thread_id=None) is None
            assert await repo.get(artifact_id=artifact_id, chat_id=1503, thread_id=None) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_error_alert_destination(monkeypatch):
    class _MigrationSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def __init__(self):
            self.chats = {-123: ChatModel(telegram_chat_id=-123, type="group", title="Old chat")}
            self.runtime_settings = AdminRuntimeSettingsModel(
                id=1,
                error_alert_chat_id=-123,
                error_alerts_enabled=True,
            )
            self.statements = []

        async def get(self, model, key):
            if model is ChatModel:
                return self.chats.get(key)
            if model is AdminRuntimeSettingsModel:
                return self.runtime_settings if key == 1 else None
            raise AssertionError(f"Unexpected model: {model}")

        def add(self, row):
            if isinstance(row, ChatModel):
                self.chats[row.telegram_chat_id] = row

        async def execute(self, statement):
            self.statements.append(statement)
            if getattr(getattr(statement, "table", None), "name", None) == AdminRuntimeSettingsModel.__tablename__:
                params = statement.compile().params
                destination = next(
                    value for name, value in params.items() if name.startswith("error_alert_chat_id")
                )
                self.runtime_settings.error_alert_chat_id = destination

        async def scalars(self, statement):
            # This test has no purchase intents or entitlements to merge.
            return []

        async def flush(self):
            return None

    session = _MigrationSession()
    for helper_name in (
        "_merge_activity_generic",
        "_merge_activity_daily_generic",
        "_merge_activity_minute_generic",
        "_merge_activity_events_generic",
        "_merge_announce_subscriptions_generic",
        "_merge_text_aliases_generic",
        "_merge_bot_roles_generic",
        "_merge_moderation_state_generic",
        "_merge_rest_state_generic",
        "_merge_activity_event_sync_state",
        "_merge_chat_member_count_snapshot",
        "_move_chat_settings",
        "_move_chat_alias_settings",
        "_move_llm_context_and_actions",
        "_merge_llm_glossary_generic",
        "_migrate_economy_scopes",
    ):
        monkeypatch.setattr(chat_migration, helper_name, AsyncMock(return_value=0))
    old_chat_id, new_chat_id = -123, -100123
    configure_error_alerts(True, old_chat_id)
    configure_error_alerts(True, old_chat_id)
    await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)

    moved_runtime_settings = any(
        getattr(getattr(statement, "table", None), "name", None) == AdminRuntimeSettingsModel.__tablename__
        for statement in session.statements
    )
    assert moved_runtime_settings is True
    assert session.runtime_settings.error_alert_chat_id == new_chat_id


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_member_count_snapshot_to_supergroup():
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id = -123, -100123
        checked_at = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add_all([
                ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"),
                ChatModel(telegram_chat_id=new_chat_id, type="supergroup", title="New chat"),
                ChatMemberCountSnapshotModel(
                    chat_id=old_chat_id,
                    member_count=47,
                    last_checked_at=checked_at,
                    last_success_at=checked_at,
                ),
            ])
            await session.commit()

            await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()

        async with session_factory() as session:
            snapshot = await session.get(ChatMemberCountSnapshotModel, new_chat_id)
            assert snapshot is not None
            assert snapshot.member_count == 47
            assert snapshot.last_success_at is not None
            assert snapshot.last_success_at.replace(tzinfo=timezone.utc) == checked_at
            assert await session.get(ChatMemberCountSnapshotModel, old_chat_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_apply_chat_migration_refreshes_cached_alert_destination(monkeypatch):
    old_chat_id, new_chat_id = -123, -100123
    configure_error_alerts(True, old_chat_id)
    monkeypatch.setattr(
        presentation_chat_migration,
        "migrate_chat_id",
        AsyncMock(return_value=ChatMigrationResult(old_chat_id, new_chat_id, migrated=True)),
    )
    monkeypatch.setattr(presentation_chat_migration.GAME_STORE, "migrate_chat_id", AsyncMock())

    migrated = await presentation_chat_migration.apply_chat_migration(
        event=SimpleNamespace(chat=SimpleNamespace(id=old_chat_id, type="group", title="Old")),
        data={"db_session": object()},
        old_chat_id=old_chat_id,
        new_chat_id=new_chat_id,
        reason="test",
    )

    assert migrated is True
    assert get_error_alert_config().chat_id == new_chat_id
