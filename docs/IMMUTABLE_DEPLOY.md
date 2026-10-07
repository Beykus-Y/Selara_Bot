# Deploy по digest и rollback

`Publish Docker Image` после успешного CI на `main` собирает app, web и gacha
из одного полного commit SHA. После завершения всех трёх сборок сохраняется
артефакт `release-manifest`: SHA, repository, publisher run ID и точные
`ghcr.io/...@sha256:...` для каждого образа. Renderer использует тот же app digest.
Теги `latest`, короткий и полный `sha-...` остаются удобными aliases; deploy
не разрешает их заново и не берёт образы из `SELARA_IMAGE` в `.env`.

## Обычный deploy

1. Найдите успешный запуск `Publish Docker Image` на `main`. Его числовой run ID
   есть в URL `.../actions/runs/<ID>` и в Summary вместе с SHA и digest manifest.
2. Укажите этот ID в обязательном `release_run_id` при ручном запуске
   `Deploy To VPS`. Перед SSH проверяются repository, workflow, ветка `main`,
   успешное завершение publisher и структура manifest.
3. На VPS нужны Python 3.10+, Docker Compose v2, существующий compose-проект,
   настроенная `.env`, внешняя сеть `edge` и доступ к GHCR. Workflow копирует
   свой deployment driver на сервер: обновлять серверный git checkout ради
   запуска driver не требуется. Compose-конфигурацию и миграционную
   совместимость версии оператор проверяет до запуска обновления.

Actions artifact v4 не перезаписывается: `overwrite: false`. Повторный publisher
с тем же run ID использует сохранённый manifest и пропускает сборки. Если
manifest отсутствует при rerun, publisher завершится ошибкой: нужно создать
новый запуск с новым release ID. Это исключает повторное использование ID
после удаления/истечения срока артефакта. Новая публикация того же commit
может иметь другие digest и получает другой release ID.

Manifest хранится в Actions до 90 дней (фактический срок может ограничиваться
политикой организации). Истёкший или удалённый артефакт останавливает workflow
до SSH; fallback на SHA-теги или `latest` отсутствует. После успешного deploy
его копия остаётся на VPS без этого ограничения срока. GHCR digest должен
оставаться доступным; удалённые образы нужно восстанавливать из отдельного
архива, а не заменять новой сборкой под прежним release ID.

## Проверка и сохранение результата

Driver использует digest для `app`, `web`, `artifact-renderer`, запрещает
локальную сборку (`--no-build`) и вторичную загрузку через mutable-теги
(`--pull never`). Перед заменой контейнеров проверяет `RepoDigests` и OCI
revision label app/web. После запуска проверяет `.Image` ID, `Config.Image`,
running/health state и app `/healthz`, затем повторно проверяет все контейнеры.
Два обновления на одном VPS сериализуются `flock`; workflow также имеет
concurrency group. Gacha digest записан в manifest для отдельного сервиса,
существующий app/web deployment workflow gacha не обновляет.

Только после успешных проверок в `<VPS_APP_DIR>/.selara-releases/` атомарно
записываются `current.json`, `release-<publisher_run_id>.json`,
`last-deployment.json` с фактическими container/image IDs. Полный результат
попадает в SSH log; manifest также есть в workflow Summary. При переходе на
другой release прежний `current.json` становится `previous.json`.
Повторный deploy текущего release не затирает предыдущий.
Неуспешный запуск не меняет известный current/previous release; контейнеры
могут требовать явного rollback. Автоматического rollback схемы БД нет.
Образы не удаляются автоматическим prune после deploy.

## Rollback

Можно запустить `Deploy To VPS` с run ID предыдущего publisher, пока его
Actions artifact доступен. Или на VPS использовать сохранённый manifest:

```bash
cd /path/to/selara
set -a
. ./.env
set +a
# scripts/release_manifest.py из версии с этим механизмом deploy;
# при необходимости используйте копию driver из /tmp/selara-release-*/scripts/.
python3 scripts/release_manifest.py deploy previous
```

Docker должен быть авторизован в GHCR. Команда проходит те же проверки digest
и health и переключает current/previous после успеха. Для более старого release
передайте `.selara-releases/release-<ID>.json`. Перед откатом убедитесь, что
текущая схема БД совместима со старым кодом: механизм фиксирует образы,
а не восстанавливает данные и не выполняет downgrade миграций.
