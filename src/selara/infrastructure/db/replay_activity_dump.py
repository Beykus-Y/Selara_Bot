from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

from selara.application.achievements import (
    AchievementAwardService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
    get_achievement_catalog_from_settings,
)
from selara.core.config import get_settings
from selara.infrastructure.db.activity_batching import ActivityBatchFlushResult, ActivityBatchMessage
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.db.session import create_engine, create_session_factory

_DATETIME_FIELDS = frozenset({"event_at", "snapshot_at", "sent_at", "edited_at"})


def _parse_datetime_value(value: object) -> object:
    if value is None or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def _build_batch_message(payload: dict[str, object]) -> ActivityBatchMessage:
    normalized: dict[str, object] = {}
    for key, value in payload.items():
        normalized[key] = _parse_datetime_value(value) if key in _DATETIME_FIELDS else value
    return ActivityBatchMessage(**normalized)


def _chunked(items: Sequence[ActivityBatchMessage], chunk_size: int) -> Iterator[Sequence[ActivityBatchMessage]]:
    for index in range(0, len(items), chunk_size):
        yield items[index : index + chunk_size]


async def _process_achievements(
    *,
    repo: SqlAlchemyActivityRepository,
    result: ActivityBatchFlushResult,
    catalog,
) -> None:
    if not result.latest_event_at_by_pair:
        return

    orchestrator = AchievementOrchestrator(
        catalog=catalog,
        evaluator=AchievementConditionEvaluator(),
        award_service=AchievementAwardService(repo._session, catalog),
        repo=repo,
    )
    for (chat_id, user_id), event_at in sorted(
        result.latest_event_at_by_pair.items(),
        key=lambda item: (item[1], item[0][0], item[0][1]),
    ):
        await orchestrator.process_message(
            chat_id=chat_id,
            user_id=user_id,
            event_at=event_at,
        )


async def _run(*, dump_path: Path, chunk_size: int) -> None:
    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Dump payload must be a JSON array.")

    events = [_build_batch_message(item) for item in payload if isinstance(item, dict)]
    if not events:
        print("No events found in dump.")
        return

    settings = get_settings()
    catalog = get_achievement_catalog_from_settings(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        replayed = 0
        impacted_chat_ids: set[int] = set()
        latest_pairs = 0

        for chunk_index, chunk in enumerate(_chunked(events, max(1, chunk_size)), start=1):
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                result = await repo.flush_activity_batch(chunk)
                await _process_achievements(repo=repo, result=result, catalog=catalog)
                await session.commit()

            replayed += len(chunk)
            impacted_chat_ids.update(result.impacted_chat_ids)
            latest_pairs += len(result.latest_event_at_by_pair)
            print(
                f"chunk={chunk_index} replayed={len(chunk)} "
                f"impacted_chats={len(result.impacted_chat_ids)} activity_pairs={len(result.latest_event_at_by_pair)}"
            )

        print(
            f"done replayed={replayed} impacted_chats={len(impacted_chat_ids)} "
            f"activity_pairs={latest_pairs}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay dumped ActivityBatcher queue into the database.")
    parser.add_argument("dump_path", type=Path, help="Path to JSON dump created from ActivityBatcher._pending")
    parser.add_argument("--chunk-size", type=int, default=500, help="How many events to replay per transaction.")
    args = parser.parse_args()
    asyncio.run(_run(dump_path=args.dump_path, chunk_size=args.chunk_size))


if __name__ == "__main__":
    main()
