from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.config import Settings
from selara.presentation.handlers.feedback import feedback_command
from selara.presentation.middlewares.db_session import DBSessionMiddleware


class _Session:
    def __init__(self) -> None:
        self.user = None
        self.rows = []

    async def get(self, _model, _user_id):
        return self.user

    def add(self, row) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        self.rows[-1].id = 42

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_feedback_command_records_suggestion_for_admin_queue() -> None:
    user = SimpleNamespace(id=123, username="ilya", first_name="Илья", last_name=None, is_bot=False)
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=user,
        answer=AsyncMock(),
    )
    session = _Session()

    await feedback_command(message, SimpleNamespace(args="предложение: быстрые ответы"), session)

    stored_user, request = session.rows
    assert stored_user.telegram_user_id == 123
    assert request.user_id == 123
    assert request.title == "Предложение: быстрые ответы"
    assert request.details == "предложение: быстрые ответы"
    message.answer.assert_awaited_once()
    assert "#42" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_feedback_command_rejects_group_submission() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(type="group"),
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )

    await feedback_command(message, SimpleNamespace(args="предложение: текст"), _Session())

    message.answer.assert_awaited_once()
    assert "/feedback" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_feedback_does_not_confirm_before_transaction_commit() -> None:
    class _CommitFailingSession(_Session):
        async def commit(self) -> None:
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            return None

    class _SessionContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    session = _CommitFailingSession()
    middleware = DBSessionMiddleware(lambda: _SessionContext(session))
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123, username="ilya", first_name="Илья", last_name=None, is_bot=False),
        answer=AsyncMock(),
    )

    async def handler(event, data):
        await feedback_command(event, SimpleNamespace(args="проблема: тест"), data["db_session"])

    with pytest.raises(RuntimeError, match="commit failed"):
        await middleware(
            handler,
            message,
            {"settings": Settings(bot_token="token", database_url="postgresql://localhost/db")},
        )

    assert all("отправлено" not in call.args[0].lower() for call in message.answer.await_args_list)
