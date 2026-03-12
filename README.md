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

2. Start PostgreSQL and Redis:

```bash
docker compose up -d postgres redis
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

## Docker deployment

The repository now includes an application image and a compose service for the bot + web panel.

Local image build:

```bash
docker compose build app
docker compose up -d app
```

VPS-friendly image workflow:

1. Build and push the image from your workstation or CI:

```bash
docker build -t ghcr.io/<your-user>/selara:latest .
docker push ghcr.io/<your-user>/selara:latest
```

2. On the VPS set image and container-network DSNs in `.env`:

```bash
SELARA_IMAGE=ghcr.io/<your-user>/selara:latest
SELARA_DATABASE_URL=postgresql+asyncpg://selara:selara@postgres:5432/selara
SELARA_REDIS_URL=redis://redis:6379/0
WEB_HOST=0.0.0.0
WEB_PORT=8080
WEB_BASE_URL=https://your-domain.example
WEB_SESSION_COOKIE_SECURE=true
```

3. Start or update only the application container:

```bash
docker compose pull app
docker compose up -d app
```

After that, application updates on the VPS no longer require `git pull`. You only publish a new image and then run `docker compose pull app && docker compose up -d app`.

Notes:

- `postgres` and `redis` keep their existing named volumes.
- The app container runs `alembic upgrade head` before startup.
- If you proxy the panel through Nginx Proxy Manager, point it to the VPS host and `WEB_PORT`.

## GitHub Actions

The repository now includes two workflows:

- [docker-publish.yml](/mnt/c/Selara/.github/workflows/docker-publish.yml): builds and pushes `ghcr.io/<owner>/selara:latest` on every push to `main`
- [deploy-vps.yml](/mnt/c/Selara/.github/workflows/deploy-vps.yml): manual deploy to the VPS over SSH

### 1. Prepare GitHub Packages

On GitHub:

1. Open repository `Settings -> Actions -> General`
2. Make sure actions are allowed
3. Open your GitHub account `Settings -> Developer settings -> Personal access tokens -> Tokens (classic)`
4. Create a token with:
   - `read:packages`
   - `write:packages` if you want to push manually from your machine too

For the publish workflow itself, `GITHUB_TOKEN` is enough. The personal token is mainly needed by the VPS to pull from GHCR.

### 2. Add repository secrets

Open `Settings -> Secrets and variables -> Actions` and add:

- `VPS_HOST`: public IP or domain of the server
- `VPS_PORT`: SSH port, usually `22`
- `VPS_USER`: SSH user
- `VPS_SSH_KEY`: private SSH key content used by GitHub Actions
- `VPS_APP_DIR`: absolute path on VPS where `docker-compose.yml` and `.env` live
- `GHCR_USERNAME`: your GitHub username
- `GHCR_TOKEN`: GitHub token with at least `read:packages`

### 3. Prepare the VPS once

Repository code no longer needs to be updated on every release, but the VPS still needs the compose files once.

Put these files on the VPS in one directory:

- `docker-compose.yml`
- `.env`

Set the app image in `.env`:

```bash
SELARA_IMAGE=ghcr.io/<your-user>/selara:latest
SELARA_DATABASE_URL=postgresql+asyncpg://selara:selara@postgres:5432/selara
SELARA_REDIS_URL=redis://redis:6379/0
WEB_HOST=0.0.0.0
WEB_PORT=8080
WEB_BASE_URL=https://your-domain.example
WEB_SESSION_COOKIE_SECURE=true
```

Then log in to GHCR on the VPS once:

```bash
echo '<github-token-with-read-packages>' | docker login ghcr.io -u <your-user> --password-stdin
```

And start the app:

```bash
docker compose up -d postgres redis app
```

### 4. Daily workflow

Normal release flow becomes:

1. Push code to `main`
2. GitHub Actions builds and pushes a fresh Docker image to GHCR
3. Run the manual `Deploy To VPS` workflow in the Actions tab

The deploy workflow executes this on the server:

```bash
docker compose pull app
docker compose up -d app
```

### 5. Nginx Proxy Manager

In Nginx Proxy Manager point the host to:

- Forward hostname/IP: your VPS IP or Docker host
- Forward port: the same `WEB_PORT` from `.env`, for example `8080`

If the public site works over HTTPS, keep this in `.env`:

```bash
WEB_BASE_URL=https://your-domain.example
WEB_SESSION_COOKIE_SECURE=true
```

## Tests

```bash
pytest
```
