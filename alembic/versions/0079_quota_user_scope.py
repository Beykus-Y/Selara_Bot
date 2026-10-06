"""count feature quotas per scope (chat or user), pool and units

Revision ID: 0079_quota_user_scope
Revises: 0078_personal_entitlements
Create Date: 2026-10-06 00:00:01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_quota_user_scope"
down_revision: str | None = "0078_personal_entitlements"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "ai_feature_quota_usage"
_TRIGGER_FUNCTION = "ai_feature_quota_usage_scope_defaults"
_TRIGGER = "trg_ai_feature_quota_usage_scope_defaults"
_UPDATE_FUNCTION = "ai_feature_quota_usage_follow_chat_id"
_UPDATE_TRIGGER = "trg_ai_feature_quota_usage_follow_chat_id"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("quota_scope_type", sa.String(length=16), server_default="chat", nullable=False))
    op.add_column(_TABLE, sa.Column("quota_scope_id", sa.BigInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("pool_key", sa.String(length=48), nullable=True))
    op.add_column(_TABLE, sa.Column("units", sa.Numeric(10, 2), server_default="1", nullable=False))

    op.execute(f"UPDATE {_TABLE} SET quota_scope_id = chat_id, pool_key = feature")
    # chat_id is ON DELETE SET NULL, so rows of already deleted chats have no scope to count against.
    op.execute(f"UPDATE {_TABLE} SET quota_scope_type = 'legacy_orphan' WHERE chat_id IS NULL")
    op.alter_column(_TABLE, "pool_key", existing_type=sa.String(length=48), nullable=False)

    op.create_check_constraint(
        "ck_ai_feature_quota_scope_type", _TABLE, "quota_scope_type IN ('chat', 'user', 'legacy_orphan')"
    )
    op.create_check_constraint(
        "ck_ai_feature_quota_scope_id", _TABLE, "quota_scope_type = 'legacy_orphan' OR quota_scope_id IS NOT NULL"
    )
    op.create_check_constraint("ck_ai_feature_quota_units", _TABLE, "units >= 0")
    op.create_index(
        "idx_ai_feature_quota_scope_usage",
        _TABLE,
        ["pool_key", "quota_scope_type", "quota_scope_id", "period_start", "status"],
    )

    # Keep the previous release's INSERTs (which know nothing about scope or pool)
    # valid, so rolling back to the old image after this migration still works.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.pool_key IS NULL THEN
                NEW.pool_key := NEW.feature;
            END IF;
            IF NEW.quota_scope_id IS NULL AND NEW.quota_scope_type = 'chat' AND NEW.chat_id IS NOT NULL THEN
                NEW.quota_scope_id := NEW.chat_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"CREATE TRIGGER {_TRIGGER} BEFORE INSERT ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()"
    )

    # The previous release also moves a group's rows to a supergroup by updating only
    # chat_id. Follow such a move for chat-scoped rows, so a rollback never leaves the
    # quota bucket behind. Updates that set quota_scope_id themselves (the current
    # release) are left alone, as are user-scoped rows.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_UPDATE_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.quota_scope_type = 'chat'
               AND NEW.chat_id IS NOT NULL
               AND NEW.chat_id IS DISTINCT FROM OLD.chat_id
               AND NEW.quota_scope_id IS NOT DISTINCT FROM OLD.quota_scope_id THEN
                NEW.quota_scope_id := NEW.chat_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"CREATE TRIGGER {_UPDATE_TRIGGER} BEFORE UPDATE OF chat_id ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_UPDATE_FUNCTION}()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_UPDATE_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_UPDATE_FUNCTION}()")
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    op.drop_index("idx_ai_feature_quota_scope_usage", table_name=_TABLE)
    op.drop_constraint("ck_ai_feature_quota_units", _TABLE, type_="check")
    op.drop_constraint("ck_ai_feature_quota_scope_id", _TABLE, type_="check")
    op.drop_constraint("ck_ai_feature_quota_scope_type", _TABLE, type_="check")
    op.drop_column(_TABLE, "units")
    op.drop_column(_TABLE, "pool_key")
    op.drop_column(_TABLE, "quota_scope_id")
    op.drop_column(_TABLE, "quota_scope_type")
