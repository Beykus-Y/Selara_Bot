"""add feature invocations and generalize per-provider usage records

Revision ID: 0072_ai_accounting_foundation
Revises: 0071_broadcast_membership
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_ai_accounting_foundation"
down_revision: str | None = "0071_broadcast_membership"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_feature_invocations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False, server_default="chat"),
        sa.Column("scope_id", sa.String(128), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("summary_run_id", sa.BigInteger(), nullable=True),
        sa.Column("error_category", sa.String(48), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'partial')",
            name="ck_ai_feature_invocations_status",
        ),
        sa.ForeignKeyConstraint(["summary_run_id"], ["daily_summary_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_ai_feature_invocations_feature_started", "ai_feature_invocations", ["feature", "started_at"]
    )
    op.create_index("idx_ai_feature_invocations_chat_started", "ai_feature_invocations", ["chat_id", "started_at"])
    op.create_index("idx_ai_feature_invocations_summary_run", "ai_feature_invocations", ["summary_run_id"])
    op.add_column(
        "daily_summary_runs",
        sa.Column("pipeline_has_unknown_cost", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column(
        "daily_summary_runs", "pipeline_cost_usd", existing_type=sa.Numeric(10, 6), type_=sa.Numeric(14, 9)
    )

    # Keep all historical rows. Old LLM zero-cost rows for unregistered models
    # cannot be considered free; mark their cost unknown without deleting usage.
    op.add_column("llm_usage_log", sa.Column("call_id", sa.String(36), nullable=True))
    op.add_column("llm_usage_log", sa.Column("request_id", sa.String(36), nullable=True))
    op.add_column("llm_usage_log", sa.Column("invocation_id", sa.BigInteger(), nullable=True))
    op.add_column("llm_usage_log", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "llm_usage_log",
        sa.Column("pricing_status", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "llm_usage_log", sa.Column("status", sa.String(24), nullable=False, server_default="succeeded")
    )
    op.add_column("llm_usage_log", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.add_column("llm_usage_log", sa.Column("error_category", sa.String(48), nullable=True))
    op.alter_column("llm_usage_log", "chat_id", existing_type=sa.BigInteger(), nullable=True)
    op.execute(
        "UPDATE llm_usage_log SET chat_id = NULL "
        "WHERE chat_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM chats WHERE chats.telegram_chat_id = llm_usage_log.chat_id)"
    )
    op.alter_column(
        "llm_usage_log", "estimated_cost_usd", existing_type=sa.Numeric(10, 6), type_=sa.Numeric(14, 9), nullable=True
    )
    op.drop_constraint("llm_usage_log_summary_run_id_fkey", "llm_usage_log", type_="foreignkey")
    op.drop_constraint("llm_usage_log_message_archive_id_fkey", "llm_usage_log", type_="foreignkey")
    op.create_foreign_key(
        "fk_llm_usage_log_invocation_id", "llm_usage_log", "ai_feature_invocations",
        ["invocation_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_llm_usage_log_chat_id", "llm_usage_log", "chats", ["chat_id"], ["telegram_chat_id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "llm_usage_log_summary_run_id_fkey", "llm_usage_log", "daily_summary_runs",
        ["summary_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "llm_usage_log_message_archive_id_fkey", "llm_usage_log", "messages",
        ["message_archive_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("idx_llm_usage_log_call_id", "llm_usage_log", ["call_id"], unique=True)
    op.create_index("idx_llm_usage_log_request_id", "llm_usage_log", ["request_id"])
    op.create_index("idx_llm_usage_log_invocation", "llm_usage_log", ["invocation_id"])
    op.create_index("idx_llm_usage_log_feature_created", "llm_usage_log", ["feature", "created_at"])
    op.create_check_constraint(
        "ck_llm_usage_log_pricing_status", "llm_usage_log", "pricing_status IN ('known', 'unknown', 'legacy')"
    )
    op.create_check_constraint(
        "ck_llm_usage_log_status", "llm_usage_log", "status IN ('succeeded', 'failed', 'validation_failed')"
    )
    op.execute(
        "UPDATE llm_usage_log SET pricing_status = 'known' "
        "WHERE (stage = 'stt' AND audio_seconds IS NOT NULL) "
        "OR (stage <> 'stt' AND model IN ('gpt-4o-mini', 'gpt-4o') "
        "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
        "AND estimated_cost_usd IS NOT NULL)"
    )
    op.execute(
        "UPDATE llm_usage_log SET pricing_status = 'unknown', estimated_cost_usd = NULL "
        "WHERE NOT ((stage = 'stt' AND audio_seconds IS NOT NULL) "
        "OR (stage <> 'stt' AND model IN ('gpt-4o-mini', 'gpt-4o') "
        "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
        "AND estimated_cost_usd IS NOT NULL))"
    )
    op.execute(
        "INSERT INTO ai_feature_invocations "
        "(feature, scope_type, chat_id, trigger, status, summary_run_id, started_at, completed_at) "
        "SELECT 'daily_summary', 'chat', chat_id, trigger, "
        "CASE WHEN status IN ('sent', 'generated', 'send_failed') THEN 'succeeded' "
        "WHEN status = 'failed' THEN 'failed' ELSE 'running' END, id, created_at, "
        "CASE WHEN status IN ('sent', 'generated', 'send_failed', 'failed') THEN created_at ELSE NULL END "
        "FROM daily_summary_runs"
    )
    op.execute(
        "UPDATE llm_usage_log AS usage SET invocation_id = invocation.id "
        "FROM ai_feature_invocations AS invocation "
        "WHERE usage.summary_run_id = invocation.summary_run_id AND invocation.feature = 'daily_summary'"
    )
    op.execute(
        "UPDATE daily_summary_runs SET pipeline_has_unknown_cost = true "
        "WHERE id IN (SELECT summary_run_id FROM llm_usage_log "
        "WHERE summary_run_id IS NOT NULL AND pricing_status = 'unknown')"
    )


def downgrade() -> None:
    # A legacy schema cannot represent an unknown cost. Refuse to turn NULL into
    # zero or silently discard uncertainty during rollback.
    bind = op.get_bind()
    unknown_costs = bind.execute(
        sa.text("SELECT count(*) FROM llm_usage_log WHERE estimated_cost_usd IS NULL")
    ).scalar_one()
    if unknown_costs:
        raise RuntimeError(
            "Cannot downgrade AI accounting while unknown-cost usage rows exist; "
            "the legacy schema would misrepresent them as free."
        )

    op.drop_column("daily_summary_runs", "pipeline_has_unknown_cost")
    op.drop_constraint("ck_llm_usage_log_status", "llm_usage_log", type_="check")
    op.drop_constraint("ck_llm_usage_log_pricing_status", "llm_usage_log", type_="check")
    op.drop_index("idx_llm_usage_log_feature_created", table_name="llm_usage_log")
    op.drop_index("idx_llm_usage_log_invocation", table_name="llm_usage_log")
    op.drop_index("idx_llm_usage_log_call_id", table_name="llm_usage_log")
    op.drop_index("idx_llm_usage_log_request_id", table_name="llm_usage_log")
    op.drop_constraint("llm_usage_log_message_archive_id_fkey", "llm_usage_log", type_="foreignkey")
    op.drop_constraint("llm_usage_log_summary_run_id_fkey", "llm_usage_log", type_="foreignkey")
    op.drop_constraint("fk_llm_usage_log_chat_id", "llm_usage_log", type_="foreignkey")
    op.drop_constraint("fk_llm_usage_log_invocation_id", "llm_usage_log", type_="foreignkey")
    op.create_foreign_key(
        "llm_usage_log_summary_run_id_fkey", "llm_usage_log", "daily_summary_runs",
        ["summary_run_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "llm_usage_log_message_archive_id_fkey", "llm_usage_log", "messages",
        ["message_archive_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_column("llm_usage_log", "error_category")
    op.drop_column("llm_usage_log", "attempt_number")
    op.drop_column("llm_usage_log", "status")
    op.drop_column("llm_usage_log", "pricing_status")
    op.drop_column("llm_usage_log", "total_tokens")
    op.drop_column("llm_usage_log", "invocation_id")
    op.drop_column("llm_usage_log", "call_id")
    op.drop_column("llm_usage_log", "request_id")
    op.drop_index("idx_ai_feature_invocations_summary_run", table_name="ai_feature_invocations")
    op.drop_index("idx_ai_feature_invocations_chat_started", table_name="ai_feature_invocations")
    op.drop_index("idx_ai_feature_invocations_feature_started", table_name="ai_feature_invocations")
    op.drop_table("ai_feature_invocations")
