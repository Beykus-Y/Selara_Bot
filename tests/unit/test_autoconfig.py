from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.autoconfig import changes, rebase_draft, review_text, run_assistant, update_draft
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.infrastructure.db.autoconfig_repository import AutoConfigRepository, now
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import AutoConfigSessionModel, ChatModel, UserModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers.autoconfig import _data, action, can_configure, keyboard, talk


def defaults():
    from selara.presentation.handlers.settings_common import settings_to_dict
    return settings_to_dict(default_chat_settings(Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')))


def response(*calls, content='Ответ'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[
        SimpleNamespace(id=str(i), function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)]))])


def client(*responses):
    return SimpleNamespace(last_model='test', last_usage=(100, 20), chat_with_tools=AsyncMock(side_effect=list(responses)))


async def run(c, draft=None):
    values = defaults()
    return await run_assistant(client=c, text='Давай завершим', baseline=values, draft=draft or values,
        defaults=values, touched=[], history=[])


def test_related_settings_update_is_atomic():
    values = defaults()
    result = update_draft(values, {'leaderboard_hybrid_karma_weight': '0.3', 'leaderboard_hybrid_activity_weight': '0.7'}, values)
    assert result['leaderboard_hybrid_karma_weight'] == 0.3
    with pytest.raises(ValueError):
        update_draft(values, {'top_limit_default': '100', 'top_limit_max': '10'}, values)
    assert values == defaults()


@pytest.mark.parametrize('updates', [{'bot_token': 'secret'}, {'chat_id': '2'}, {'title_price': 'nan'},
    {'vote_daily_limit': '9999999999999999999999'}, {'welcome_text': '<x>' * 500}, {'llm_enabled': True}])
def test_rejects_unknown_keys_invalid_types_and_unbounded_values(updates):
    with pytest.raises(ValueError):
        update_draft(defaults(), updates, defaults())


async def test_finish_stops_all_subsequent_tools_and_never_saves():
    c = client(response(('update_settings_draft', {'updates': {'economy_enabled': 'false'}}),
        ('finish_configuration', {}), ('update_settings_draft', {'updates': {'economy_enabled': 'true'}})))
    result = await run(c)
    assert result.finished and result.draft['economy_enabled'] is False
    assert c.chat_with_tools.call_count == 1
    assert {t['function']['name'] for t in c.chat_with_tools.call_args.kwargs['tools']} == {
        'read_settings', 'update_settings_draft', 'finish_configuration'}


async def test_model_cannot_use_live_registry_or_cross_chat_arguments():
    c = client(response(('ban_user', {'chat_id': 999})), response(('update_settings_draft',
        {'updates': {'economy_enabled': 'false'}, 'chat_id': 999})), response(content='Уточни'))
    result = await run(c)
    assert result.draft == defaults() and not result.finished
    errors = [json.loads(m['content']).get('error', '') for m in c.chat_with_tools.call_args.kwargs['messages'] if m['role'] == 'tool']
    assert any('Инструмент недоступен' in e for e in errors)
    assert any('Ожидается только updates' in e for e in errors)


async def test_llm_error_rolls_back_this_turn_not_previous_turns():
    prior = {**defaults(), 'economy_enabled': False}
    c = client(response(('update_settings_draft', {'updates': {'welcome_enabled': 'true'}})))
    result = await run(c, prior)
    assert result.draft == prior and not result.finished
    assert 'недоступен' in result.answer


async def test_read_tracks_inspected_fields_and_loop_is_bounded():
    c = client(*[response(('read_settings', {'keys': ['economy_enabled']}))] * 4)
    result = await run(c)
    assert result.touched == ['economy_enabled'] and c.chat_with_tools.call_count == 4


def test_review_escapes_user_content_and_lists_changed_and_inspected():
    baseline = defaults()
    draft = {**baseline, 'welcome_text': '<script>bad</script>', 'economy_enabled': False}
    text = review_text(title='<evil>', baseline=baseline, draft=draft, touched=['top_limit_max'])
    assert '<script>' not in text and '&lt;evil&gt;' in text
    assert '→' in text and 'Просмотрены без изменения' in text and 'Затронутые разделы' in text


def test_rebase_preserves_unrelated_live_change_and_detects_dependencies():
    baseline = defaults()
    draft = {**baseline, 'welcome_enabled': True}
    live = {**baseline, 'economy_enabled': False}
    assert rebase_draft(baseline, draft, live) == {**live, 'welcome_enabled': True}
    draft = {**baseline, 'top_limit_default': 20}
    live = {**baseline, 'top_limit_max': 10}
    with pytest.raises(ValueError):
        rebase_draft(baseline, draft, live)


@pytest.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([ChatModel(telegram_chat_id=-1001, type='supergroup', title='Test'),
            UserModel(telegram_user_id=1, is_bot=False)])
        await session.commit()
        yield session
    await engine.dispose()


async def make_draft(db, state='review'):
    repo = AutoConfigRepository(db)
    row = await repo.start(1, [{'id': -1001, 'title': 'Test', 'type': 'supergroup'}])
    row.chat_id = -1001
    row.baseline = defaults()
    row.draft = {**defaults(), 'welcome_enabled': False}
    row.state = state
    await db.commit()
    return row


