from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0004"
down_revision = "20260314_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gacha_player_banner_wallets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("currency_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "banner"),
    )
    op.add_column(
        "gacha_pull_history",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="free"),
    )
    op.add_column(
        "gacha_pull_history",
        sa.Column("base_currency_price", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gacha_pull_history",
        sa.Column("purchase_price", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gacha_pull_history",
        sa.Column("sale_price", sa.Integer(), nullable=True),
    )
    op.add_column(
        "gacha_pull_history",
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        INSERT INTO gacha_player_banner_wallets (user_id, banner, currency_balance)
        SELECT user_id, banner, COALESCE(SUM(primogems), 0)
        FROM gacha_pull_history
        GROUP BY user_id, banner
        """
    )

    op.alter_column("gacha_pull_history", "source", server_default=None)
    op.alter_column("gacha_pull_history", "base_currency_price", server_default=None)
    op.alter_column("gacha_pull_history", "purchase_price", server_default=None)


def downgrade() -> None:
    op.drop_column("gacha_pull_history", "sold_at")
    op.drop_column("gacha_pull_history", "sale_price")
    op.drop_column("gacha_pull_history", "purchase_price")
    op.drop_column("gacha_pull_history", "base_currency_price")
    op.drop_column("gacha_pull_history", "source")
    op.drop_table("gacha_player_banner_wallets")
