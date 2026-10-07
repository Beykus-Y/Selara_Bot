# Общий rate limit для входа

User code login и оба admin password endpoints используют Redis из `REDIS_URL`.
`WEB_LOGIN_ATTEMPT_LIMIT` задаёт максимум failed/in-flight попыток в скользящем
окне `WEB_LOGIN_ATTEMPT_WINDOW_MINUTES`. Lua admission атомарно очищает истёкшие
элементы, проверяет лимит, добавляет новый token и устанавливает TTL по времени
Redis. Несколько web replicas и restart видят одно состояние.

Успешный login освобождает только собственный token. Параллельные failed attempts
остаются в окне; successful login не сбрасывает чужой счётчик. При ошибке Redis
admission отвечает HTTP 503 до проверки credentials. Если Redis стал недоступен
после успешной авторизации, сохранённая попытка консервативно истечёт по TTL.
Отдельные namespaces `web` и `admin` сохраняются; admin API и HTML login разделяют
один admin window. IP/key хешируется перед записью в Redis.

## Reverse proxy

Limiter использует `request.client.host`. Uvicorn обрабатывает forwarded headers
только от IP/CIDR из `WEB_FORWARDED_ALLOW_IPS`. По умолчанию доверены
`127.0.0.1,::1`; пустое значение отключает доверие. Wildcard и сети /0 запрещены.
Для Docker укажите IP реального reverse proxy или его выделенный изолированный
subnet, например `172.20.0.0/16`, если именно эта сеть используется вашим proxy.
Не доверяйте сети, в которой находятся произвольные клиенты.

Proxy должен перезаписывать X-Forwarded-For либо корректно добавлять свой hop.
Uvicorn выбирает последний недоверенный адрес цепочки: поддельный адрес слева
не заменяет реальную клиентскую identity. От недоверенного peer forwarded headers
игнорируются. Приложение не читает X-Real-IP напрямую. При отдельном запуске
Uvicorn CLI передайте тот же allow-list через `--forwarded-allow-ips`.

Проверки общего состояния/атомарности/expiry выполняются в CI на реальном Redis.
