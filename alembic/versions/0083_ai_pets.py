"""AI pets core: pets, per-chat relationships, event journal and the item catalog

Pets belong to users and survive chat deletion (chat FKs are SET NULL); data
that only makes sense inside one chat (relationships, events) cascades with it.
``chat_settings.pets_enabled`` is a per-chat permission, off by default.

There is deliberately no CHECK tying ``status='active'`` to a non-NULL
``current_chat_id``: with ``ON DELETE SET NULL`` such a CHECK would make chat
deletion fail. The pet service moves an orphaned pet home or puts it to sleep.

Revision ID: 0083_ai_pets
Revises: 0082_personal_ai
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_ai_pets"
down_revision: str | None = "0082_personal_ai"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Starting catalog; the owner edits prices and effects in the admin table editor afterwards.
_SEED_ITEMS = (
    ("dry_food", "Сухой корм", "food", 40, {"satiety": 15, "mood": 2, "xp": 2}, 1, 10),
    ("treat", "Лакомство", "food", 90, {"satiety": 10, "mood": 8, "affinity": 1, "xp": 3}, 1, 20),
    ("hearty_meal", "Сытный обед", "food", 160, {"satiety": 35, "mood": 5, "xp": 4}, 1, 30),
    ("ball", "Мячик", "toy", 120, {"mood": 10, "affinity": 2, "xp": 5}, 1, 40),
    ("plush", "Плюшевая игрушка", "toy", 250, {"mood": 18, "affinity": 3, "xp": 8}, 3, 50),
    ("puzzle", "Головоломка", "toy", 400, {"mood": 15, "affinity": 2, "xp": 15}, 5, 60),
)


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("pets_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "ai_pets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "home_chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "current_chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("species_key", sa.String(32), nullable=False),
        sa.Column("species_custom", sa.String(40), nullable=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("name_norm", sa.String(32), nullable=False),
        sa.Column("traits", sa.JSON(), nullable=False),
        sa.Column("character_custom", sa.String(300), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("xp", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mood", sa.SmallInteger(), nullable=False, server_default="70"),
        sa.Column("satiety", sa.SmallInteger(), nullable=False, server_default="70"),
        sa.Column("energy", sa.SmallInteger(), nullable=False, server_default="70"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("dormant_reason", sa.String(32), nullable=True),
        sa.Column("travel_unlocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("level >= 1", name="ck_ai_pets_level"),
        sa.CheckConstraint("xp >= 0", name="ck_ai_pets_xp"),
        sa.CheckConstraint("mood BETWEEN 0 AND 100", name="ck_ai_pets_mood"),
        sa.CheckConstraint("satiety BETWEEN 0 AND 100", name="ck_ai_pets_satiety"),
        sa.CheckConstraint("energy BETWEEN 0 AND 100", name="ck_ai_pets_energy"),
        sa.CheckConstraint("status IN ('active', 'dormant', 'released')", name="ck_ai_pets_status"),
    )
    op.create_index(
        "uq_ai_pets_owner_alive",
        "ai_pets",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'released'"),
        sqlite_where=sa.text("status <> 'released'"),
    )
    op.create_index(
        "uq_ai_pets_chat_name_active",
        "ai_pets",
        ["current_chat_id", "name_norm"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ai_pet_relationships",
        sa.Column("pet_id", sa.BigInteger(), sa.ForeignKey("ai_pets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("affinity", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("interactions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("affinity_gained_today", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("xp_gained_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gained_day", sa.Date(), nullable=True),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("affinity BETWEEN -100 AND 100", name="ck_ai_pet_relationships_affinity"),
    )

    op.create_table(
        "ai_pet_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.BigInteger(), sa.ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "actor_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("effects", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_pet_events_idempotency_key"),
    )
    op.create_index("idx_ai_pet_events_pet_created", "ai_pet_events", ["pet_id", "created_at"])
    op.create_index(
        "idx_ai_pet_events_pet_actor_type", "ai_pet_events", ["pet_id", "actor_user_id", "event_type", "created_at"]
    )

    items = op.create_table(
        "ai_pet_items",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("effects", sa.JSON(), nullable=False),
        sa.Column("min_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('food', 'toy')", name="ck_ai_pet_items_kind"),
        sa.CheckConstraint("price >= 0", name="ck_ai_pet_items_price"),
        sa.CheckConstraint("min_level >= 1", name="ck_ai_pet_items_min_level"),
    )
    op.bulk_insert(
        items,
        [
            {
                "code": code,
                "title": title,
                "kind": kind,
                "price": price,
                "effects": effects,
                "min_level": min_level,
                "enabled": True,
                "sort_order": sort_order,
            }
            for code, title, kind, price, effects, min_level, sort_order in _SEED_ITEMS
        ],
    )


def downgrade() -> None:
    op.drop_table("ai_pet_items")
    op.drop_index("idx_ai_pet_events_pet_actor_type", table_name="ai_pet_events")
    op.drop_index("idx_ai_pet_events_pet_created", table_name="ai_pet_events")
    op.drop_table("ai_pet_events")
    op.drop_table("ai_pet_relationships")
    op.drop_index("uq_ai_pets_chat_name_active", table_name="ai_pets")
    op.drop_index("uq_ai_pets_owner_alive", table_name="ai_pets")
    op.drop_table("ai_pets")
    op.drop_column("chat_settings", "pets_enabled")
