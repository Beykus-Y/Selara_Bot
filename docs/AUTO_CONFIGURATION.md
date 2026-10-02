# AI configuration in private messages

Use `/autocfg` in the bot's private chat. Select a known group from inline buttons,
then describe the changes in ordinary language. The active assistant operates on
one draft. It can read settings, atomically update related draft fields and call
`finish_configuration`. It has no save, moderation, code, network or general agent
registry access. A group does not need its own llm_enabled flag to use this wizard;
the global configured LLM client must be available.

Say “На этом завершим” or press “К сводке”. The model stops after the finish tool;
ordinary messages in review do not call it. The server renders old → new values,
inspected-but-unchanged parameters, affected sections and relevant dependency
notes. Settings remain live exactly as before until “Сохранить”. “Продолжить
настройку” resumes the same draft; “Отменить изменения” discards the draft,
not the group's current configuration. `/autocfgcancel` is an independent exit.
Calling `/autocfg` again recovers the open draft rather than silently replacing it.

The group schedule timezone is shown explicitly and supplied to the assistant.
The setting catalog reuses existing human descriptions, type/range parsers and
cross-field validation. Related changes such as both leaderboard weights can be
submitted together. Unknown fields, destination IDs, secrets, non-string values
and unbounded integers are rejected. This wizard covers CHAT_SETTINGS_KEYS;
roles, command access rules, glossary entries and gacha operations are separate.

## Authorization and consistency

Candidates come from recorded active group memberships, including inherited and
custom role permissions. Each needs the existing Selara manage_settings right,
no bot ban, current Telegram membership of user and bot, and successful API
verification. Telegram-admin status alone does not grant Selara permissions.
No owner-role bootstrap occurs. Verification failure denies access; a bot without
sufficient access to verify other members may not show that group.

Every callback is private, bound to its initiating user, random session ID and
revision. Every lookup includes the trusted sender. Model arguments cannot select
a destination. Authorization is checked during selection, each AI turn, reviewing,
continuing, and again inside saving. Cancel does not require retained group rights.
Old buttons, forwarded callbacks, replayed saves and cancelled sessions cannot write.

Save locks the draft and current settings, compares the entire original snapshot
and validates the final proposal. Any external settings change blocks that save;
the desired patch is rebased on current values and a fresh review is required.
Unrelated live changes survive. Dependency conflicts return to the assistant for
repair. Only changed keys are written, unless initializing a missing settings row,
when all effective defaults are preserved. Configuration, audit diff and draft
closure commit together. The feature cache is invalidated after commit.
A group-to-supergroup migration invalidates an open draft; select the chat again.

## Reliability and limits

Drafts survive process restarts in autoconfig_sessions (migration 0065). Each user
has at most one open session, expiring after 24 hours. Closed and expired sessions
are replaced on the next start. Closing clears history and draft content. A durable
lease plus conditional revision update allows one AI request across workers.
Cancellation invalidates in-flight work; a late model result cannot resurrect it.
An expired lease can be recovered by `/autocfg` without replaying old tools.

A session permits at most 50 turns, four tool rounds per turn and six operations
per round. Requests obey the configured LLM cooldown, text is limited to 4000
characters, completions to 2000 tokens and each turn has a 180s deadline. Recent
conversation history is bounded. A failed turn rolls back its draft operations;
previous turns remain. Draft mutation is never a fallback for explicit save.
Estimated successful-turn usage and confirmed diffs appear in the chat audit log;
private conversation text is not included there.

Inline callbacks are acknowledged before network checks. Long summaries and model
responses are safely split for Telegram. At review, a save button is absent when
there are no changes. Cancel/review also work when the model becomes unavailable.
Deploy remains manual; apply the normal `alembic upgrade head` before using the
new version.

## Plain-language interaction

The assistant speaks in human setting names and confirmed outcomes, without raw
keys, true/false, API jargon or timezone arithmetic. The read-only
convert_schedule_time tool translates a supplied IANA local timezone into the
configured bot schedule timezone; it never changes global or group timezones.
Schedules currently store whole hours. Fractional offsets and differing seasonal
time changes require clarification rather than silently promising a local schedule.
Prepared changes remain drafts; only the user's Save button applies them.
