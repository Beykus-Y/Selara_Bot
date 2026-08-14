"""store every native reaction on admin broadcasts

Revision ID: 0052_all_broadcast_reactions
Revises: 0051_broadcast_reactions
Create Date: 2026-08-14 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052_all_broadcast_reactions"
down_revision: str | None = "0051_broadcast_reactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_broadcast_reactions",
        sa.Column("reaction_type", sa.String(length=16), nullable=False, server_default="emoji"),
    )
    op.add_column(
        "admin_broadcast_reactions",
        sa.Column("reaction_value", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "admin_broadcast_reactions",
        sa.Column("actor_key", sa.String(length=80), nullable=True),
    )
    op.execute(
        """
        UPDATE admin_broadcast_reactions
        SET reaction_value = replace(replace(emoji, chr(65039), ''), chr(65038), ''),
            actor_key = CASE
                WHEN actor_user_id IS NOT NULL THEN 'user:' || actor_user_id::text
                ELSE 'chat:' || actor_chat_id::text
            END
        """
    )
    op.execute(
        """
        DELETE FROM admin_broadcast_reactions
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY delivery_id, source, actor_key, reaction_type, reaction_value
                           ORDER BY active DESC, reacted_at DESC, id DESC
                       ) AS duplicate_number
                FROM admin_broadcast_reactions
            ) AS ranked_reactions
            WHERE duplicate_number > 1
        )
        """
    )
    op.alter_column("admin_broadcast_reactions", "reaction_value", nullable=False)
    op.alter_column("admin_broadcast_reactions", "actor_key", nullable=False)
    op.alter_column("admin_broadcast_reactions", "option_key", nullable=True)
    op.drop_constraint(
        "uq_admin_broadcast_reaction_user_option",
        "admin_broadcast_reactions",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_admin_broadcast_reactions_type",
        "admin_broadcast_reactions",
        "reaction_type IN ('emoji', 'custom_emoji', 'paid')",
    )
    op.create_unique_constraint(
        "uq_admin_broadcast_reaction_actor_value",
        "admin_broadcast_reactions",
        ["delivery_id", "source", "actor_key", "reaction_type", "reaction_value"],
    )

    op.add_column(
        "admin_broadcast_reaction_counts",
        sa.Column("reaction_type", sa.String(length=16), nullable=False, server_default="emoji"),
    )
    op.add_column(
        "admin_broadcast_reaction_counts",
        sa.Column("reaction_value", sa.String(length=128), nullable=True),
    )
    op.execute(
        """
        UPDATE admin_broadcast_reaction_counts
        SET reaction_value = replace(replace(emoji, chr(65039), ''), chr(65038), '')
        """
    )
    op.execute(
        """
        DELETE FROM admin_broadcast_reaction_counts
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY delivery_id, reaction_type, reaction_value
                           ORDER BY observed_at DESC, id DESC
                       ) AS duplicate_number
                FROM admin_broadcast_reaction_counts
            ) AS ranked_counts
            WHERE duplicate_number > 1
        )
        """
    )
    op.alter_column("admin_broadcast_reaction_counts", "reaction_value", nullable=False)
    op.drop_constraint(
        "uq_admin_broadcast_reaction_count_delivery_emoji",
        "admin_broadcast_reaction_counts",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_admin_broadcast_reaction_counts_type",
        "admin_broadcast_reaction_counts",
        "reaction_type IN ('emoji', 'custom_emoji', 'paid')",
    )
    op.create_unique_constraint(
        "uq_admin_broadcast_reaction_count_delivery_value",
        "admin_broadcast_reaction_counts",
        ["delivery_id", "reaction_type", "reaction_value"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_admin_broadcast_reaction_count_delivery_value",
        "admin_broadcast_reaction_counts",
        type_="unique",
    )
    op.drop_constraint(
        "ck_admin_broadcast_reaction_counts_type",
        "admin_broadcast_reaction_counts",
        type_="check",
    )
    op.execute("DELETE FROM admin_broadcast_reaction_counts WHERE reaction_type <> 'emoji'")
    op.drop_column("admin_broadcast_reaction_counts", "reaction_value")
    op.drop_column("admin_broadcast_reaction_counts", "reaction_type")
    op.create_unique_constraint(
        "uq_admin_broadcast_reaction_count_delivery_emoji",
        "admin_broadcast_reaction_counts",
        ["delivery_id", "emoji"],
    )

    op.drop_constraint(
        "uq_admin_broadcast_reaction_actor_value",
        "admin_broadcast_reactions",
        type_="unique",
    )
    op.drop_constraint(
        "ck_admin_broadcast_reactions_type",
        "admin_broadcast_reactions",
        type_="check",
    )
    op.execute(
        "DELETE FROM admin_broadcast_reactions WHERE option_key IS NULL OR reaction_type <> 'emoji'"
    )
    op.alter_column("admin_broadcast_reactions", "option_key", nullable=False)
    op.drop_column("admin_broadcast_reactions", "actor_key")
    op.drop_column("admin_broadcast_reactions", "reaction_value")
    op.drop_column("admin_broadcast_reactions", "reaction_type")
    op.create_unique_constraint(
        "uq_admin_broadcast_reaction_user_option",
        "admin_broadcast_reactions",
        ["delivery_id", "source", "actor_user_id", "option_key"],
    )
