from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gacha_player_cards",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("character_code", sa.String(length=64), nullable=False),
        sa.Column("copies_owned", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "banner", "character_code"),
    )
    op.create_table(
        "gacha_players",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("adventure_rank", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("adventure_xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_primogems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "gacha_pull_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("character_code", sa.String(length=64), nullable=False),
        sa.Column("character_name", sa.String(length=128), nullable=False),
        sa.Column("rarity", sa.String(length=16), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("primogems", sa.Integer(), nullable=False),
        sa.Column("adventure_xp", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gacha_pull_history_pulled_at", "gacha_pull_history", ["pulled_at"], unique=False)
    op.create_index("ix_gacha_pull_history_user_id", "gacha_pull_history", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gacha_pull_history_user_id", table_name="gacha_pull_history")
    op.drop_index("ix_gacha_pull_history_pulled_at", table_name="gacha_pull_history")
    op.drop_table("gacha_pull_history")
    op.drop_table("gacha_players")
    op.drop_table("gacha_player_cards")