def user():
    return SimpleNamespace(id=1, username=None, first_name='User', last_name=None, is_bot=False)


def callback(row, op='save'):
    return SimpleNamespace(data=_data(row, op), from_user=user(), answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(type='private', id=1), answer=AsyncMock(), edit_reply_markup=AsyncMock()))


async def test_claim_deduplicates_and_cancelled_ai_cannot_resurrect_draft(db):
    repo = AutoConfigRepository(db)
    row = await make_draft(db, 'active')
    first = await repo.claim_turn(row, cooldown=5)
    assert first
    assert await repo.claim_turn(row, cooldown=5) is None
    row = await repo.get(1, lock=True)
    repo.close(row)
    await db.commit()
    result = await run(client(response(('finish_configuration', {}))))
    assert await repo.finish_turn(user_id=1, token=first, result=result, text='stop') is None
    assert await repo.get(1) is None


async def test_expired_session_is_inaccessible(db):
    row = await make_draft(db)
    row.expires_at = now() - timedelta(seconds=1)
    await db.commit()
    assert await AutoConfigRepository(db).get(1) is None


async def test_foreign_and_stale_callbacks_do_not_write(db, monkeypatch):
    row = await make_draft(db)
    settings = Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')
    repo = SimpleNamespace(upsert_chat_settings=AsyncMock())
    cb = callback(row)
    cb.from_user.id = cb.message.chat.id = 2
    await action(cb, SimpleNamespace(), db, repo, settings)
    cb = callback(row)
    cb.data = cb.data.replace(':0:', ':9:')
    await action(cb, SimpleNamespace(), db, repo, settings)
    repo.upsert_chat_settings.assert_not_called()


async def test_save_rechecks_permissions_and_is_atomic_and_idempotent(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(handler, 'can_configure', check)
    row = await make_draft(db)
    cb = callback(row)
    repo = SqlAlchemyActivityRepository(db)
    settings = Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')
    assert await repo.get_chat_settings(chat_id=-1001) is None
    await action(cb, SimpleNamespace(), db, repo, settings)
    assert not (await repo.get_chat_settings(chat_id=-1001)).welcome_enabled
    assert await AutoConfigRepository(db).get(1) is None
    assert check.call_count == 2
    assert len(await repo.list_audit_logs(chat_id=-1001)) == 1
    await action(cb, SimpleNamespace(), db, repo, settings)
    assert len(await repo.list_audit_logs(chat_id=-1001)) == 1


async def test_save_conflict_requires_fresh_review_before_any_write(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(return_value=True))
    row = await make_draft(db)
    cb = callback(row)
    repo = SqlAlchemyActivityRepository(db)
    from selara.domain.entities import ChatSnapshot
    await repo.upsert_chat_settings(chat=ChatSnapshot(-1001, 'supergroup', 'Test'), values={**defaults(), 'economy_enabled': False})
    await db.commit()
    await action(cb, SimpleNamespace(), db, repo, Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:'))
    assert (await repo.get_chat_settings(chat_id=-1001)).welcome_enabled == defaults()['welcome_enabled']
    row = await AutoConfigRepository(db).get(1)
    assert row.state == 'review' and row.revision == 1 and row.draft['economy_enabled'] is False
    assert cb.message.answer.call_count >= 2


async def test_revoked_permissions_and_membership_fail_closed(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'has_permission', AsyncMock(return_value=(True, 'owner', False)))
    repo = SimpleNamespace(get_moderation_state=AsyncMock(return_value=None))
    chat = await db.get(ChatModel, -1001)
    bot = SimpleNamespace(id=10, get_chat_member=AsyncMock(side_effect=RuntimeError('API unavailable')))
    assert not await can_configure(bot=bot, repo=repo, user=user(), chat=chat)
    bot.get_chat_member = AsyncMock(side_effect=[SimpleNamespace(status='left'), SimpleNamespace(status='member')])
    assert not await can_configure(bot=bot, repo=repo, user=user(), chat=chat)
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status='administrator'))
    assert await can_configure(bot=bot, repo=repo, user=user(), chat=chat)
    repo.get_moderation_state = AsyncMock(return_value=SimpleNamespace(is_banned=True))
    assert not await can_configure(bot=bot, repo=repo, user=user(), chat=chat)


