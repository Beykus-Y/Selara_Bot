# Liveness и readiness

App отвечает на три неавторизованных endpoint. Они не показывают токены,
адреса зависимостей или тексты ошибок:

- `/livez`: 200, если web process способен обработать запрос. БД, Redis,
  polling и внешние провайдеры не проверяются. Используйте для проверки
  жизни процесса, не для подтверждения успешного deploy.
- `/readyz`: 200 только при успешном `SELECT 1` в main DB, Redis PING,
  настроенном durable Redis game store и работающем polling с heartbeat
  не старше 45 секунд. Иначе 503. DB/Redis probes идут параллельно,
  каждая ограничена двумя секундами.
- `/healthz`: compatibility alias для полной readiness. Старые мониторы
  получают строгий результат вместо прежней проверки одной БД.

Ответ readiness содержит `status` и boolean `checks.database`, `checks.redis`,
`checks.polling`, с `Cache-Control: no-store`. Polling heartbeat обновляется
каждые 10 секунд; `mark_bot_polling_stopped()` в `finally` выключает readiness
при завершении poller. Heartbeat показывает жизнь процесса polling, а не
latency Telegram API; он не отправляет дополнительные Telegram API запросы
на каждый публичный probe. Во время startup до запуска polling readiness 503.

## Redis policy

Redis обязателен для production readiness: durable game state, live events
и общий login limiter требуют shared store. Memory fallback не считается
готовым production runtime, даже если другой Redis client уже получил PONG.
Старый game store, оставшийся в memory mode после outage, должен восстановить
durable backend или быть перезапущен; один ответ PONG не означает, что runtime
уже восстановил состояние. Это не запрещает `/livez` и диагностику outage.

LLM, STT, web search, gacha API и прогрев image cache не входят в обязательные
probes: их отказ может корректно отключить только соответствующую feature.
Readiness не выполняет оплачиваемых вызовов провайдеров.

## Deploy и public frontend

Nginx web image проксирует `/miniapp/readyz`, `/miniapp/healthz` и `/miniapp/livez`
в app. Deployment driver сначала проверяет локальный `/readyz`, затем
`WEB_BASE_URL/miniapp/readyz` через публичный маршрут. Нужно настроить
`WEB_BASE_URL` в app `.env` на URL, которым пользуются пользователи; HTTPS
сертификат проверяется стандартным TLS client, без bypass.

Driver читает checksum `/usr/share/nginx/html/index.html` из запущенного web
контейнера и сравнивает с HTTP response `WEB_BASE_URL/miniapp/`. Stale frontend,
неправильный upstream, HTTP/TLS ошибка или readiness 503 останавливают deploy
до продвижения current/previous release metadata. Повторные HTTP checks
ограничены шестью попытками с паузой 5 секунд; каждый request имеет timeout 5s.
Если reverse proxy переписывает HTML, такая проверка не пройдёт: production
route должен отдавать оригинальный frontend document.

Процесс deploy не может проверить всю бизнес-логику через health probes.
CI и проверка миграционной совместимости остаются обязательными.
