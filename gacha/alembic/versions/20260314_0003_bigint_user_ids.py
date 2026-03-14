from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0003"
down_revision = "20260314_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("gacha_players", "user_id", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)
    op.alter_column(
        "gacha_player_cards",
        "user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "gacha_player_banner_cooldowns",
        "user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "gacha_pull_history",
        "user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column("gacha_pull_history", "user_id", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
    op.alter_column(
        "gacha_player_banner_cooldowns",
        "user_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "gacha_player_cards",
        "user_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column("gacha_players", "user_id", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
