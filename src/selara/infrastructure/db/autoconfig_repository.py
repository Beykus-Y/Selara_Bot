"""Persistent private drafts; version and lease predicates reject stale AI work."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from selara.infrastructure.db.models import AutoConfigSessionModel


def now():
    return datetime.now(timezone.utc)


class AutoConfigRepository:
    def __init__(self, session):
        self.session = session

    async def get(self, user_id: int, *, lock=False):
        stmt = select(AutoConfigSessionModel).where(AutoConfigSessionModel.user_id == user_id,
            AutoConfigSessionModel.expires_at > now(), AutoConfigSessionModel.state != 'closed')
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt.execution_options(populate_existing=True))
        if lock and row is not None and row.state == 'busy' and row.lease_until is not None:
            until = row.lease_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until < now():
                row.state = 'active'
                row.lease_token = row.lease_until = None
                row.revision += 1
                await self.session.flush()
        return row

    async def start(self, user_id: int, candidates: list):
        # Preserve cooldown across cancelled/restarted drafts.
        previous = await self.session.scalar(select(AutoConfigSessionModel).where(AutoConfigSessionModel.user_id == user_id))
        last_turn_at = previous.last_turn_at if previous is not None else None
        # The caller locks its user row; unique user_id also protects other workers.
        await self.session.execute(delete(AutoConfigSessionModel).where(or_(
            AutoConfigSessionModel.expires_at <= now(),
            (AutoConfigSessionModel.user_id == user_id) & (AutoConfigSessionModel.state == 'closed'))).execution_options(synchronize_session='fetch'))
        row = AutoConfigSessionModel(id=uuid4().hex, user_id=user_id, state='choosing', revision=0,
            candidates=candidates, baseline={}, draft={}, touched=[], history=[], turns=0,
            last_turn_at=last_turn_at, expires_at=now() + timedelta(hours=24))
        self.session.add(row)
        await self.session.flush()
        return row

    async def claim_turn(self, row, *, cooldown: float):
        token = uuid4().hex
        current = now()
        stmt = update(AutoConfigSessionModel).where(
            AutoConfigSessionModel.id == row.id, AutoConfigSessionModel.revision == row.revision,
            AutoConfigSessionModel.expires_at > current, AutoConfigSessionModel.turns < 50,
            or_(AutoConfigSessionModel.state == 'active',
                (AutoConfigSessionModel.state == 'busy') & (AutoConfigSessionModel.lease_until < current)),
            or_(AutoConfigSessionModel.last_turn_at.is_(None),
                AutoConfigSessionModel.last_turn_at <= current - timedelta(seconds=max(1, cooldown))),
        ).values(state='busy', lease_token=token, lease_until=current + timedelta(minutes=4),
            last_turn_at=current, turns=AutoConfigSessionModel.turns + 1,
            revision=AutoConfigSessionModel.revision + 1).execution_options(synchronize_session=False)
        changed = (await self.session.execute(stmt)).rowcount == 1
        await self.session.commit()
        return token if changed else None

    async def finish_turn(self, *, user_id, token, result, text):
        row = await self.get(user_id, lock=True)
        if row is None or row.state != 'busy' or row.lease_token != token:
            return None
        row.draft = result.draft
        row.touched = result.touched
        row.history = [*row.history[-22:], {'role': 'user', 'content': text},
            {'role': 'assistant', 'content': (result.answer or 'Диалог завершён, показана сводка черновика.')[:4000]}]
        row.state = 'review' if result.finished else 'active'
        row.lease_token = row.lease_until = None
        row.revision += 1
        await self.session.commit()
        return row

    @staticmethod
    def close(row):
        row.state = 'closed'
        row.revision += 1
        row.lease_token = row.lease_until = None
        row.history, row.candidates, row.baseline, row.draft, row.touched = [], [], {}, {}, []
