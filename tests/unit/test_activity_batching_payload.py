from datetime import datetime, timedelta, timezone

from selara.infrastructure.db.activity_batching import (
    ActivityBatchMessage,
    activity_batch_message_from_payload,
    activity_batch_message_to_payload,
)


def _event(**overrides: object) -> ActivityBatchMessage:
    sent_at = datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "chat_id": -1001,
        "chat_type": "supergroup",
        "chat_title": "Group",
        "user_id": 42,
        "username": "ann",
        "first_name": "Ann",
        "last_name": None,
        "is_bot": False,
        "event_at": sent_at,
        "telegram_message_id": 77,
        "snapshot_kind": "created",
        "snapshot_at": sent_at,
        "sent_at": sent_at,
        "message_type": "text",
        "text": "hi",
        "raw_message_json": {"message_id": 77, "text": "hi"},
        "snapshot_hash": "abc",
    }
    values.update(overrides)
    return ActivityBatchMessage(**values)


def _restore(payload: dict, event: ActivityBatchMessage) -> ActivityBatchMessage:
    # The inbox row's own columns supply the chat identity, so the payload never carries a copy of it.
    return activity_batch_message_from_payload(
        payload,
        chat_id=event.chat_id,
        chat_type=event.chat_type,
        chat_title=event.chat_title,
    )


def test_payload_roundtrip_keeps_every_field() -> None:
    event = _event(edited_at=datetime(2026, 10, 7, 9, 31, tzinfo=timezone(timedelta(hours=3))))

    payload = activity_batch_message_to_payload(event)

    assert isinstance(payload["event_at"], str)
    assert _restore(payload, event) == event


def test_chat_identity_is_left_to_the_inbox_columns() -> None:
    payload = activity_batch_message_to_payload(_event())

    assert not {"chat_id", "chat_type", "chat_title"} & payload.keys()


def test_naive_datetimes_are_stored_as_utc() -> None:
    event = _event(event_at=datetime(2026, 10, 7, 9, 30))

    payload = activity_batch_message_to_payload(event)

    assert payload["event_at"] == "2026-10-07T09:30:00+00:00"
    assert _restore(payload, event).event_at == datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)


def test_unknown_payload_keys_are_ignored_for_forward_compatibility() -> None:
    event = _event()
    payload = activity_batch_message_to_payload(event)
    payload["field_from_a_newer_release"] = 1

    assert _restore(payload, event) == event


def test_archive_free_events_roundtrip_without_snapshot_fields() -> None:
    event = _event(
        telegram_message_id=None,
        count_as_activity=False,
        snapshot_kind=None,
        snapshot_at=None,
        sent_at=None,
        message_type=None,
        text=None,
        raw_message_json=None,
        snapshot_hash=None,
    )

    assert _restore(activity_batch_message_to_payload(event), event) == event
