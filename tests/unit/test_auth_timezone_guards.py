from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from selara.infrastructure.db.admin_auth import SqlAlchemyAdminAuthRepository
from selara.infrastructure.db.models import AdminSessionModel, UserModel, WebSessionModel
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository


class _FakeSession:
    def __init__(self, records: dict[tuple[type, object], object]) -> None:
        self._records = records
        self.flush_calls = 0

    async def get(self, model_class, record_id):
        return self._records.get((model_class, record_id))

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_admin_auth_accepts_naive_expires_at_from_storage() -> None:
    now = datetime.now(timezone.utc)
    session_row = SimpleNamespace(
        admin_user_id=77,
        revoked_at=None,
        expires_at=(now + timedelta(hours=1)).replace(tzinfo=None),
        last_seen_at=None,
    )
    session = _FakeSession({(AdminSessionModel, "admin-session"): session_row})
    repo = SqlAlchemyAdminAuthRepository(session)

    admin_user_id = await repo.get_admin_by_session(
        session_token="admin-session",
        now=now,
        touch=True,
    )

    assert admin_user_id == 77
    assert session_row.last_seen_at == now
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_web_auth_accepts_naive_expires_at_from_storage() -> None:
    now = datetime.now(timezone.utc)
    session_row = SimpleNamespace(
        user_id=501,
        revoked_at=None,
        expires_at=(now + timedelta(hours=1)).replace(tzinfo=None),
        last_seen_at=None,
    )
    user_row = UserModel(
        telegram_user_id=501,
        username="viewer",
        first_name="View",
        last_name="Er",
        is_bot=False,
    )
    session = _FakeSession(
        {
            (WebSessionModel, "viewer-session"): session_row,
            (UserModel, 501): user_row,
        }
    )
    repo = SqlAlchemyWebAuthRepository(session)

    user = await repo.get_user_by_session(
        session_digest="viewer-session",
        now=now,
        touch=True,
    )

    assert user is not None
    assert user.telegram_user_id == 501
    assert user.username == "viewer"
    assert session_row.last_seen_at == now
    assert session.flush_calls == 1
