from datetime import datetime, timedelta, timezone

from selara.application.interfaces import ActivityRepository
from selara.domain.entities import ChatSnapshot, UserSnapshot, VoteResult


async def execute(
    repo: ActivityRepository,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    voter: UserSnapshot,
    target: UserSnapshot,
    vote_value: int,
    event_at: datetime,
    daily_limit: int,
    days_for_7d: int,
) -> VoteResult:
    if voter.telegram_user_id == target.telegram_user_id:
        return VoteResult(
            accepted=False,
            reason="Нельзя голосовать за себя",
            target_karma_all_time=None,
            target_karma_7d=None,
        )

    if target.is_bot:
        return VoteResult(
            accepted=False,
            reason="Нельзя голосовать за бота",
            target_karma_all_time=None,
            target_karma_7d=None,
        )

    if vote_value not in {-1, 1}:
        return VoteResult(
            accepted=False,
            reason="Голос должен быть +1 или -1",
            target_karma_all_time=None,
            target_karma_7d=None,
        )

    limit_since = datetime.now(timezone.utc) - timedelta(hours=24)
    votes_24h = await repo.count_votes_by_voter_since(
        chat_id=chat_id,
        voter_user_id=voter.telegram_user_id,
        since=limit_since,
    )
    if votes_24h >= daily_limit:
        return VoteResult(
            accepted=False,
            reason="Достигнут дневной лимит голосов",
            target_karma_all_time=None,
            target_karma_7d=None,
        )

    chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type=chat_type, title=chat_title)
    await repo.record_vote(
        chat=chat,
        voter=voter,
        target=target,
        vote_value=vote_value,
        event_at=event_at,
    )

    karma_all = await repo.get_karma_value(
        chat_id=chat_id,
        user_id=target.telegram_user_id,
        period="all",
    )
    karma_since = datetime.now(timezone.utc) - timedelta(days=days_for_7d)
    karma_7d = await repo.get_karma_value(
        chat_id=chat_id,
        user_id=target.telegram_user_id,
        period="7d",
        since=karma_since,
    )

    return VoteResult(
        accepted=True,
        reason=None,
        target_karma_all_time=karma_all,
        target_karma_7d=karma_7d,
    )
