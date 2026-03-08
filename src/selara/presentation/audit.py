from __future__ import annotations

from selara.domain.entities import ChatSnapshot


async def log_chat_action(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    action_code: str,
    description: str,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    meta_json: dict | None = None,
):
    return await activity_repo.add_audit_log(
        chat=ChatSnapshot(
            telegram_chat_id=chat_id,
            chat_type=chat_type,
            title=chat_title,
        ),
        action_code=action_code,
        description=description,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        meta_json=meta_json,
    )
