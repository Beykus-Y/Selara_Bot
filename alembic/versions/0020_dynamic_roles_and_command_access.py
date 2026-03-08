"""add dynamic roles and command access rules

Revision ID: 0020_dynamic_roles
Revises: 0019_activity_minute_week_start
Create Date: 2026-02-22 20:05:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_dynamic_roles"
down_revision: str | None = "0019_activity_minute_week_start"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _system_roles_payload() -> tuple[tuple[str, str, int, list[str], bool, str], ...]:
    return (
        ("participant", "Участник", 0, [], True, "participant"),
        ("junior_admin", "Мл. админ", 10, ["announce"], True, "junior_admin"),
        ("senior_admin", "Старший админ", 20, ["announce", "manage_games", "moderate_users"], True, "senior_admin"),
        (
            "co_owner",
            "Совладелец",
            30,
            [
                "announce",
                "manage_games",
                "moderate_users",
                "manage_settings",
                "manage_roles",
                "manage_command_access",
                "manage_role_templates",
            ],
            True,
            "co_owner",
        ),
        (
            "owner",
            "Владелец",
            40,
            [
                "announce",
                "manage_games",
                "moderate_users",
                "manage_settings",
                "manage_roles",
                "manage_command_access",
                "manage_role_templates",
            ],
            True,
            "owner",
        ),
    )


def upgrade() -> None:
    op.create_table(
        "chat_role_definitions",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("title_ru", sa.String(length=128), nullable=False),
        sa.Column("rank", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("template_key", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "role_code"),
    )
    op.create_index(
        "idx_chat_role_definitions_chat_rank",
        "chat_role_definitions",
        ["chat_id", "rank"],
        unique=False,
    )

    op.create_table(
        "chat_command_access_rules",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("command_key", sa.String(length=64), nullable=False),
        sa.Column("min_role_code", sa.String(length=64), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("chat_id", "command_key"),
    )
    op.create_index(
        "idx_chat_command_access_rules_chat_role",
        "chat_command_access_rules",
        ["chat_id", "min_role_code"],
        unique=False,
    )

    with op.batch_alter_table("user_chat_bot_roles") as batch:
        batch.drop_constraint("ck_user_chat_bot_roles_role", type_="check")
        batch.alter_column(
            "role",
            existing_type=sa.String(length=16),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='junior_admin' WHERE role='helper'"))
    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='senior_admin' WHERE role='moderator'"))
    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='co_owner' WHERE role='admin'"))

    bind = op.get_bind()
    chat_ids = [int(row[0]) for row in bind.execute(sa.text("SELECT telegram_chat_id FROM chats")).fetchall()]
    role_table = sa.table(
        "chat_role_definitions",
        sa.column("chat_id", sa.BigInteger()),
        sa.column("role_code", sa.String(length=64)),
        sa.column("title_ru", sa.String(length=128)),
        sa.column("rank", sa.BigInteger()),
        sa.column("permissions", sa.JSON()),
        sa.column("is_system", sa.Boolean()),
        sa.column("template_key", sa.String(length=64)),
    )
    for chat_id in chat_ids:
        for role_code, title_ru, rank, permissions, is_system, template_key in _system_roles_payload():
            exists = bind.execute(
                sa.text(
                    "SELECT 1 FROM chat_role_definitions "
                    "WHERE chat_id=:chat_id AND role_code=:role_code LIMIT 1"
                ),
                {"chat_id": chat_id, "role_code": role_code},
            ).scalar_one_or_none()
            if exists is not None:
                continue
            bind.execute(
                role_table.insert().values(
                    chat_id=chat_id,
                    role_code=role_code,
                    title_ru=title_ru,
                    rank=rank,
                    permissions=permissions,
                    is_system=is_system,
                    template_key=template_key,
                )
            )


def downgrade() -> None:
    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='helper' WHERE role='junior_admin'"))
    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='moderator' WHERE role='senior_admin'"))
    op.execute(sa.text("UPDATE user_chat_bot_roles SET role='admin' WHERE role='co_owner'"))

    with op.batch_alter_table("user_chat_bot_roles") as batch:
        batch.alter_column(
            "role",
            existing_type=sa.String(length=64),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "ck_user_chat_bot_roles_role",
            "role IN ('owner', 'admin', 'moderator', 'helper')",
        )

    op.drop_index("idx_chat_command_access_rules_chat_role", table_name="chat_command_access_rules")
    op.drop_table("chat_command_access_rules")

    op.drop_index("idx_chat_role_definitions_chat_rank", table_name="chat_role_definitions")
    op.drop_table("chat_role_definitions")
