"""AI pet inventory and cosmetics

``ai_pet_items`` gains the ``cosmetic`` kind and a ``slot`` for it; owned
items live in ``ai_pet_inventory`` (per pet, removed with the pet). Starting
cosmetics are seeded; prices stay editable in the admin table editor.

Revision ID: 0087_ai_pet_inventory
Revises: 0086_ai_pet_events
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_ai_pet_inventory"
down_revision: str | None = "0086_ai_pet_events"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_SEED_COSMETICS = (
    ("bow", "Бантик", 150, "neck", 1, 70),
    ("top_hat", "Цилиндр", 300, "head", 1, 80),
    ("hero_cape", "Плащ героя", 500, "back", 5, 90),
    ("crown", "Корона", 1000, "head", 10, 100),
)


def upgrade() -> None:
    op.add_column("ai_pet_items", sa.Column("slot", sa.String(16), nullable=True))
    op.drop_constraint("ck_ai_pet_items_kind", "ai_pet_items", type_="check")
    op.create_check_constraint("ck_ai_pet_items_kind", "ai_pet_items", "kind IN ('food', 'toy', 'cosmetic')")
    op.create_check_constraint(
        "ck_ai_pet_items_slot",
        "ai_pet_items",
        "(kind = 'cosmetic' AND slot IN ('head', 'neck', 'back')) OR (kind <> 'cosmetic' AND slot IS NULL)",
    )

    op.create_table(
        "ai_pet_inventory",
        sa.Column("pet_id", sa.BigInteger(), sa.ForeignKey("ai_pets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "item_code", sa.String(32), sa.ForeignKey("ai_pet_items.code", ondelete="RESTRICT"), primary_key=True
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("equipped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity >= 0", name="ck_ai_pet_inventory_quantity"),
        sa.CheckConstraint("NOT equipped OR quantity > 0", name="ck_ai_pet_inventory_equipped_owned"),
    )

    items = sa.table(
        "ai_pet_items",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("kind", sa.String),
        sa.column("price", sa.BigInteger),
        sa.column("effects", sa.JSON),
        sa.column("min_level", sa.Integer),
        sa.column("enabled", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("slot", sa.String),
    )
    op.bulk_insert(
        items,
        [
            {
                "code": code, "title": title, "kind": "cosmetic", "price": price, "effects": {},
                "min_level": min_level, "enabled": True, "sort_order": sort_order, "slot": slot,
            }
            for code, title, price, slot, min_level, sort_order in _SEED_COSMETICS
        ],
    )


def downgrade() -> None:
    op.drop_table("ai_pet_inventory")
    # Cosmetics cannot exist under the old CHECK: every cosmetic row goes, including ones the admin added.
    op.execute("DELETE FROM ai_pet_items WHERE kind = 'cosmetic'")
    op.drop_constraint("ck_ai_pet_items_slot", "ai_pet_items", type_="check")
    op.drop_constraint("ck_ai_pet_items_kind", "ai_pet_items", type_="check")
    op.create_check_constraint("ck_ai_pet_items_kind", "ai_pet_items", "kind IN ('food', 'toy')")
    op.drop_column("ai_pet_items", "slot")
