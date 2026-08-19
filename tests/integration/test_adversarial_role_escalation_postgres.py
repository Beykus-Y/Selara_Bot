"""Adversarial test: privilege escalation via /rolesetrank rank-ceiling bypass.

Hypothesis under test (now fixed)
----------------------------------
`role_add_command` (/roleadd) correctly enforces that a non-owner actor can only
assign a role whose rank is strictly lower than the actor's own rank
(see `_role_add_allowed` in selara/presentation/handlers/moderation.py).

`role_set_rank_command` (/rolesetrank) — which only requires the
`manage_role_templates` permission — calls
`SqlAlchemyActivityRepository.update_custom_role(..., rank=...)`. Previously
this method performed **no** check that the new rank stayed below the actor's
own rank, and no check that the actor already outranked the role being
edited. Custom (non-system) roles were always editable this way regardless of
who currently held them.

This meant a `co_owner` (system rank 30, below `owner`'s rank 40, holding
`PERM_MANAGE_ROLE_TEMPLATES` by default) could:

  1. Create a low-rank custom role and assign it to an accomplice account
     (allowed, since the *initial* rank is below the co_owner's own rank).
  2. Use /rolesetrank to raise that already-assigned custom role's rank to an
     arbitrary number — e.g. higher than the real owner's rank (40) — with no
     re-validation against the co_owner's own rank.
  3. The accomplice's *effective* role would then outrank the owner. Any check
     that compares ranks via `_can_manage_target`/`get_effective_role_definition`
     (e.g. kick/ban/demote authority over other admins) would treat the
     accomplice as senior to the true owner.

Fix
---
`update_custom_role` now accepts `actor_role_code`/`actor_rank` and, whenever
`rank` is being changed by a non-owner actor, enforces the same ceiling
`_role_add_allowed` already enforces for /roleadd: the new rank must stay
strictly below the actor's own rank, AND the role's *current* rank must
already be strictly below the actor's own rank (an actor may not touch a
role that already outranks them). The true `owner` remains exempt, mirroring
`_role_add_allowed`'s and `_can_manage_target`'s existing owner bypass.

This test reproduces steps 1-3 directly against the repository layer (the
same calls the handlers make) and shows the rank-escalation attempt is now
rejected, while legitimate operations (owner setting any rank; co_owner
setting a rank strictly below their own) still succeed.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers.moderation import _can_manage_target, _role_add_allowed


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_co_owner_cannot_escalate_accomplice_above_owner_via_rolesetrank() -> None:
    engine, session_factory = await _database()
    chat_id = -1001

    chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="Test chat")
    owner_user = UserSnapshot(telegram_user_id=1, username="owner", first_name="Owner", last_name=None, is_bot=False)
    co_owner_user = UserSnapshot(telegram_user_id=2, username="deputy", first_name="Deputy", last_name=None, is_bot=False)
    accomplice_user = UserSnapshot(telegram_user_id=3, username="alt", first_name="Alt", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.ensure_chat_role_templates(chat=chat)

        # Owner assigns themself owner, and grants a trusted deputy co_owner.
        await repo.set_bot_role(chat=chat, target=owner_user, role="owner", assigned_by_user_id=owner_user.telegram_user_id)
        await repo.set_bot_role(chat=chat, target=co_owner_user, role="co_owner", assigned_by_user_id=owner_user.telegram_user_id)
        await session.commit()

        owner_role = await repo.get_effective_role_definition(chat_id=chat_id, user_id=owner_user.telegram_user_id)
        co_owner_role = await repo.get_effective_role_definition(chat_id=chat_id, user_id=co_owner_user.telegram_user_id)
        assert owner_role.rank == 40
        assert co_owner_role.rank == 30

        # Step 1: co_owner (acting via /rolecreate handler logic) creates a
        # low-rank custom role and assigns it to an accomplice account.
        # This mirrors role_create_command + role_add_command exactly.
        created = await repo.create_custom_role_from_template(
            chat=chat,
            title_ru="Deputy Helper",
            template_token="junior_admin",
            rank=5,  # deliberately below co_owner's rank(30) so /roleadd allows it
        )
        assert created.rank == 5

        current_target_definition = await repo.get_effective_role_definition(
            chat_id=chat_id, user_id=accomplice_user.telegram_user_id
        )
        assert _role_add_allowed(
            actor_role_code=co_owner_role.role_code,
            actor_rank=co_owner_role.rank,
            target_current_rank=current_target_definition.rank,
            target_new_rank=created.rank,
        )  # role_add_command's own guard permits this initial low-rank grant

        await repo.set_bot_role(
            chat=chat, target=accomplice_user, role=created.role_code, assigned_by_user_id=co_owner_user.telegram_user_id
        )
        await session.commit()

        accomplice_role_before = await repo.get_effective_role_definition(
            chat_id=chat_id, user_id=accomplice_user.telegram_user_id
        )
        assert accomplice_role_before.rank == 5
        # Owner can still manage the accomplice at this point (as expected).
        assert _can_manage_target(actor_role_code="owner", actor_rank=owner_role.rank, target_rank=accomplice_role_before.rank)

        # Step 2: co_owner now attempts /rolesetrank on the ALREADY-ASSIGNED
        # custom role, exactly as role_set_rank_command does, passing its own
        # actor role/rank context (as the fixed handler now does). The
        # repository must reject this: 9999 is >= the co_owner's own rank(30).
        with pytest.raises(ValueError):
            await repo.update_custom_role(
                chat_id=chat_id,
                role_token=created.role_code,
                rank=9999,
                actor_role_code=co_owner_role.role_code,
                actor_rank=co_owner_role.rank,
            )
        await session.rollback()

        # The role's rank, and therefore the accomplice's effective rank,
        # must be unchanged -- the escalation attempt had no effect.
        unchanged_role = await repo.resolve_chat_role_definition(chat_id=chat_id, token=created.role_code)
        assert unchanged_role.rank == 5

        accomplice_role_after = await repo.get_effective_role_definition(
            chat_id=chat_id, user_id=accomplice_user.telegram_user_id
        )
        assert accomplice_role_after.rank == 5
        assert accomplice_role_after.rank < owner_role.rank
        assert not _can_manage_target(
            actor_role_code=accomplice_role_after.role_code,
            actor_rank=accomplice_role_after.rank,
            target_rank=owner_role.rank,
        ), "accomplice must NOT be able to manage the true owner"

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_co_owner_can_still_set_rank_strictly_below_own_rank() -> None:
    """A legitimate rank edit -- staying below the actor's own rank -- must still work."""
    engine, session_factory = await _database()
    chat_id = -1002

    chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="Test chat")
    owner_user = UserSnapshot(telegram_user_id=1, username="owner", first_name="Owner", last_name=None, is_bot=False)
    co_owner_user = UserSnapshot(telegram_user_id=2, username="deputy", first_name="Deputy", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.ensure_chat_role_templates(chat=chat)
        await repo.set_bot_role(chat=chat, target=owner_user, role="owner", assigned_by_user_id=owner_user.telegram_user_id)
        await repo.set_bot_role(chat=chat, target=co_owner_user, role="co_owner", assigned_by_user_id=owner_user.telegram_user_id)
        await session.commit()

        co_owner_role = await repo.get_effective_role_definition(chat_id=chat_id, user_id=co_owner_user.telegram_user_id)
        assert co_owner_role.rank == 30

        created = await repo.create_custom_role_from_template(
            chat=chat, title_ru="Deputy Helper", template_token="junior_admin", rank=5
        )
        await session.commit()

        updated = await repo.update_custom_role(
            chat_id=chat_id,
            role_token=created.role_code,
            rank=20,  # still strictly below co_owner's own rank(30)
            actor_role_code=co_owner_role.role_code,
            actor_rank=co_owner_role.rank,
        )
        await session.commit()
        assert updated.rank == 20

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_can_set_any_rank_via_rolesetrank() -> None:
    """The true owner remains exempt from the rank ceiling, mirroring /roleadd."""
    engine, session_factory = await _database()
    chat_id = -1003

    chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="Test chat")
    owner_user = UserSnapshot(telegram_user_id=1, username="owner", first_name="Owner", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.ensure_chat_role_templates(chat=chat)
        await repo.set_bot_role(chat=chat, target=owner_user, role="owner", assigned_by_user_id=owner_user.telegram_user_id)
        await session.commit()

        owner_role = await repo.get_effective_role_definition(chat_id=chat_id, user_id=owner_user.telegram_user_id)
        assert owner_role.rank == 40

        created = await repo.create_custom_role_from_template(
            chat=chat, title_ru="Deputy Helper", template_token="junior_admin", rank=5
        )
        await session.commit()

        updated = await repo.update_custom_role(
            chat_id=chat_id,
            role_token=created.role_code,
            rank=9999,
            actor_role_code=owner_role.role_code,
            actor_rank=owner_role.rank,
        )
        await session.commit()
        assert updated.rank == 9999

    await engine.dispose()
