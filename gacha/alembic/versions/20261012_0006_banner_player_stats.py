from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261012_0006"
down_revision = "20260818_0005"
branch_labels = None
depends_on = None

# Exact per-banner totals rebuilt from the audit trail. Every pull, admin grant and
# card change that touched gacha_players.total_points / adventure_xp also wrote a
# gacha_pull_history row with the same points and adventure_xp values, so the sums
# below equal the old shared totals for any player with history. ON CONFLICT keeps
# a manual re-run from overwriting rows that live writes have already maintained.
_BACKFILL_SQL = """
INSERT INTO gacha_player_banner_stats (user_id, banner, total_points, adventure_xp)
SELECT user_id, banner, COALESCE(SUM(points), 0), COALESCE(SUM(adventure_xp), 0)
FROM gacha_pull_history
GROUP BY user_id, banner
ON CONFLICT (user_id, banner) DO NOTHING
"""


def upgrade() -> None:
    op.create_table(
        "gacha_player_banner_stats",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adventure_xp", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "banner"),
    )
    op.execute(_BACKFILL_SQL)
    # The column server defaults stay: removing them would need ALTER COLUMN, which SQLite rejects.


def downgrade() -> None:
    # Legacy shared totals on gacha_players are still maintained by the writer, so
    # dropping the per-banner table loses no data the previous release reads.
    op.drop_table("gacha_player_banner_stats")
