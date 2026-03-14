from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0002"
down_revision = "20260314_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gacha_player_banner_cooldowns",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("next_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "banner"),
    )


def downgrade() -> None:
    op.drop_table("gacha_player_banner_cooldowns")
