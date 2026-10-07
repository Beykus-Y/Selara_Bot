"""Make Mini App broadcasts resumable: owner leases, delivery claims, cancellation and a stored photo.

Revision ID: 0105_broadcast_resume
Revises: 0103_ai_turn_leases
"""

import sqlalchemy as sa
from alembic import op

revision = "0105_broadcast_resume"
down_revision = "0103_ai_turn_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_broadcasts", sa.Column("lease_owner_token", sa.String(length=64), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("media_filename", sa.Text(), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("media_content", sa.LargeBinary(), nullable=True))
    op.add_column("admin_broadcast_deliveries", sa.Column("claim_token", sa.String(length=64), nullable=True))
    op.add_column(
        "admin_broadcast_deliveries",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_broadcast_deliveries", "claim_expires_at")
    op.drop_column("admin_broadcast_deliveries", "claim_token")
    op.drop_column("admin_broadcasts", "media_content")
    op.drop_column("admin_broadcasts", "media_filename")
    op.drop_column("admin_broadcasts", "cancelled_at")
    op.drop_column("admin_broadcasts", "lease_expires_at")
    op.drop_column("admin_broadcasts", "lease_owner_token")
