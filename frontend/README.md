# Selara — фронтенд

React + TypeScript + Vite Mini App/веб-панель для Selara Bot. Собирается в
статику и раздаётся через nginx (см. `Dockerfile`, `nginx/default.conf.template`),
проксируясь к FastAPI-бэкенду бота (`APP_UPSTREAM`, см. `docker-compose.yml`
в корне репозитория — сервис `web`).

## Разработка

```bash
npm install
npm run dev      # dev-сервер с HMR
npm run build    # tsc -b && vite build
npm run preview  # предпросмотр production-сборки
```

## Lint

`npm run lint` запускает не только фронтенд-линт (`eslint .`), но и
`lint:server-ui` — отдельный набор проверок для **серверного** Jinja/JS/CSS
UI бота (`src/selara/web/templates`, `src/selara/web/static/*.js/css`):

- `lint:server-ui:jinja` — `djlint` по Jinja-шаблонам.
- `lint:server-ui:js` — ESLint по статическим JS-файлам серверного UI.
- `lint:server-ui:css` — Stylelint по CSS-файлам серверного UI.
- `lint:server-ui:html` — рендерит фикстуры (`scripts/render_*_fixture.py`)
  и прогоняет их через HTMLHint.

Это одна из причин, почему `pip install -e .[dev]` нужен даже для чисто
фронтенд-разработки — часть lint-пайплайна вызывает Python-скрипты
основного бота (`${SELARA_PYTHON:-.venv/bin/python}`).

## Как это связано с основным ботом

- Этот каталог — Mini App/PC-панель (`selara-web` контейнер), отдельный
  сервис от основного бота (`selara-app`). Полная схема — в
  `../docker-compose.yml` и `INSTALLATION.md`.
- Не путать с server-side rendered страницами бота (`/app/*`,
  Jinja-шаблоны в `src/selara/web/templates`) — они лежат в основном
  Python-репозитории, но их frontend-инструментарий (lint/тесты фикстур)
  живёт здесь же, в этом каталоге.
