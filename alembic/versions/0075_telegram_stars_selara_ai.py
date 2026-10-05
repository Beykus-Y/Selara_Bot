"""add Telegram Stars checkout and chat-scoped Selara AI entitlements

Revision ID: 0075_telegram_stars_selara_ai
Revises: 0074_quota_origin_provider_start
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_telegram_stars_selara_ai"
down_revision: str | None = "0074_quota_origin_provider_start"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_entitlements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("product_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_chat_entitlements_status"),
        sa.CheckConstraint("valid_from < valid_until", name="ck_chat_entitlements_validity"),
        sa.CheckConstraint("product_key IN ('selara_ai_monthly')", name="ck_chat_entitlements_product"),
        sa.UniqueConstraint("chat_id", "product_key", name="uq_chat_entitlements_chat_product"),
    )
    op.create_index(
        "idx_chat_entitlements_active_until",
        "chat_entitlements",
        ["product_key", "status", "valid_until"],
    )

    op.create_table(
        "selara_ai_purchase_intents",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("buyer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title", sa.Text(), nullable=True),
        sa.Column("product_key", sa.String(length=64), nullable=False),
        sa.Column("amount_stars", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("invoice_payload", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invoice_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pre_checkout_query_id", sa.String(length=128), nullable=True),
        sa.Column("pre_checkout_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("product_key IN ('selara_ai_monthly')", name="ck_selara_ai_purchase_intents_product"),
        sa.CheckConstraint("amount_stars > 0", name="ck_selara_ai_purchase_intents_amount"),
        sa.CheckConstraint("duration_seconds > 0", name="ck_selara_ai_purchase_intents_duration"),
        sa.CheckConstraint(
            "status IN ('open', 'checkout_accepted', 'consumed')",
            name="ck_selara_ai_purchase_intents_status",
        ),
        sa.CheckConstraint("currency = 'XTR'", name="ck_selara_ai_purchase_intents_currency"),
        sa.UniqueConstraint("invoice_payload", name="uq_selara_ai_purchase_intents_invoice_payload"),
    )
    op.create_index(
        "idx_selara_ai_purchase_intents_buyer_created",
        "selara_ai_purchase_intents",
        ["buyer_user_id", "created_at"],
    )
    op.create_index(
        "idx_selara_ai_purchase_intents_chat",
        "selara_ai_purchase_intents",
        ["chat_id", "status"],
    )

    op.create_table(
        "selara_ai_payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(length=255), nullable=False),
        sa.Column("provider_payment_charge_id", sa.String(length=255), nullable=True),
        sa.Column("invoice_payload", sa.Text(), nullable=False),
        sa.Column("purchase_intent_id", sa.String(length=36), nullable=True),
        sa.Column("buyer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("product_key", sa.String(length=64), nullable=True),
        sa.Column("amount_stars", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_state", sa.String(length=16), nullable=False),
        sa.Column("processing_reason", sa.String(length=48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_stars >= 0", name="ck_selara_ai_payments_nonnegative_amount"),
        sa.CheckConstraint("processing_state IN ('applied', 'rejected')", name="ck_selara_ai_payments_state"),
        sa.CheckConstraint(
            "product_key IS NULL OR product_key IN ('selara_ai_monthly')",
            name="ck_selara_ai_payments_product",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_intent_id"], ["selara_ai_purchase_intents.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("telegram_payment_charge_id", name="uq_selara_ai_payments_telegram_charge"),
    )
    op.create_index(
        "idx_selara_ai_payments_target_time", "selara_ai_payments", ["target_chat_id", "payment_at"]
    )
    op.create_index(
        "idx_selara_ai_payments_buyer_time", "selara_ai_payments", ["buyer_user_id", "payment_at"]
    )
    op.create_index(
        "idx_selara_ai_payments_state_time", "selara_ai_payments", ["processing_state", "payment_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_selara_ai_payments_state_time", table_name="selara_ai_payments")
    op.drop_index("idx_selara_ai_payments_buyer_time", table_name="selara_ai_payments")
    op.drop_index("idx_selara_ai_payments_target_time", table_name="selara_ai_payments")
    op.drop_table("selara_ai_payments")
    op.drop_index("idx_selara_ai_purchase_intents_chat", table_name="selara_ai_purchase_intents")
    op.drop_index("idx_selara_ai_purchase_intents_buyer_created", table_name="selara_ai_purchase_intents")
    op.drop_table("selara_ai_purchase_intents")
    op.drop_index("idx_chat_entitlements_active_until", table_name="chat_entitlements")
    op.drop_table("chat_entitlements")
