from datetime import datetime, timezone

from selara.infrastructure.db.replay_activity_dump import _build_batch_message


def test_build_batch_message_parses_iso_strings_and_keeps_unix_timestamps() -> None:
    message = _build_batch_message(
        {
            "chat_id": 1,
            "chat_type": "group",
            "chat_title": "Test",
            "user_id": 2,
            "username": "alice",
            "first_name": "Alice",
            "last_name": None,
            "is_bot": False,
            "event_at": "2026-04-09T16:39:42+00:00",
            "telegram_message_id": 77,
            "count_as_activity": False,
            "snapshot_kind": "edited",
            "snapshot_at": 1773050700,
            "sent_at": "2026-04-09T16:35:00Z",
            "edited_at": 1773050700,
            "message_type": "text",
            "text": "hello",
            "caption": None,
            "raw_message_json": {"message_id": 77, "text": "hello"},
            "snapshot_hash": "hash",
        }
    )

    assert message.event_at == datetime(2026, 4, 9, 16, 39, 42, tzinfo=timezone.utc)
    assert message.snapshot_at == 1773050700
    assert message.sent_at == datetime(2026, 4, 9, 16, 35, 0, tzinfo=timezone.utc)
    assert message.edited_at == 1773050700
