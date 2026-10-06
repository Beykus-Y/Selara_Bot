"""Alembic upgrade/downgrade of 0079-0084 on a pre-filled database from the previous release."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS_RELEASE = "0078_family_pet_command_key"
_HEAD = "0084_personal_ai_memory"
_PERIOD = "now(), now() + interval '1 day'"

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _server_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


def _asyncpg_dsn(url: str, database: str | None = None) -> str:
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    if database:
        dsn = dsn.rsplit("/", 1)[0] + "/" + database
    return dsn


def _run_sql(dsn: str, *statements: str):
    async def run():
        connection = await asyncpg.connect(dsn)
        try:
            results = []
            for statement in statements:
                results.append(await connection.fetch(statement))
            return results
        finally:
            await connection.close()

    return asyncio.run(run())


def _alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": dsn.replace("postgresql://", "postgresql+asyncpg://")}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=_ROOT, env=env, capture_output=True, text=True, timeout=600
    )


def _quota_insert(chat_id: str, key: str, feature: str = "llm_admin") -> str:
    # Exactly the columns the previous release's code knows about.
    return (
        "INSERT INTO ai_feature_quota_usage (feature, chat_id, trigger, idempotency_key, period_start, period_end, "
        f"policy_key, quota_limit, access_tier, owner_exempt, status) VALUES ('{feature}', {chat_id}, 'telegram', "
        f"'{key}', {_PERIOD}, 'p', 10, 'free', false, 'consumed')"
    )


@pytest.fixture(scope="module")
def database():
    server = _server_url()
    name = f"mig_check_{uuid.uuid4().hex[:10]}"
    _run_sql(_asyncpg_dsn(server), f'CREATE DATABASE "{name}"')
    dsn = _asyncpg_dsn(server, name)
    try:
        result = _alembic(dsn, "upgrade", _PREVIOUS_RELEASE)
        assert result.returncode == 0, result.stderr[-2000:]
        _run_sql(
            dsn,
            "INSERT INTO chats (telegram_chat_id, type, title) VALUES (-1001, 'supergroup', 'Legacy'), "
            "(-2001, 'group', 'Before upgrade'), (-2002, 'supergroup', 'After upgrade'), (-3001, 'group', 'Gone')",
            "INSERT INTO selara_ai_purchase_intents (id, buyer_user_id, source_chat_id, chat_id, chat_title, "
            "product_key, amount_stars, currency, duration_seconds, invoice_payload, status, expires_at) VALUES "
            "('11111111-1111-1111-1111-111111111111', 7, -1001, -1001, 'Legacy', 'selara_ai_monthly', 100, 'XTR', "
            "2592000, 'selara_ai:v1:11111111-1111-1111-1111-111111111111', 'open', now() + interval '1 hour')",
            _quota_insert("-1001", "legacy-1", "llm_admin"),
            _quota_insert("-1001", "legacy-2", "daily_summary"),
            _quota_insert("-3001", "legacy-orphan", "llm_admin"),
            "DELETE FROM chats WHERE telegram_chat_id = -3001",  # ON DELETE SET NULL -> chat_id IS NULL
        )
        result = _alembic(dsn, "upgrade", _HEAD)
        assert result.returncode == 0, result.stderr[-2000:]
        yield dsn
    finally:
        _run_sql(_asyncpg_dsn(server), f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_upgrade_backfills_previous_release_rows(database):
    rows = _run_sql(
        database,
        "SELECT idempotency_key, quota_scope_type, quota_scope_id, pool_key, units FROM ai_feature_quota_usage "
        "WHERE idempotency_key LIKE 'legacy-%' ORDER BY idempotency_key",
        "SELECT target_scope, target_user_id, chat_id, paid_daily_limit FROM selara_ai_purchase_intents",
        "SELECT version_num FROM alembic_version",
    )
    by_key = {row["idempotency_key"]: row for row in rows[0]}
    assert (by_key["legacy-1"]["quota_scope_type"], by_key["legacy-1"]["quota_scope_id"]) == ("chat", -1001)
    assert by_key["legacy-1"]["pool_key"] == "llm_admin" and by_key["legacy-2"]["pool_key"] == "daily_summary"
    assert by_key["legacy-1"]["units"] == 1
    orphan = by_key["legacy-orphan"]
    assert orphan["quota_scope_type"] == "legacy_orphan"
    legacy_intent = rows[1][0]
    assert (legacy_intent["target_scope"], legacy_intent["target_user_id"]) == ("chat", None)
    assert legacy_intent["chat_id"] == -1001 and legacy_intent["paid_daily_limit"] is None
    assert rows[2][0]["version_num"] == _HEAD


def test_old_image_insert_and_group_migration_stay_scoped_after_upgrade(database):
    _run_sql(
        database,
        _quota_insert("-2001", "old-image-insert", "llm_admin"),
        _quota_insert("-2001", "old-image-insert-2", "daily_summary"),
    )
    inserted = _run_sql(
        database,
        "SELECT quota_scope_type, quota_scope_id, pool_key FROM ai_feature_quota_usage "
        "WHERE idempotency_key = 'old-image-insert'",
    )[0][0]
    assert (inserted["quota_scope_type"], inserted["quota_scope_id"], inserted["pool_key"]) == ("chat", -2001, "llm_admin")

    # The previous release migrates a group to a supergroup by changing chat_id only.
    _run_sql(
        database,
        "UPDATE ai_feature_quota_usage SET chat_id = -2002 WHERE chat_id = -2001",
    )
    moved = _run_sql(
        database,
        "SELECT quota_scope_id FROM ai_feature_quota_usage WHERE idempotency_key LIKE 'old-image-insert%'",
    )[0]
    assert {row["quota_scope_id"] for row in moved} == {-2002}

    # The current release sets both columns itself; the trigger must not override that.
    # The new image sets chat_id and quota_scope_id together. quota_scope_id differs from both the
    # old scope and the new chat_id here, so a trigger that always copied chat_id would give -1001.
    _run_sql(
        database,
        "UPDATE ai_feature_quota_usage SET chat_id = -1001, quota_scope_id = -2003 "
        "WHERE idempotency_key = 'old-image-insert-2'",
    )
    explicit = _run_sql(
        database, "SELECT quota_scope_id FROM ai_feature_quota_usage WHERE idempotency_key = 'old-image-insert-2'"
    )[0][0]
    assert explicit["quota_scope_id"] == -2003

    # Rows of other scopes are never rescoped by a chat_id change.
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9001, false) ON CONFLICT DO NOTHING",
        "INSERT INTO ai_feature_quota_usage (feature, chat_id, trigger, idempotency_key, period_start, period_end, "
        "policy_key, quota_limit, access_tier, owner_exempt, status, quota_scope_type, quota_scope_id, pool_key) "
        f"VALUES ('personal_chat', -2002, 'telegram', 'user-row', {_PERIOD}, 'p', 5, 'free', false, 'consumed', "
        "'user', 9001, 'personal_daily')",
        "UPDATE ai_feature_quota_usage SET chat_id = -1001 WHERE idempotency_key = 'user-row'",
    )
    user_row = _run_sql(
        database, "SELECT quota_scope_type, quota_scope_id FROM ai_feature_quota_usage WHERE idempotency_key = 'user-row'"
    )[0][0]
    assert (user_row["quota_scope_type"], user_row["quota_scope_id"]) == ("user", 9001)


def test_personal_ai_tables_enforce_constraints_and_cascade_with_the_user(database):
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9100, false) ON CONFLICT DO NOTHING",
        "INSERT INTO personal_ai_profiles (user_id) VALUES (9100)",
        "INSERT INTO personal_ai_messages (user_id, role, content) VALUES (9100, 'user', 'hi'), (9100, 'assistant', 'yo')",
        "INSERT INTO personal_ai_summaries (user_id, content, period_start, period_end, messages_count) "
        "VALUES (9100, 's', now(), now(), 2)",
    )
    profile = _run_sql(database, "SELECT display_name, mode, formality, reply_length, emoji_enabled, revision "
                                 "FROM personal_ai_profiles WHERE user_id = 9100")[0][0]
    assert (profile["display_name"], profile["mode"], profile["formality"]) == ("Selara", "assistant", "ty")
    assert profile["emoji_enabled"] is True and profile["revision"] == 0

    for bad in (
        "UPDATE personal_ai_profiles SET mode = 'bogus' WHERE user_id = 9100",
        "UPDATE personal_ai_profiles SET character_custom = repeat('x', 501) WHERE user_id = 9100",
        "INSERT INTO personal_ai_messages (user_id, role, content) VALUES (9100, 'system', 'x')",
        "INSERT INTO personal_ai_messages (user_id, thread, role, content) VALUES (9100, 'other', 'user', 'x')",
    ):
        with pytest.raises(asyncpg.PostgresError):
            _run_sql(database, bad)

    # Deleting the user removes everything private; nothing else ever deletes it.
    _run_sql(database, "DELETE FROM users WHERE telegram_user_id = 9100")
    counts = _run_sql(
        database,
        "SELECT count(*) AS n FROM personal_ai_profiles WHERE user_id = 9100",
        "SELECT count(*) AS n FROM personal_ai_messages WHERE user_id = 9100",
        "SELECT count(*) AS n FROM personal_ai_summaries WHERE user_id = 9100",
    )
    assert [row[0]["n"] for row in counts] == [0, 0, 0]


def test_personal_memory_table_enforces_constraints_and_cascades_with_the_user(database):
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9110, false) ON CONFLICT DO NOTHING",
        "INSERT INTO personal_ai_profiles (user_id) VALUES (9110)",
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9110, 'I am Vegan', 'explicit')",
    )
    defaults = _run_sql(
        database,
        "SELECT auto_memory_enabled, memory_extract_cursor FROM personal_ai_profiles WHERE user_id = 9110",
        "SELECT pinned, last_used_at FROM personal_ai_memories WHERE user_id = 9110",
        "SELECT memory_free_limit, memory_paid_limit, memory_auto_extract, memory_extract_every "
        "FROM selara_personal_config",
    )
    assert dict(defaults[0][0]) == {"auto_memory_enabled": False, "memory_extract_cursor": 0}
    assert dict(defaults[1][0]) == {"pinned": False, "last_used_at": None}

    for bad in (
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9110, 'x', 'bogus')",
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9110, '', 'explicit')",
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9110, repeat('x', 301), 'explicit')",
        # Case-insensitive duplicates of one user are impossible even if two writers race.
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9110, 'I AM VEGAN', 'explicit')",
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (999999991, 'x', 'explicit')",
        "INSERT INTO selara_personal_config (id, memory_extract_every) VALUES (1, 1)",
        "INSERT INTO selara_personal_config (id, memory_extract_every) VALUES (1, 41)",
    ):
        try:
            _run_sql(database, bad)
        except asyncpg.PostgresError:
            continue
        pytest.fail(f"statement should have been rejected: {bad}")

    _run_sql(database, "DELETE FROM users WHERE telegram_user_id = 9110")
    left = _run_sql(database, "SELECT count(*) AS n FROM personal_ai_memories WHERE user_id = 9110")
    assert left[0][0]["n"] == 0


def test_downgrade_refuses_to_drop_personal_memories(database):
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9111, false) ON CONFLICT DO NOTHING",
        "INSERT INTO personal_ai_memories (user_id, content, source) VALUES (9111, 'Я веган', 'explicit')",
    )
    refused = _alembic(database, "downgrade", "0083_ai_pets")
    assert refused.returncode != 0
    assert "Cannot downgrade 0084_personal_ai_memory" in refused.stderr
    assert _run_sql(database, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == _HEAD
    assert _run_sql(database, "SELECT count(*) AS n FROM personal_ai_memories")[0][0]["n"] == 1
    _run_sql(database, "DELETE FROM users WHERE telegram_user_id = 9111")

    assert _alembic(database, "downgrade", "0083_ai_pets").returncode == 0
    columns = _run_sql(
        database,
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'personal_ai_profiles' "
        "AND column_name IN ('auto_memory_enabled', 'memory_extract_cursor')",
    )[0]
    assert columns == []
    result = _alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stderr[-2000:]


def test_downgrade_refuses_to_drop_private_personal_ai_data(database):
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9101, false) ON CONFLICT DO NOTHING",
        "INSERT INTO personal_ai_profiles (user_id) VALUES (9101)",
    )
    refused = _alembic(database, "downgrade", "0081_selara_personal_config")
    assert refused.returncode != 0
    assert "Cannot downgrade 0082_personal_ai" in refused.stderr
    assert _run_sql(database, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == _HEAD
    assert _run_sql(database, "SELECT count(*) AS n FROM personal_ai_profiles")[0][0]["n"] == 1
    _run_sql(database, "DELETE FROM users WHERE telegram_user_id = 9101")


def test_downgrade_refuses_to_drop_personal_data_and_is_clean_without_it(database):
    _run_sql(
        database,
        "INSERT INTO users (telegram_user_id, is_bot) VALUES (9002, false) ON CONFLICT DO NOTHING",
        "INSERT INTO user_entitlements (user_id, product_key, status, valid_from, valid_until) "
        "VALUES (9002, 'selara_personal_monthly', 'active', now(), now() + interval '30 days')",
    )
    refused = _alembic(database, "downgrade", _PREVIOUS_RELEASE)
    assert refused.returncode != 0
    assert "Cannot downgrade 0079_personal_entitlements" in refused.stderr
    # The failed downgrade must leave the schema untouched.
    assert _run_sql(database, "SELECT version_num FROM alembic_version")[0][0]["version_num"] == _HEAD
    assert _run_sql(database, "SELECT count(*) AS n FROM user_entitlements")[0][0]["n"] == 1

    _run_sql(database, "DELETE FROM user_entitlements", "DELETE FROM ai_feature_quota_usage WHERE quota_scope_type = 'user'")
    assert _alembic(database, "downgrade", _PREVIOUS_RELEASE).returncode == 0
    survivors = _run_sql(database, "SELECT count(*) AS n FROM ai_feature_quota_usage WHERE idempotency_key LIKE 'legacy-%'")
    assert survivors[0][0]["n"] == 3
    result = _alembic(database, "upgrade", _HEAD)
    assert result.returncode == 0, result.stderr[-2000:]
