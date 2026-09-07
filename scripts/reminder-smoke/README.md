# Reminder Smoke Harness

Полный изолированный smoke запускается одной командой из корня checkout:

```sh
python3 scripts/reminder-smoke/harness.py
```

Harness сам генерирует имя `rdm-reminder-smoke-<12 hex>`, создаёт env во
временном каталоге вне репозитория, ждёт healthcheck PostgreSQL/Redis/stub,
применяет Alembic ровно до `20260904_0005`, создаёт синтетическую карточку и
запускает bounded проверки scheduler/worker/intent/delivery. Внешние порты не
публикуются, сеть Compose internal, оба feature flag включены только во
временном env. Вызовы stub не сохраняют payload и не печатают токены,
получателей или текст сообщений; `/stats` содержит только канал, режим,
количество и HTTP-коды.

При любой ошибке exit code ненулевой. `finally` выполняет только
`docker compose -p <проверенное имя> down --volumes --remove-orphans`; глобальные
prune/cleanup запрещены. После cleanup проверяется отсутствие контейнеров этого
проекта. Реальные внешние Telegram/Bitrix24 endpoints не вызываются.

Ожидаемый результат: `reminder smoke passed: project=rdm-reminder-smoke-...` и
JSON-отчёт с `db=ok` и безопасной статистикой stub.