async def test_review_messages_do_not_call_llm(db):
    row = await make_draft(db)
    msg = SimpleNamespace(from_user=user(), answer=AsyncMock())
    c = client(response())
    await talk(msg, SimpleNamespace(), db, SimpleNamespace(), Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:'), c)
    c.chat_with_tools.assert_not_called()


def test_callback_payloads_fit_telegram_limit():
    row = SimpleNamespace(id='a'*32, revision=999, state='choosing', candidates=[{'id': -1001, 'title': 'a'}] * 100)
    assert all(len(b.callback_data.encode()) <= 64 for line in keyboard(row).inline_keyboard for b in line)


async def test_permission_revoked_at_final_check_never_writes(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(side_effect=[True, False]))
    row = await make_draft(db)
    repo = SqlAlchemyActivityRepository(db)
    await action(callback(row), SimpleNamespace(), db, repo, Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:'))
    assert await repo.get_chat_settings(chat_id=-1001) is None
    assert (await AutoConfigRepository(db).get(1)).state == 'review'


async def test_continue_preserves_draft_and_invalidates_old_save(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(return_value=True))
    row = await make_draft(db)
    save = callback(row)
    original = dict(row.draft)
    settings = Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')
    repo = SqlAlchemyActivityRepository(db)
    await action(callback(row, 'continue'), SimpleNamespace(), db, repo, settings)
    row = await AutoConfigRepository(db).get(1)
    assert row.state == 'active' and row.draft == original
    await action(save, SimpleNamespace(), db, repo, settings)
    assert await repo.get_chat_settings(chat_id=-1001) is None


async def test_expired_busy_lease_can_be_recovered_without_stale_result(db):
    repo = AutoConfigRepository(db)
    row = await make_draft(db, 'active')
    token = await repo.claim_turn(row, cooldown=1)
    row = await repo.get(1)
    row.lease_until = now() - timedelta(seconds=1)
    await db.commit()
    row = await repo.get(1, lock=True)
    assert row.state == 'active' and row.lease_token is None
    await db.commit()
    result = await run(client(response(('finish_configuration', {}))))
    assert await repo.finish_turn(user_id=1, token=token, result=result, text='stop') is None


async def test_talk_finishes_into_review_without_live_settings_write(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(return_value=True))
    await make_draft(db, 'active')
    thinking = SimpleNamespace(edit_text=AsyncMock())
    msg = SimpleNamespace(from_user=user(), text='На этом завершим', answer=AsyncMock(return_value=thinking))
    c = client(response(('finish_configuration', {})))
    repo = SqlAlchemyActivityRepository(db)
    await talk(msg, SimpleNamespace(), db, repo, Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:'), c)
    assert (await AutoConfigRepository(db).get(1)).state == 'review'
    assert await repo.get_chat_settings(chat_id=-1001) is None
    assert 'Сохранить' in [b.text for line in msg.answer.call_args.kwargs['reply_markup'].inline_keyboard for b in line]


async def test_cooldown_survives_cancel_and_restart(db):
    repo = AutoConfigRepository(db)
    row = await make_draft(db, 'active')
    assert await repo.claim_turn(row, cooldown=10)
    row = await repo.get(1, lock=True)
    repo.close(row)
    await db.commit()
    row = await repo.start(1, [])
    row.state = 'active'
    await db.commit()
    assert await repo.claim_turn(row, cooldown=10) is None


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://invalid host', '/local/file'])
def test_rejects_invalid_welcome_button_url(url):
    with pytest.raises(ValueError):
        update_draft(defaults(), {'welcome_button_url': url}, defaults())


async def test_start_lists_only_verified_known_groups_and_selection_creates_draft(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    from selara.infrastructure.db.models import UserChatActivityModel
    db.add(ChatModel(telegram_chat_id=-1002, type='supergroup', title='Denied'))
    await db.flush()
    db.add_all([UserChatActivityModel(chat_id=c, user_id=1, is_active_member=True, last_seen_at=now()) for c in [-1001, -1002]])
    await db.commit()
    async def allowed(**kwargs):
        return kwargs['chat'].telegram_chat_id == -1001
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(side_effect=allowed))
    message = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=user(), answer=AsyncMock())
    repo = SqlAlchemyActivityRepository(db)
    await handler.start(message, SimpleNamespace(), db, repo, client(response()))
    row = await AutoConfigRepository(db).get(1)
    assert row.state == 'choosing' and [c['id'] for c in row.candidates] == [-1001]
    cb = callback(row, 'pick')
    cb.data += '0'
    await action(cb, SimpleNamespace(), db, repo, Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:'))
    row = await AutoConfigRepository(db).get(1)
    assert row.chat_id == -1001 and row.state == 'active' and row.baseline == row.draft
    assert await repo.get_chat_settings(chat_id=-1001) is None


async def test_cancel_does_not_reset_live_configuration(db):
    from selara.presentation.handlers.autoconfig import cancel
    from selara.domain.entities import ChatSnapshot
    repo = SqlAlchemyActivityRepository(db)
    await repo.upsert_chat_settings(chat=ChatSnapshot(-1001, 'supergroup', 'Test'), values={**defaults(), 'economy_enabled': False})
    await make_draft(db)
    await cancel(SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=user(), answer=AsyncMock()), db)
    assert (await repo.get_chat_settings(chat_id=-1001)).economy_enabled is False
    assert await AutoConfigRepository(db).get(1) is None


async def test_autocfg_recovery_shows_full_review_even_without_model(db, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(return_value=True))
    row = await make_draft(db)
    message = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=user(), answer=AsyncMock())
    await handler.start(message, SimpleNamespace(), db, SqlAlchemyActivityRepository(db), llm_client=None)
    assert 'Черновик настроек' in message.answer.call_args.args[0]
    assert '→' in message.answer.call_args.args[0]
    assert (await AutoConfigRepository(db).get(1)).id == row.id
