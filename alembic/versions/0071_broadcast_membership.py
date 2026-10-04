"""Track bot group membership and bind idempotency keys to broadcast content."""

import sqlalchemy as sa
from alembic import op


revision = "0071_broadcast_membership"
down_revision = "0070_member_snapshot_retry"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chats",
        sa.Column("is_bot_member", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "admin_broadcasts",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_column("admin_broadcasts", "request_fingerprint")
    op.drop_column("chats", "is_bot_member")
