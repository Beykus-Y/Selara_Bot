# Message archive retention and shutdown drain

Issues: #79 (archive retention), #144 (bounded `ActivityBatcher.close()`).

## Off by default

Retention is off until `MESSAGE_ARCHIVE_RETENTION_DAYS` is set to 7 or more. With the default `0`, nothing is deleted
and the archive grows as it did before.

Once it is on, the deletes are permanent. Take a backup first (`INSTALLATION.md` §3.5). Rolling back the code does not
bring deleted rows back, and setting the value back to `0` only stops future deletes.

## What is kept and for how long

The message archive (`messages` table) stores group messages with their raw Telegram JSON, text, captions, edit
snapshots and voice transcripts. Parked activity events (`activity_event_dead_letters`) carry the same payloads. Both
follow one window:

| Setting | Default | Meaning |
| --- | --- | --- |
| `MESSAGE_ARCHIVE_RETENTION_DAYS` | `0` (off) | Archive rows whose `snapshot_at` is older than this are deleted, together with their text, raw JSON and transcripts. Dead letters whose `created_at` is older than this are deleted too. `0` turns the cleanup off. Values `1`–`6` are rejected as a safety margin: the daily summary and its tools read the last day or two of the archive. |

Suggested value: 14 days, which matches the transcript TTL planned in `docs/DAILY_SUMMARY_TODO.md`. That is a product
choice; the code does not assume it. Transcripts have no separate, shorter TTL: they are deleted with their rows.

Dead letters hold activity events whose counters were never applied. Once one is outside the window it is deleted, and
its counters are lost for good: a dead letter cannot be replayed after that. Dead letters are pruned only when
retention is on.

Not affected by the cleanup:

- Activity counters and other aggregates. They live in their own tables.
- Daily summary runs, their stored summaries and the usage log. A deleted archive row only clears the link:
  `llm_usage_log.message_archive_id` and `stt_budget_reservations.archive_row_id` are set to `NULL`.
- The activity inbox (`activity_event_inbox`). Its rows are pending work and are deleted once applied.

## What changes for users

- The daily summary, its search and stats tools, and the admin archive view only see messages inside the window.
- Reply context for messages older than the window is no longer available.
- Text, captions, raw JSON and transcripts of older messages are gone and cannot be recovered without a backup.

## How the cleanup runs

- A background task starts about a minute after the bot starts and then runs once an hour. With `0` it does not run.
- Each run deletes in batches of 500 rows. Every batch is its own transaction, with a 0.5-second pause between batches,
  so no statement holds a lock on a table for long.
- A run that deletes anything logs `Message archive retention deleted expired rows` with `archive_rows_deleted` and
  `dead_letters_deleted`. A failed run logs `Message archive retention run failed`, and the next run tries again.
- The cleanup takes no lock across instances. The deployment runs one instance. With two, their runs can collide, and
  a collision can show up as a failed run in the log.

## Deploying

- Migration `0107_message_archive_index` adds `idx_messages_snapshot_at` on `messages(snapshot_at)`. It is a plain
  `CREATE INDEX`, so it blocks writes to `messages` while it builds. Run the deploy in a quiet window.
- Before you set a value of 7 or more, take a backup and check that it restores (`INSTALLATION.md` §3.5). The first
  run deletes everything older than the window, so on an archive with months of history it works through a large
  backlog over several hours.
- Deleted rows free space for reuse inside PostgreSQL, but the table files do not shrink on their own. The first purge
  also writes a lot of WAL, so check disk space before you enable it.
- Rolling back the code does not restore deleted rows. Only a backup does.

## Shutdown drain (`ACTIVITY_BATCH_CLOSE_GRACE_SECONDS`, default `5`)

On shutdown the activity batcher gets up to this many seconds to apply the activity inbox. Anything it has not applied
stays in `activity_event_inbox` and is applied on the next start. If the batcher is still busy when the grace period
ends, it is cancelled and an `Activity inbox was not fully applied before shutdown` error is logged with
`inbox_pending`, the number of unapplied events.

The worst case for this step is about 7 seconds: the grace period, one second to unwind the cancelled batch, and one
second to read the backlog for the log. The step runs after the other background tasks have stopped, so the whole
shutdown can take longer. Docker's default stop timeout is 10 seconds. `docker-compose.yml` does not set
`stop_grace_period`; setting it is a deployment decision.
