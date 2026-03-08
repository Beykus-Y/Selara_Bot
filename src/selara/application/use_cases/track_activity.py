from datetime import datetime

from selara.application.interfaces import ActivityRepository
from selara.domain.entities import ActivityStats, ChatSnapshot, UserSnapshot


async def execute(
    repo: ActivityRepository,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    event_at: datetime,
) -> ActivityStats:
    chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type=chat_type, title=chat_title)
    user = UserSnapshot(
        telegram_user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
    )
    return await repo.upsert_activity(chat=chat, user=user, event_at=event_at)
