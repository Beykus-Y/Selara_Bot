"""Unit tests run without a database, so the durable AI turn lease is granted and handlers behave as before."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest


class _GrantedTurnLease:
    """Stands in for a lease the test holds: every checkpoint passes and no loss is ever reported."""

    lost = False

    async def confirm(self) -> None:
        return None


@asynccontextmanager
async def _granted_turn_lease(**_kwargs) -> AsyncIterator[_GrantedTurnLease]:
    yield _GrantedTurnLease()


@pytest.fixture(autouse=True)
def _grant_ai_turn_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    from selara.presentation.handlers import group_character, llm_admin, personal_ai

    monkeypatch.setattr(personal_ai, "ai_turn_lease", _granted_turn_lease)
    monkeypatch.setattr(llm_admin, "ai_turn_lease", _granted_turn_lease)
    monkeypatch.setattr(group_character, "ai_turn_lease", _granted_turn_lease)
