# Проверка зависимостей frontend

Обязательный CI `frontend` после `npm ci` выполняет
`npm audit --omit=dev --audit-level=high` для production dependency graph из
`frontend/package-lock.json`. High/critical advisory блокирует проверку и merge;
low/moderate остаются видимыми в отчёте без блокировки. Ошибки registry/network
не скрываются: результат проверки должен быть подтверждён, иначе CI не зелёный.
Build/lint зависимости обновляются Dependabot, но не входят в этот production gate.

Отдельный workflow `Frontend dependency audit` проверяет lockfile ветки `dev`
каждый понедельник в 06:00 UTC, в том числе без новых PR. Его можно запустить
вручную. Dependabot еженедельно создаёт npm update PR в `dev`, включая обновление
lockfile; они проходят обычные `backend`/`frontend` checks и требуют merge через PR.
GitHub активирует schedule и Dependabot config после попадания этих workflow/config
файлов в default branch (`main`); CI audit на PR работает сразу. Продвижение
`dev` в `main` выполняется отдельным обычным PR, а не прямым push.

## Исключения

Текущих исключений нет. Сначала обновляйте затронутые пакеты и lockfile.
Если advisory неприменим или фикс пока отсутствует, исключение требует отдельного
PR с advisory URL/ID, пакетом и версиями, обоснованием неприменимости/принятого
риска, ответственным и датой пересмотра не позднее 30 дней. Условия обхода должны
быть ограничены этой advisory и версиями и прекращать действовать по истечении
срока. Не используйте `continue-on-error`, `|| true` или общий ignore high/critical.
Такое изменение рассматривается отдельно и не отменяет остальные проверки.
