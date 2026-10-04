"""Track failed Telegram member-count checks for fast retries."""

import sqlalchemy as sa
from alembic import op


revision = "0070_chat_member_snapshot_retries"
down_revision = "0069_broadcast_idempotency_key"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_member_count_snapshots",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("chat_member_count_snapshots", "last_error_at")
