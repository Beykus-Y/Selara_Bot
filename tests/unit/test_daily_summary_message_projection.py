from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from selara.infrastructure.db.models import MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


@pytest.mark.parametrize("extra", [
    None, {}, {"type": "mention", "offset": 3, "length": 5},
    {"type": "text_mention", "offset": "3", "length": 5, "user": {"id": 2}},
    {"type": "text_mention", "offset": -1, "length": 5, "user": {"id": 2}},
    {"type": "text_mention", "offset": 3, "length": 5, "user": {"id": True}},
])
def test_projection_extracts_only_valid_minimal_text_mentions(extra):
    row = MessageArchiveModel(
        telegram_message_id=1, user_id=1, sent_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        text="😀 герой", transcript=None, reply_to_telegram_message_id=None,
        raw_message_json={
            "entities": [extra, {
                "type": "text_mention", "offset": 3, "length": 5,
                "user": {"id": 2, "first_name": "Private name", "username": "private"},
            }],
            "chat": {"title": "Private chat"},
        },
    )
    view = SqlAlchemyActivityRepository._to_archived_message_view(row)
    assert view.text_mentions == ((3, 5, 2),)
    assert set(asdict(view)) == {
        "telegram_message_id", "user_id", "sent_at", "text", "transcript",
        "reply_to_telegram_message_id", "text_mentions",
    }
    assert "Private" not in repr(view)


@pytest.mark.parametrize("raw", [{}, None, {"entities": {}}, {"entities": None}])
def test_projection_accepts_legacy_payload_without_entities(raw):
    row = MessageArchiveModel(
        telegram_message_id=1, user_id=1, sent_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        text="legacy", raw_message_json=raw,
    )
    assert SqlAlchemyActivityRepository._to_archived_message_view(row).text_mentions == ()
