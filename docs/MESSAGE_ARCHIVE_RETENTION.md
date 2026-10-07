# Message archive retention and shutdown drain

Issues: #79 (archive retention), #144 (bounded `ActivityBatcher.close()`).

## What is kept and for how long

The message archive (`messages` table) stores group messages with their raw Telegram JSON, text, captions,
edit snapshots and voice transcripts. Parked activity events (`activity_event_dead_letters`) carry the same
payloads. Both are cleaned up by one window:

| Setting | Default | Meaning |
| --- | --- | --- |
| `MESSAGE_ARCHIVE_RETENTION_DAYS` | `14` | Archive rows whose `snapshot_at` is older than this are deleted, together with their text, raw JSON and transcripts. Dead letters whose `created_at` is older than this are deleted too. `0` turns the cleanup off. Values `1`–`6` are rejected, because the daily summary works on one day at a time and needs its window intact. |

The default of 14 days matches the transcript TTL planned in `docs/DAILY_SUMMARY_TODO.md`. Transcripts have no
separate, shorter TTL: they are deleted with their rows.

Not affected by the cleanup:

- Activity counters and other aggregates. They live in their own tables.
- Daily summary runs, their stored summaries and the usage log. A deleted archive row only clears the link:
  `llm_usage_log.message_archive_id` and `stt_budget_reservations.archive_row_id` are set to `NULL`.
- The activity inbox (`activity_event_inbox`). Its rows are pending work and are deleted once applied.

## What changes for users

- The daily summary, its search and stats tools, and the admin archive view only see messages inside the window.
- Reply context for messages older than the window is no longer available.

## How the cleanup runs

- A background task starts about a minute after the bot starts and then runs once an hour.
- Each run deletes in batches of 500 rows. Every batch is its own transaction, followed by a short pause, so no
  statement holds a lock on a table for long.
- A run that deletes anything logs `Message archive retention deleted expired rows` with the counts. A failed run
  logs `Message archive retention run failed` and the next run tries again.

## Deploying

- Migration `0107_message_archive_index` adds `idx_messages_snapshot_at` on `messages(snapshot_at)`. It is a plain
  `CREATE INDEX`, so it blocks writes to `messages` while it builds. Run the deploy in a quiet window.
- The first run deletes everything older than the window, so on an archive with months of history it works through
  a large backlog over several hours. Set `MESSAGE_ARCHIVE_RETENTION_DAYS` before the first start if you want a
  different window, or `0` to skip the cleanup.
- Deleted rows are not restored by rolling back the code. Take a backup first if you need the history.

## Shutdown drain (`ACTIVITY_BATCH_CLOSE_GRACE_SECONDS`, default `5`)

On shutdown the activity batcher gets up to this many seconds to apply the activity inbox. Anything it has not
applied stays in `activity_event_inbox` and is applied on the next start. If the batcher is still busy when the
grace period ends, it is cancelled and an `Activity inbox was not fully applied before shutdown` error is logged
with `inbox_pending`, the number of unapplied events. Keep the grace period below the container's stop timeout
(Docker's default is 10 seconds). `docker-compose.yml` does not set `stop_grace_period`.
