from __future__ import annotations

import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_0072_migration_preserves_legacy_rows_and_does_not_call_unknown_models_free():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    migration_path = Path(__file__).parents[2] / "alembic" / "versions" / "0072_ai_accounting_foundation.py"
    spec = importlib.util.spec_from_file_location("ai_accounting_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"test_ai_accounting_{uuid.uuid4().hex}"
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            await connection.exec_driver_sql("CREATE TABLE chats (telegram_chat_id BIGINT PRIMARY KEY)")
            await connection.exec_driver_sql("CREATE TABLE users (telegram_user_id BIGINT PRIMARY KEY)")
            await connection.exec_driver_sql(
                "CREATE TABLE daily_summary_runs (id BIGSERIAL PRIMARY KEY, chat_id BIGINT NOT NULL, "
                "summary_date DATE NOT NULL, trigger VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), pipeline_cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0, "
                "FOREIGN KEY (chat_id) REFERENCES chats(telegram_chat_id))"
            )
            await connection.exec_driver_sql("CREATE TABLE messages (id BIGSERIAL PRIMARY KEY)")
            await connection.exec_driver_sql(
                "CREATE TABLE llm_usage_log (id BIGSERIAL PRIMARY KEY, summary_run_id BIGINT NULL, "
                "message_archive_id BIGINT NULL, chat_id BIGINT NOT NULL, feature VARCHAR(32) NOT NULL, "
                "stage VARCHAR(32) NOT NULL, model VARCHAR(64) NOT NULL, prompt_tokens INTEGER NULL, "
                "completion_tokens INTEGER NULL, audio_seconds NUMERIC(10,2) NULL, "
                "estimated_cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT llm_usage_log_summary_run_id_fkey FOREIGN KEY (summary_run_id) "
                "REFERENCES daily_summary_runs(id) ON DELETE CASCADE, "
                "CONSTRAINT llm_usage_log_message_archive_id_fkey FOREIGN KEY (message_archive_id) "
                "REFERENCES messages(id) ON DELETE CASCADE)"
            )
            await connection.execute(text("INSERT INTO chats VALUES (-100)"))
            await connection.execute(text("INSERT INTO users VALUES (7)"))
            result = await connection.execute(
                text("INSERT INTO daily_summary_runs (chat_id, summary_date, trigger, status) "
                     "VALUES (-100, '2026-10-04', 'manual', 'sent') RETURNING id")
            )
            run_id = result.scalar_one()
            await connection.execute(
                text("INSERT INTO llm_usage_log "
                     "(summary_run_id, chat_id, feature, stage, model, prompt_tokens, completion_tokens, estimated_cost_usd) "
                     "VALUES (:run, -100, 'daily_summary', 'writer', 'gpt-4o-mini', 100, 20, 0.000027), "
                     "(:run, -100, 'daily_summary', 'analyst', 'provider/future', 80, 10, 0), "
                     "(NULL, -100, 'daily_summary', 'stt', 'whisper-compatible', NULL, NULL, 0.001)"),
                {"run": run_id},
            )

            def run_upgrade(sync_connection):
                context = MigrationContext.configure(sync_connection)
                with Operations.context(context):
                    migration.upgrade()

            await connection.run_sync(run_upgrade)
            rows = (await connection.execute(text(
                "SELECT id, invocation_id, pricing_status, estimated_cost_usd, stage FROM llm_usage_log ORDER BY id"
            ))).all()
            assert len(rows) == 3
            assert rows[0].invocation_id is not None
            assert rows[0].pricing_status == "known" and rows[0].estimated_cost_usd == Decimal("0.000027")
            assert rows[1].pricing_status == "unknown" and rows[1].estimated_cost_usd is None
            assert rows[2].pricing_status == "known" and rows[2].estimated_cost_usd == Decimal("0.001")
            run = (await connection.execute(text(
                "SELECT pipeline_has_unknown_cost FROM daily_summary_runs WHERE id = :id"
            ), {"id": run_id})).scalar_one()
            assert run is True
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await engine.dispose()
