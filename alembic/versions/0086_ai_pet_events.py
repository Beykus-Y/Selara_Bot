"""chat setting for spontaneous AI pet events

Travel needs no schema change: ``ai_pets.travel_unlocked`` and the per-chat
relationship/memory keys already exist; travels are journaled in ``ai_pet_events``.

Revision ID: 0086_ai_pet_events
Revises: 0085_model_catalog_router
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086_ai_pet_events"
down_revision: str | None = "0085_model_catalog_router"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("pets_spontaneous_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "pets_spontaneous_enabled")
