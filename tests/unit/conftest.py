"""Unit tests run without a database, so the durable AI turn lease is granted and handlers behave as before."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest


@asynccontextmanager
async def _granted_turn_lease(**_kwargs) -> AsyncIterator[bool]:
    yield True


@pytest.fixture(autouse=True)
def _grant_ai_turn_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    from selara.presentation.handlers import llm_admin, personal_ai

    monkeypatch.setattr(personal_ai, "ai_turn_lease", _granted_turn_lease)
    monkeypatch.setattr(llm_admin, "ai_turn_lease", _granted_turn_lease)
