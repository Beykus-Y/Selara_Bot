"""Upgrade/downgrade on an existing database, including data and constraint checks."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "0083_ai_pets"
REVISION = "0084_model_catalog_router"


def sql(dsn, *statements):
    async def run():
        connection = await asyncpg.connect(dsn)
        try:
            return [await connection.fetch(statement) for statement in statements]
        finally:
            await connection.close()
    return asyncio.run(run())


def alembic(dsn, *args):
    env = {**os.environ, "DATABASE_URL": dsn.replace("postgresql://", "postgresql+asyncpg://")}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600)


def test_model_catalog_upgrade_downgrade_keeps_historical_costs():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    server = url.replace("postgresql+asyncpg://", "postgresql://")
    name = "catalog_migration_" + uuid4().hex[:12]
    sql(server, f'CREATE DATABASE "{name}"')
    dsn = server.rsplit("/", 1)[0] + "/" + name
    try:
        result = alembic(dsn, "upgrade", PREVIOUS)
        assert result.returncode == 0, result.stderr[-3000:]
        sql(dsn, "INSERT INTO llm_usage_log (feature, stage, model, estimated_cost_usd, pricing_status) "
            "VALUES ('llm_admin', 'test', 'gpt-4o-mini', 0.123, 'known')")
        result = alembic(dsn, "upgrade", REVISION)
        assert result.returncode == 0, result.stderr[-3000:]
        profiles = sql(dsn, "SELECT profile_key, ail_multiplier, model_key FROM llm_model_profiles")[0]
        assert {p["profile_key"] for p in profiles} == {"basic", "analytics", "freeform", "creative", "fast"}
        assert all(p["ail_multiplier"] == 1 and p["model_key"] is None for p in profiles)
        sql(dsn, "INSERT INTO llm_model_catalog (key, model_id, display_name) VALUES ('one', 'provider/one', 'One')",
            "INSERT INTO llm_model_identifiers (model_id, model_key) VALUES ('provider/one', 'one'), ('snapshot', 'one')",
            "UPDATE llm_model_profiles SET model_key = 'one', ail_multiplier = 5 WHERE profile_key = 'basic'",
            "UPDATE llm_usage_log SET model_profile = 'basic'")
        for statement in (
            "INSERT INTO llm_model_identifiers (model_id, model_key) VALUES ('snapshot', 'one')",
            "UPDATE llm_model_catalog SET prompt_price_usd_per_million = 'NaN'",
            "UPDATE llm_model_profiles SET ail_multiplier = 'NaN'",
            "UPDATE llm_model_profiles SET ail_multiplier = 0",
        ):
            with pytest.raises(asyncpg.PostgresError):
                sql(dsn, statement)
        result = alembic(dsn, "downgrade", PREVIOUS)
        assert result.returncode == 0, result.stderr[-3000:]
        rows = sql(dsn, "SELECT model, estimated_cost_usd FROM llm_usage_log")[0]
        assert str(rows[0]["estimated_cost_usd"]) == "0.123000000"
        assert rows[0]["model"] == "gpt-4o-mini"
        assert sql(dsn, "SELECT to_regclass('llm_model_catalog') AS table_name")[0][0]["table_name"] is None
        result = alembic(dsn, "upgrade", REVISION)
        assert result.returncode == 0, result.stderr[-3000:]
        # Valid catalog costs above the old precision are preserved; downgrade refuses atomically.
        for cost in ("100000", "-100000", "99999999999.999999999"):
            sql(dsn, f"UPDATE llm_usage_log SET estimated_cost_usd = {cost}")
            result = alembic(dsn, "downgrade", PREVIOUS)
            assert result.returncode != 0 and "costs do not fit NUMERIC(14,9)" in result.stderr
            assert sql(dsn, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == REVISION
            assert sql(dsn, "SELECT estimated_cost_usd::text AS cost FROM llm_usage_log")[0][0]["cost"] == (cost + ".000000000" if "." not in cost else cost)
        sql(dsn, "UPDATE llm_usage_log SET estimated_cost_usd = 99999.999999999")
        result = alembic(dsn, "downgrade", PREVIOUS)
        assert result.returncode == 0, result.stderr[-3000:]
        assert sql(dsn, "SELECT estimated_cost_usd::text AS cost FROM llm_usage_log")[0][0]["cost"] == "99999.999999999"
        result = alembic(dsn, "upgrade", REVISION)
        assert result.returncode == 0, result.stderr[-3000:]
        sql(dsn, "INSERT INTO chats (telegram_chat_id, type) VALUES (-9001, 'supergroup')",
            "INSERT INTO daily_summary_runs (chat_id, summary_date, window_from, window_to, trigger, lease_until, pipeline_cost_usd) "
            "VALUES (-9001, current_date, now() - interval '1 day', now(), 'scheduled', now(), 200000)")
        result = alembic(dsn, "downgrade", PREVIOUS)
        assert result.returncode != 0 and "summary costs do not fit NUMERIC(14,9)" in result.stderr
        assert sql(dsn, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == REVISION
        sql(dsn, "UPDATE daily_summary_runs SET pipeline_cost_usd = 99999.999999999")
        # Longer OpenRouter identifiers are stored without truncation; unsafe downgrade refuses atomically.
        long_model = "provider/" + "x" * 80
        sql(dsn, f"UPDATE llm_usage_log SET model = '{long_model}'")
        result = alembic(dsn, "downgrade", PREVIOUS)
        assert result.returncode != 0 and "identifiers longer than 64" in result.stderr
        assert sql(dsn, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == REVISION
        sql(dsn, "UPDATE llm_usage_log SET model = 'gpt-4o-mini'")
        result = alembic(dsn, "downgrade", PREVIOUS)
        assert result.returncode == 0, result.stderr[-3000:]
    finally:
        sql(server, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
