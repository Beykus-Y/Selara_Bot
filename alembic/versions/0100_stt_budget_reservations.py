"""Durable admission reservations for the rolling Daily Summary STT budget."""

import sqlalchemy as sa
from alembic import op

revision = "0100_stt_budget_reservations"
down_revision = "0099_personal_ai_tools"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stt_budget_reservations",
        sa.Column("token", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("archive_row_id", sa.BigInteger(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("claim_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_ms", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="reserved"),
        sa.Column("usage_log_id", sa.BigInteger(), sa.ForeignKey("llm_usage_log.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("reserved_ms > 0", name="ck_stt_budget_reserved_ms"),
        sa.CheckConstraint("status IN ('reserved', 'consumed')", name="ck_stt_budget_status"),
    )
    op.create_index("idx_stt_budget_chat_created", "stt_budget_reservations", ["chat_id", "created_at"])
    op.create_index("idx_stt_budget_archive_lease", "stt_budget_reservations", ["archive_row_id", "lease_expires_at"])
    op.create_index("idx_stt_budget_usage_log", "stt_budget_reservations", ["usage_log_id"], unique=True)


def downgrade():
    op.drop_table("stt_budget_reservations")
