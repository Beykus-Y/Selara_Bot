import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.exc import IntegrityError

from selara.core.config import Settings
from selara.presentation.handlers.feedback import feedback_command
from selara.presentation.middlewares.db_session import DBSessionMiddleware
from selara.infrastructure.db.models import UserFeatureRequestModel, UserModel


class _Session:
    def __init__(self) -> None:
        self.user = None
        self.rows = []
        self.statements = []

    async def get(self, _model, _user_id):
        return self.user

    def add(self, row) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        self.rows[-1].id = 42

    async def commit(self) -> None:
        return None

    async def execute(self, statement) -> None:
        self.statements.append(statement)


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

    (request,) = session.rows
    assert request.user_id == 123
    assert request.title == "Предложение: быстрые ответы"
    assert request.details == "предложение: быстрые ответы"
    message.answer.assert_awaited_once()
    assert "#42" in message.answer.await_args.args[0]
    assert len(session.statements) == 1
    statement = session.statements[0].compile(dialect=postgresql_dialect())
    assert "ON CONFLICT (telegram_user_id) DO UPDATE" in str(statement)


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


@pytest.mark.asyncio
async def test_simultaneous_first_feedback_from_same_user_does_not_conflict() -> None:
    users: set[int] = set()
    requests: list[UserFeatureRequestModel] = []
    waiting_for_user_lookup = 0
    both_lookups_finished = asyncio.Event()
    next_request_id = 0
    request_id_lock = asyncio.Lock()

    class _ConcurrentSession:
        async def get(self, model, _user_id):
            nonlocal waiting_for_user_lookup
            assert model is UserModel
            waiting_for_user_lookup += 1
            if waiting_for_user_lookup == 2:
                both_lookups_finished.set()
            await both_lookups_finished.wait()
            return None

        def add(self, row) -> None:
            if isinstance(row, UserModel):
                if row.telegram_user_id in users:
                    raise IntegrityError("INSERT users", {}, RuntimeError("duplicate key"))
                users.add(row.telegram_user_id)
                return
            requests.append(row)

        async def execute(self, statement) -> None:
            compiled = statement.compile(dialect=postgresql_dialect())
            assert "ON CONFLICT (telegram_user_id) DO UPDATE" in str(compiled)
            users.add(321)

        async def flush(self) -> None:
            nonlocal next_request_id
            async with request_id_lock:
                next_request_id += 1
                requests[-1].id = next_request_id

        async def commit(self) -> None:
            return None

    user = SimpleNamespace(id=321, username="new-user", first_name="Новый", last_name=None, is_bot=False)
    messages = [
        SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=user,
            answer=AsyncMock(),
        )
        for _ in range(2)
    ]

    await asyncio.gather(
        *(
            feedback_command(
                message,
                SimpleNamespace(args=f"проблема: обращение {index}"),
                _ConcurrentSession(),
            )
            for index, message in enumerate(messages)
        )
    )

    assert users == {321}
    assert len(requests) == 2
    assert all(message.answer.await_count == 1 for message in messages)
