# Selara

Telegram bot on `aiogram 3` with clean architecture and dual command interface:

- slash commands: `/me`, `/rep`, `/top [N|karma N|activity N|неделя [N]|сутки [N]|час [N]|месяц [N]]`, `/active [N]`, `/game`, `/role`, `/pair`, `/marry`, `/relation`, `/love`, `/care`, `/date`, `/gift`, `/support`, `/flirt`, `/surprise`, `/vow`, `/breakup`, `/divorce`, `/eco`, `/farm`, `/shop`, `/inventory`, `/tap`, `/daily`, `/lottery`, `/market`, `/pay`, `/settings`, `/setcfg`, `/setrank`, `/ranks`, `/setalias`, `/aliases`, `/unalias`, `/aliasmode`, `/roles`, `/roleadd`, `/roleremove`, `/roledefs`, `/roletemplates`, `/rolecreate`, `/rolesettitle`, `/rolesetrank`, `/roleperms`, `/roledelete`, `/lastseen [@username|user_id]`, `/login`, `/help`
- text aliases (RU): `кто я`, `репутация`, `мой рейтинг`, `актив [N]`, `топ [N]`, `топ карма [N]`, `топ неделя [N]`, `топ сутки [N]`, `топ час [N]`, `топ месяц [N]`, `когда был/когда была`, `помощь`, `флирт`, `сюрприз`, `клятва`

## Features

- Tracks activity per chat and per user.
- Tracks daily and minute activity aggregates for leaderboard windows.
- Supports karma voting via reply `+` / `-` in group chats.
- Provides interactive leaderboard modes: hybrid, activity, karma.
- Sends chart images for `/me`, `/rep`, `/top`.
- Supports group mini-games: Spy, Mafia, Dice, Number, Quiz, Bredovukha and Bunker.
- Supports secret role/card delivery in DM for game modes that use private information.
- Supports economy mode: farm, clicker, daily rewards, lottery, market and inventory.
- Supports per-group bot settings with `.env` defaults.
- Supports per-group command access ranks and custom bot roles (with templates and editable permissions).
- Stores last seen timestamp per user in chat.
- Runs a parallel web panel with one-time 6-digit login codes issued by the bot in DM via `/login`.
- Supports PostgreSQL with Alembic migrations.
- Keeps business logic outside Telegram handlers.

## Group settings

`/settings` shows effective settings for the current chat.

`/setcfg <key> <value>` changes one setting for the current group.

`/setrank "<command>" "<role>"` sets a minimum bot role rank for a command in the current group.
Text form also works: `установить "команда" ранг внутри бота роль`.

Examples:

```bash
/setcfg vote_daily_limit 30
/setcfg text_commands_enabled false
/setcfg leaderboard_hybrid_karma_weight 0.6
/setcfg leaderboard_hybrid_activity_weight 0.4
/setcfg leaderboard_week_start_weekday 0
/setcfg leaderboard_week_start_hour 6
/setcfg vote_daily_limit default
```

## Quick start

1. Copy env file:

```bash
cp .env.example .env
```

2. Start PostgreSQL:

```bash
docker compose up -d postgres
```

3. Install dependencies (example with venv):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

4. Run migrations:

```bash
alembic upgrade head
```

5. Start bot and web panel:

```bash
python -m selara.main
```

The same process now starts both the Telegram bot and the HTTP panel.

## Web panel

Default URL: `http://127.0.0.1:8080/login`

1. Open the bot in DM
2. Send `/login`
3. Enter the one-time 6-digit code on the site

Useful env vars:

```bash
WEB_ENABLED=true
WEB_HOST=0.0.0.0
WEB_PORT=8080
WEB_DOMAIN=example.com
WEB_BASE_URL=http://127.0.0.1:8080
WEB_AUTH_SECRET=change-me
WEB_LOGIN_CODE_TTL_MINUTES=5
WEB_SESSION_TTL_HOURS=168
WEB_SESSION_COOKIE_SECURE=false
```

## Tests

```bash
pytest
```
