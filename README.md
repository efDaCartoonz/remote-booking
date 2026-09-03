# Remote Booking New / RDM

Remote Desktop Manager (RDM) — сервис бронирования и управления удалёнными
подключениями инженеров технической поддержки.

Текущая стадия: backend-основа MVP с локальной авторизацией, жизненным циклом
карточки удалённого подключения, Frame API с Omnidesk и первичным автоматическим
распределением L2. Актуальный stage-стенд: Raspberry Pi `172.17.131.115`.

## Что Реализовано

- FastAPI backend с health endpoints и OpenAPI-документацией.
- PostgreSQL-схема под управлением Alembic.
- Redis и сервисный контур Docker Compose.
- Локальная авторизация пользователей через HTTP-only session cookie.
- Внутренний API карточек и базовые переходы жизненного цикла.
- Клиентский Frame API для тикетов Omnidesk:
  - создаёт короткоживущие frame-сессии;
  - серверно сверяет Omnidesk `case_id` и номер тикета;
  - читает существующие RDM-карточки по тикету;
  - создаёт карточку при допустимых условиях;
  - переоткрывает закрытый тикет Omnidesk перед созданием карточки.
- Первичное автоматическое распределение L2:
  - выбирает доступного инженера L2 из пула распределения;
  - использует глобальное состояние Round Robin;
  - исключает кандидатов вне графика, в отсутствии, отключённых от пула или
    занятых пересекающейся активной карточкой;
  - переводит карточку в `Отклонено` с причиной, если кандидатов нет.

## Состав Репозитория

- `backend/` — FastAPI-приложение, Alembic-миграции, Celery entrypoints и тесты.
- `frontend/` — каркас Vue 3 + Vite.
- `Docs/` — бизнес-концепция, SRS, технический проект и схема БД.
- `decisions/` — зафиксированные проектные решения.
- `skills/` — локальные инструкции для review-процесса.
- `scripts/` — эксплуатационные вспомогательные скрипты.
- `docker-compose.yml` — локальный/dev/stage контур сервисов.

## Локальный Запуск

1. Скопировать `.env.example` в `.env`.
2. Заполнить локальные значения `.env`. Не коммитить реальные пароли, ключи и токены.
3. Запустить стек:

```bash
docker compose up --build
```

Полезные локальные адреса:

- Backend live: `http://localhost:8000/health/live`
- Backend ready: `http://localhost:8000/health/ready`
- API-документация: `http://localhost:8000/api/docs`
- Frontend: `http://localhost:8080`

## Конфигурация

Runtime-конфигурация читается из `.env`.

Основные переменные:

- `APP_SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `BACKEND_CORS_ORIGINS`
- `OMNIDESK_BASE_URL`
- `OMNIDESK_STAFF_EMAIL`
- `OMNIDESK_API_KEY`
- `OMNIDESK_TIMEOUT_SECONDS`

Правила работы с секретами:

- Реальные `.env` должны оставаться локальными для конкретного хоста.
- `db-password-nimda.txt` и файлы вида `*api-key*` не должны попадать в Git.
- Нельзя выводить или коммитить пароли, API-ключи, токены, содержимое тикетов
  и персональные данные.
- `.env.example` должен содержать только шаблонные значения.

## База Данных

PostgreSQL публикуется на порту `5432` хоста для доступа со stage/LAN.

Форма подключения к stage:

- Host: IP-адрес stage-сервера, например `172.17.131.115`
- Port: `5432`
- Database: `rdm`
- User: `nimda`
- Password: хранится только в локальном password-файле stage или `.env`

Применить миграции из backend-контейнера:

```bash
docker compose exec backend alembic upgrade head
```

Проверить состояние миграций:

```bash
docker compose exec backend alembic current
docker compose exec backend alembic heads
```

Текущий migration head: `20260901_0001`.

## API

Health:

- `GET /health/live`
- `GET /health/ready`

Локальная авторизация:

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

Внутренние карточки:

- `POST /api/v1/cards`
- `GET /api/v1/cards/{card_id}`
- `POST /api/v1/cards/{card_id}/assign`
- `POST /api/v1/cards/{card_id}/confirm`
- `POST /api/v1/cards/{card_id}/reject`
- `POST /api/v1/cards/{card_id}/start`
- `POST /api/v1/cards/{card_id}/complete`
- `POST /api/v1/cards/{card_id}/cancel`

Frame API:

- `POST /api/v1/frame/sessions`
- `GET /api/v1/frame/cards`
- `POST /api/v1/frame/cards`

После создания frame-сессии клиент передаёт токен в заголовке
`X-RDM-Frame-Token`. Если в запросе есть Origin или Referer, frame-сессия
привязывается к этому origin.

## Проверки

Backend-тесты запускаются из директории `backend`:

```bash
cd backend
pytest tests/test_cards.py tests/test_frame_api.py tests/test_l2_distribution.py
```

Lint и форматирование запускаются из корня репозитория:

```bash
ruff check --no-cache backend/app backend/tests
ruff format --check --no-cache
```

Перед публикацией нужно проверить staged diff и отслеживаемые файлы на секреты.

## Stage Workflow

Обновления stage выполняются через отдельный контрольный шаг:

1. Проверить локальный Git-статус, новые коммиты, remote, набор миграций и
   отслеживаемые файлы на секреты.
2. Проверить Raspberry Pi stage read-only: Git HEAD/status, Docker Compose,
   Alembic current/head, health endpoints и свежие логи.
3. Запросить явное подтверждение перед `git push`, write-действиями по SSH,
   Docker Compose изменениями, миграциями, перезапусками или тестовыми данными.
4. Отправить `main` в GitHub.
5. Обновить рабочую копию Raspberry Pi только fast-forward merge.
6. Применить только неприменённые миграции, если они есть.
7. Перезапустить только сервисы, затронутые доставленными изменениями.
8. Проверить health endpoints, Alembic, логи и узкий smoke-сценарий этапа.

Нельзя удалять Docker volumes, данные БД, миграции или stage-конфигурацию без
отдельного явного подтверждения.

## Статус Stage

На Raspberry Pi `172.17.131.115` доставлены и smoke-tested этапы:

- первая PostgreSQL-миграция;
- локальная авторизация;
- жизненный цикл карточки удалённого подключения;
- Frame API с реальным Omnidesk lookup по `case_id`;
- первичное автоматическое распределение L2.

L2 stage был проверен изолированными тестовыми данными внутри транзакции с
rollback. Реальные рабочие данные и Docker volumes во время smoke-теста не
изменялись и не удалялись.
