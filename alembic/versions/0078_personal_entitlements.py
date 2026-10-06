"""add user-scoped Selara Personal entitlements and a target scope for Stars purchases

Revision ID: 0078_personal_entitlements
Revises: 0077_selara_ai_payment_refunds
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_personal_entitlements"
down_revision: str | None = "0077_selara_ai_payment_refunds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INTENTS = "selara_ai_purchase_intents"
_PAYMENTS = "selara_ai_payments"


def upgrade() -> None:
    op.create_table(
        "user_entitlements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_user_entitlements_status"),
        sa.CheckConstraint("valid_from < valid_until", name="ck_user_entitlements_validity"),
        sa.CheckConstraint("product_key IN ('selara_personal_monthly')", name="ck_user_entitlements_product"),
        sa.UniqueConstraint("user_id", "product_key", name="uq_user_entitlements_user_product"),
    )
    op.create_index(
        "idx_user_entitlements_active_until",
        "user_entitlements",
        ["product_key", "status", "valid_until"],
    )

    # ck_chat_entitlements_product is deliberately left alone: a personal product
    # must never be storable as a chat entitlement, even by a misrouted payment.

    op.add_column(_INTENTS, sa.Column("target_scope", sa.String(length=8), server_default="chat", nullable=False))
    op.add_column(_INTENTS, sa.Column("target_user_id", sa.BigInteger(), nullable=True))
    op.alter_column(_INTENTS, "source_chat_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column(_INTENTS, "chat_id", existing_type=sa.BigInteger(), nullable=True)
    op.drop_constraint("ck_selara_ai_purchase_intents_product", _INTENTS, type_="check")
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_product",
        _INTENTS,
        "product_key IN ('selara_ai_monthly', 'selara_personal_monthly')",
    )
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_target_scope", _INTENTS, "target_scope IN ('chat', 'user')"
    )
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_target_shape",
        _INTENTS,
        "(target_scope = 'chat' AND chat_id IS NOT NULL AND target_user_id IS NULL) OR "
        "(target_scope = 'user' AND target_user_id IS NOT NULL AND chat_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_scope_product",
        _INTENTS,
        "(target_scope = 'chat' AND product_key = 'selara_ai_monthly') OR "
        "(target_scope = 'user' AND product_key = 'selara_personal_monthly')",
    )
    # Gifts are off in the MVP; the future gifts PR only has to drop this constraint.
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_personal_self_only",
        _INTENTS,
        "target_scope <> 'user' OR target_user_id = buyer_user_id",
    )
    op.create_index("idx_selara_ai_purchase_intents_target_user", _INTENTS, ["target_user_id", "status"])

    op.add_column(_PAYMENTS, sa.Column("target_scope", sa.String(length=8), server_default="chat", nullable=False))
    op.add_column(_PAYMENTS, sa.Column("target_user_id", sa.BigInteger(), nullable=True))
    op.drop_constraint("ck_selara_ai_payments_product", _PAYMENTS, type_="check")
    op.create_check_constraint(
        "ck_selara_ai_payments_product",
        _PAYMENTS,
        "product_key IS NULL OR product_key IN ('selara_ai_monthly', 'selara_personal_monthly')",
    )
    op.create_check_constraint(
        "ck_selara_ai_payments_target_scope", _PAYMENTS, "target_scope IN ('chat', 'user')"
    )
    op.create_check_constraint(
        "ck_selara_ai_payments_target_shape",
        _PAYMENTS,
        "(target_scope = 'chat' AND target_user_id IS NULL) OR "
        "(target_scope = 'user' AND target_user_id IS NOT NULL AND target_chat_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_selara_ai_payments_scope_product",
        _PAYMENTS,
        "product_key IS NULL OR "
        "(target_scope = 'chat' AND product_key = 'selara_ai_monthly') OR "
        "(target_scope = 'user' AND product_key = 'selara_personal_monthly')",
    )
    op.create_index("idx_selara_ai_payments_target_user_time", _PAYMENTS, ["target_user_id", "payment_at"])


def _count(bind, statement: str) -> int:
    return int(bind.execute(sa.text(statement)).scalar() or 0)


def downgrade() -> None:
    bind = op.get_bind()
    # Never lose a personal payment or subscription silently.
    if (
        _count(bind, f"SELECT count(*) FROM {_INTENTS} WHERE target_scope = 'user'")
        or _count(bind, f"SELECT count(*) FROM {_PAYMENTS} WHERE target_scope = 'user'")
        or _count(bind, "SELECT count(*) FROM user_entitlements")
    ):
        raise RuntimeError(
            "Cannot downgrade 0078_personal_entitlements: personal purchases or entitlements exist. "
            "Resolve them manually first; refusing to drop paid data."
        )

    op.drop_index("idx_selara_ai_payments_target_user_time", table_name=_PAYMENTS)
    op.drop_constraint("ck_selara_ai_payments_scope_product", _PAYMENTS, type_="check")
    op.drop_constraint("ck_selara_ai_payments_target_shape", _PAYMENTS, type_="check")
    op.drop_constraint("ck_selara_ai_payments_target_scope", _PAYMENTS, type_="check")
    op.drop_constraint("ck_selara_ai_payments_product", _PAYMENTS, type_="check")
    op.create_check_constraint(
        "ck_selara_ai_payments_product", _PAYMENTS, "product_key IS NULL OR product_key IN ('selara_ai_monthly')"
    )
    op.drop_column(_PAYMENTS, "target_user_id")
    op.drop_column(_PAYMENTS, "target_scope")

    op.drop_index("idx_selara_ai_purchase_intents_target_user", table_name=_INTENTS)
    op.drop_constraint("ck_selara_ai_purchase_intents_personal_self_only", _INTENTS, type_="check")
    op.drop_constraint("ck_selara_ai_purchase_intents_scope_product", _INTENTS, type_="check")
    op.drop_constraint("ck_selara_ai_purchase_intents_target_shape", _INTENTS, type_="check")
    op.drop_constraint("ck_selara_ai_purchase_intents_target_scope", _INTENTS, type_="check")
    op.drop_constraint("ck_selara_ai_purchase_intents_product", _INTENTS, type_="check")
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_product", _INTENTS, "product_key IN ('selara_ai_monthly')"
    )
    op.alter_column(_INTENTS, "chat_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column(_INTENTS, "source_chat_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column(_INTENTS, "target_user_id")
    op.drop_column(_INTENTS, "target_scope")

    op.drop_index("idx_user_entitlements_active_until", table_name="user_entitlements")
    op.drop_table("user_entitlements")
