"""Persist sanitized operational alert history for Mini App monitoring."""
import sqlalchemy as sa
from alembic import op


revision = "0068_operational_alert_history"
down_revision = "0067_chat_member_count_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=160), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("sanitized_traceback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_operational_alerts_created", "operational_alerts", ["created_at"])
    op.create_index("idx_operational_alerts_fingerprint", "operational_alerts", ["fingerprint"])


def downgrade():
    op.drop_index("idx_operational_alerts_fingerprint", table_name="operational_alerts")
    op.drop_index("idx_operational_alerts_created", table_name="operational_alerts")
    op.drop_table("operational_alerts")
