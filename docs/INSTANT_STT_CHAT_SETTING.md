# Мгновенная расшифровка в отдельном чате

`instant_stt_enabled` управляет автоматическим ответом на voice/video-note
сообщения. По умолчанию `true`: миграция 0101 сохраняет прежнее поведение
существующих чатов, новый чат также получает `true`. Глобальные `STT_ENABLED`
и доступность STT client по-прежнему необходимы; per-chat switch не включает
неподключённого провайдера.

Администратор с правом настройки чата может выключить ответы:

```text
/setcfg instant_stt_enabled false
```

Вернуть прежнее поведение: `/setcfg instant_stt_enabled true` или
`/setcfg instant_stt_enabled default`. Этот же toggle доступен в существующей
панели настройки группы в личке и web/Mini App в разделе «AI и итоги дня».
Используются общие boolean parser, права настройки и запись audit.

При `false` handler не занимает STT cooldown, не скачивает Telegram file,
не отправляет статус распознавания и не вызывает instant provider. Другие
чаты и личные сообщения сохраняют свои настройки. Если чтение настройки
упало из-за DB error, instant reply пропускается: fallback на default не должен
снова включать оплачиваемую feature вопреки решению администратора.

Daily Summary имеет независимые switches `daily_summary_include_voice` /
`daily_summary_include_video_notes`, требует `save_message` и использует свой
durable budget. Выключение instant STT не выключает эту явно разрешённую
транскрипцию для итогов. Чтобы не транскрибировать аудио ни для одного из
сценариев, выключите instant toggle и оба switches для итогов.
