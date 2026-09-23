# Remote Booking New / RDM

Remote Desktop Manager (RDM) — сервис бронирования и управления удалёнными
подключениями инженеров технической поддержки.

Текущая стадия: backend-основа MVP с локальной авторизацией, жизненным циклом
карточки удалённого подключения, Frame API с Omnidesk, первичным автоматическим
распределением L2, действиями назначенного L2 и автоматическим распределением
L1, а также сопровождением отклонённой карточки назначенным L1. Актуальный
stage-стенд: Raspberry Pi `172.17.131.115`. Runtime-доставка подтверждена на
stage контролируемыми Telegram- и Bitrix24-проверками; периодические
напоминания подтверждены только для внутреннего изолированного контура.
Production scanner и delivery остаются выключенными.

Локальная ветка содержит candidate внутреннего Omnidesk case index/backfill
(`20260914_0006`, `2815d2e`). Candidate не применён на stage, не меняет
manager API/frontend и не является подтверждением stage или production.
Возможности BL-03 (срочные коллизии, ретроспективная регистрация, завершение)
завершены (DONE) на implementation candidate HEAD `34aba3362bbfd8bf8ef37a511e540e1f916f232b`:
пройден exact quality gate (backend 297 passed, 26 skipped; frontend native 3 tests passed
и build passed; Ruff/format passed; migration round-trip `0001 -> 20260922_0008 -> 0001 -> 0008`;
isolated PostgreSQL BL-03 behavioral matrix 7/7 passed с подтверждённой очисткой).
Финальный exact-SHA gate BL-03 документационного коммита ещё не выполнялся; внешняя
доставка Omnidesk не заявляется (IE-01); REQ-FR-154 не расширяется сверх счётчиков
dashboard; BL-03 не подтверждён на stage и не заявляется как stage-proven.
BL-04 завершён и отправлен в `main` коммитом `40ab192` после успешного exact-SHA
`make verify`: backend 300 passed, 37 skipped; frontend 3 tests passed и build
passed; migration round-trip `0001 -> 0009 -> 0001 -> 0009`; отдельная
изолированная PostgreSQL matrix BL-04 — 11 passed; временные Docker-ресурсы
очищены. Push: `9d5863e..40ab192`. Следующий этап IE-01 — NOT STARTED.

Минимальный внутренний frontend подтверждён на доверенном LAN stage: ссылка из
контролируемого Telegram-уведомления ведёт к login, возвращает на карточку после
входа и поддерживает logout и UI-ветку `404`. Для HTTP stage допустим
`AUTH_COOKIE_SECURE=false`; на VPS обязательны HTTPS и `AUTH_COOKIE_SECURE=true`.

## Что Реализовано

- FastAPI backend с health endpoints и OpenAPI-документацией.
- PostgreSQL-схема под управлением Alembic.
- Redis и сервисный контур Docker Compose.
- Внутренний Vue frontend с SPA fallback, same-origin `/api/v1` и детальной
  карточкой по UUID после локальной авторизации: статус, время, длительность,
  владельцы L1/L2, флаги, безопасная история и действия назначенных L1/L2.
- Read-only панель руководителя `/manager` с полным набором статусов,
  периодом, limit до 200, детерминированной сортировкой и summary по полному
  отфильтрованному набору. API возвращает только безопасную проекцию карточки.
  Панель поддерживает режимы списка и календаря (день/неделя), локальные
  date-only фильтры и отображение часового пояса; календарная сетка сохраняет
  точное время и длительность карточек.
- Создание обычной карточки руководителем:
  - перед созданием сверяет пару `case_id` и номера обращения в Omnidesk;
  - принимает только разрешённые поля и создаёт lifecycle L2 атомарно;
  - ручное назначение учитывает график, отсутствие и пересечения L2;
  - PostgreSQL-конфликты одного активного обращения и пересечения L2 возвращает
    как безопасные `409`, без частичного lifecycle;
  - после отказа L2 создаёт follow-up следующему допустимому L2 с новым
    attempt, schedule, event, audit и notification intents.
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
- Действия назначенного L2:
  - подтверждает назначенную карточку;
  - отклоняет её только с указанной причиной;
  - фиксирует ответ в `assignment_attempt`, истории и аудите;
  - после отказа назначает следующего допустимого кандидата того же цикла;
  - переводит карточку в `Отклонено`, когда кандидаты исчерпаны;
  - отклоняет действия чужого L2 и повторные устаревшие запросы.
- Автоматическое распределение L1 для отклонённой карточки:
  - запускается после отсутствия подходящего L2 или полного круга отказов L2;
  - использует отдельные пул и состояние Round Robin L1;
  - исключает неактивных, отсутствующих, неработающих и занятых L1;
  - сохраняет историю и аудит назначения;
  - оставляет карточку в `Отклонено` без владельца, если кандидатов L1 нет.
- Сопровождение отклонённой карточки назначенным L1:
  - назначенный L1 идемпотентно отмечает информирование клиента;
  - только назначенный L1 может перенести время и изменить описание;
  - перенос сбрасывает сопровождение и запускает новый цикл распределения L2;
  - история и аудит фиксируют информирование и перенос;
  - назначение L1 создаёт дедуплицированные intents доставки для Telegram и
    Bitrix24 с учётом пользовательских настроек каналов.
- Runtime-доставка уведомлений:
  - Celery worker и beat забирают intents из PostgreSQL-очереди;
  - атомарное получение с `SKIP LOCKED`, ограниченные повторы и восстановление
    устаревших блокировок не допускают параллельной обработки одного intent;
  - получатель определяется только по настройкам пользователя, а текст
    содержит минимальные данные карточки и внутреннюю ссылку;
  - Telegram Bot API и Bitrix24 webhook получают только разрешённые конфигурацией
    endpoint'ы; аудит хранит безопасные статусы, без ответов внешних систем;
  - доставка имеет семантику at-least-once: Bitrix24 получает стабильный ключ
    идемпотентности, а Telegram после аварии между отправкой и фиксацией может
    получить повтор.
- Техническая основа менеджерской эскалации:
  - создание `card_events` возвращает внутренний числовой ID события;
  - notification intent хранит source event metadata и серверный
    детерминированный `dedupe_key`;
  - PostgreSQL partial unique index исключает повтор одного ненулевого ключа,
    сохраняя допустимость legacy intents без ключа;
  - payload ограничен разрешёнными техническими полями;
- Событийная менеджерская эскалация:
  - запускается при отсутствии L2, полном отказе L2, повторном неуспешном
    цикле L2 и отсутствии L1;
  - выбирает только активных пользователей с ролью руководителя и создаёт
    intents только для настроенных Telegram/Bitrix24 каналов;
  - использует `card_events.id` и серверный `dedupe_key`, поэтому повтор одного
    исходного события не создаёт новые intents или аудит;
  - не меняет статус карточки и не делает прямых внешних HTTP-вызовов.
- Скоуп BL-03: срочные коллизии, ретроспективная регистрация и завершение (DONE на candidate HEAD `34aba3362bbfd8bf8ef37a511e540e1f916f232b`; не подтверждён на stage, финальный exact-SHA gate документации ещё не проводился, внешняя доставка Omnidesk не заявляется):
   - вытеснение планов в статусах `ASSIGNED` и `CONFIRMED` при срочных коллизиях с автоматическим переназначением либо переводом в `Отклонено` (REJECTED→L1 follow-up) при исчерпании кандидатов;
   - сброс флага и времени просрочки (`overdue_flag`, `overdue_at`) у вытесненной карточки;
   - безопасный отказ при коллизии с карточкой в статусе `IN_PROGRESS` без каких-либо мутаций в БД (карточки, события и аудит остаются неизменными);
   - ретроспективная саморегистрация L2 (retroactive self-registration): автоматический расчёт фактической длительности выполняется при создании уже завершённой ретроспективной карточки, а не при последующем завершении карточки in-progress (обычное завершение может принимать опциональную длительность); строгая валидация активного кода результата, отложенное создание outbox intent (для `in_progress` intent не формируется до завершения);
   - валидация активных кодов результата по каталогу `connection_results` (запрет неизвестных/неактивных кодов);
   - старт карточки (`start_card`) фиксирует только время фактического начала (`actual_start_at`); код результата (`result_code`) и фактическая длительность (`actual_duration_minutes`) при старте не фиксируются (фиксируются при завершении);
   - идемпотентное создание intent во внутренний outbox заметок Omnidesk (`omnidesk_internal_note_outbox`) при завершении карточки без раскрытия `case_id` (заметка Omnidesk является только идемпотентным внутренним outbox intent, внешняя доставка относится к IE-01);
   - фиксация событий (`CardEventType.URGENT_COLLISION`), записей аудита и уведомлений;
   - метрики срочных коллизий в панели руководителя: подсчёт суммарного количества событий `urgent_collision` по карточкам выборки (REQ-FR-154 не заявляется полностью закрытым: реализованы только суммарные счётчики в dashboard summary);
   - границы SRS: BL-03 закрывает REQ-FR-099..125, 144..145 и частичный REQ-FR-154 (только dashboard summary counters; REQ-FR-154 не заявляется полностью закрытым; требования REQ-FR-137..143 и 156..160 не заявляются в BL-03; BL-04 владеет REQ-FR-137..143);
   - статус подтверждения: на candidate HEAD `34aba3362bbfd8bf8ef37a511e540e1f916f232b` пройден exact quality gate: backend regression (297 passed, 26 skipped), frontend native suite (3 tests passed) и build passed, Ruff check/format PASS, migration round-trip `0001 -> 20260922_0008 -> 0001 -> 0008` (`20260901_0001 → 20260922_0008 → 20260901_0001 → 20260922_0008`), isolated PostgreSQL BL-03 behavioral matrix 7/7 passed с подтверждённым cleanup временных ресурсов. Финальный exact-SHA quality gate BL-03 после обновления документации ещё не проводился; внешняя доставка Omnidesk не заявляется (IE-01); stage и deployment не заявляются. BL-04 завершён: exact-SHA `make verify` на `40ab192` прошёл (backend 300 passed, 37 skipped; frontend 3 passed и build passed; migration round-trip `0001 -> 0009 -> 0001 -> 0009`), isolated PostgreSQL matrix BL-04 — 11 passed, временные ресурсы очищены; commit `40ab192` отправлен (`9d5863e..40ab192`).

## Состав Репозитория

- `backend/` — FastAPI-приложение, Alembic-миграции, Celery entrypoints и тесты.
- `frontend/` — каркас Vue 3 + Vite.
- `Docs/` — бизнес-концепция, SRS, технический проект и схема БД.
- `Docs/RDM-Coordinator-Model-Economy-Workflow.md` — инструкция координатору
  по экономии моделей, эскалации и контролю лимитов.
- `decisions/` — зафиксированные проектные решения.
- `scripts/` — эксплуатационные вспомогательные скрипты.
- `docker-compose.yml` — локальный/dev/stage контур сервисов.
- `Docs/RDM-Technical-Design-v0.1.md` — первая версия технического проекта
  для согласования, не утверждённая спецификация.

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
- `REMINDER_SCANNER_ENABLED` (по умолчанию `false`; включение scanner требует
  отдельного stage-gate с согласованным тестовым получателем)
- `REMINDER_L2_INTERVAL_SECONDS`, `REMINDER_L2_ESCALATION_AFTER_COUNT`,
  `REMINDER_L1_INTERVAL_SECONDS`, `REMINDER_L1_ESCALATION_AFTER_COUNT`,
  `REMINDER_L1_MANAGER_REPEAT_SECONDS`, `REMINDER_L1_INFORMED_INTERVAL_SECONDS`,
  `REMINDER_SCAN_INTERVAL_SECONDS`, `REMINDER_BATCH_SIZE`

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

Текущий migration head: `20260904_0005`.

Migration contract на PostgreSQL 16 входит в `make verify-migrations`: чистая
БД проходит `20260901_0001 → head → 20260901_0001 → head`. Перед первым
upgrade до head gate записывает базовые `users` и `connection_cards`, затем
проверяет их сохранность после каждого перехода и инварианты: один активный
тикет и отсутствие пересечения активных назначений L2. Проверка использует
только `.env.example` и удаляет временные containers/volumes после завершения.

Локальный candidate head: `20260914_0006`; он требует отдельного
migration/PostgreSQL gate до публикации.

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
- `POST /api/v1/cards/l1`
- `POST /api/v1/cards/l2`
- `POST /api/v1/cards/l2/urgent`
- `POST /api/v1/cards/l2/retroactive`
- `GET /api/v1/cards/{card_id}`
- `POST /api/v1/cards/{card_id}/assign`
- `POST /api/v1/cards/{card_id}/confirm`
- `POST /api/v1/cards/{card_id}/reject`
- `POST /api/v1/cards/{card_id}/start`
- `POST /api/v1/cards/{card_id}/complete`
- `POST /api/v1/cards/{card_id}/cancel`
- `POST /api/v1/cards/{card_id}/l1/client-informed`
- `POST /api/v1/cards/{card_id}/l1/reschedule`

Frame API:

- `POST /api/v1/frame/sessions`
- `GET /api/v1/frame/cards`
- `POST /api/v1/frame/cards`

После создания frame-сессии клиент передаёт токен в заголовке
`X-RDM-Frame-Token`. Если в запросе есть Origin или Referer, frame-сессия
привязывается к этому origin.

## Проверки

Единый quality gate запускается из корня checkout:

```bash
make verify
```

Он не читает локальный `.env`: использует только `.env.example` и запускает
четыре независимые части, которые можно повторить по отдельности:

```bash
make verify-backend
make verify-frontend
make verify-compose
make verify-migrations
```

`verify-migrations` создаёт отдельный Docker Compose project
`rdm-quality-gate` (имя можно переопределить через `RDM_GATE_PROJECT`),
проверяет `upgrade → downgrade → upgrade` и удаляет созданные контейнеры и
volumes при выходе. Его `docker-compose.quality-gate.yml` снимает host-port
bindings, поэтому временный contour не конфликтует с работающими сервисами.
Для frontend используются `npm ci`, native `node --test` и production build.
Gate завершается с `UNAVAILABLE`, если нужный runtime не установлен; это не
считается успешной проверкой.

Перед публикацией также нужно проверить staged diff и отслеживаемые файлы на
секреты.

## Статус Stage

На Raspberry Pi `172.17.131.115` доставлены и smoke-tested этапы:

- первая PostgreSQL-миграция;
- локальная авторизация;
- жизненный цикл карточки удалённого подключения;
- Frame API с реальным Omnidesk lookup по `case_id`;
- первичное автоматическое распределение L2.
- действия назначенного L2: подтверждение, отказ, переназначение и исчерпание
  кандидатов.
- автоматическое распределение L1 из отдельного Round Robin-пула после
  отсутствия L2 или полного отказа L2 (`2382045`, `2446760`).
- stage-gate реального Telegram-канала для внутреннего пользователя `nimda`:
  авторизация, private-chat привязка и одна подтверждённая тестовая отправка.
- сопровождение отклонённой карточки L1 и миграция `20260903_0002`
  (`8ab818a`, `0c87b5f`, `e286446`).
- runtime-доставка notification intents и миграция `20260904_0003`
  (`c978f44`, `ea92289`, `a6d78ce`).
- техническая основа идемпотентной менеджерской эскалации и миграция
  `20260904_0004` (`ef62656`, `8f03561`, `ad67e21`, `e638e29`).
- событийная менеджерская эскалация для отсутствия L2, полного отказа L2,
  повторного неуспешного цикла и отсутствия L1 (`38e0d38`, `45d6349`,
  `af0d919`). Срочная коллизия и таймерные правила в этот этап не входят.
- read-only панель руководителя (`8877eeb`): доступ только для роли
  `Руководитель`, safe allowlist полей, фильтры status/period/limit,
  детерминированная сортировка и full-set summary.
- календарь панели руководителя (`52dc3a5`): режимы день/неделя, 15-минутная
  сетка без округления фактического времени и длительности, date-only период с
  локальными границами суток, responsive-горизонтальная прокрутка и компактная
  login-карточка до авторизации.
- manager card creation (`abf0580`): безопасный Omnidesk preflight, ручное и
  автоматическое назначение L2, откат ошибок follow-up и конкурирующие
  запросы `201/409` без частичных записей. Controlled stage-smoke подтвердил
  L2-A → L2-B и отдельное automatic assignment; marker-данные удалены.

Распределение и действия L2 проверены изолированными тестовыми данными внутри
транзакции с rollback. На stage подтверждены успешное подтверждение назначенным
L2, обязательная причина отказа, назначение следующего кандидата в текущем
цикле, переход в `Отклонено` после исчерпания пула, а также защита от чужого и
устаревшего действия. `assignment_attempt`, история и аудит содержали ожидаемые
изменения. Реальные рабочие данные и Docker volumes во время smoke-теста не
изменялись и не удалялись.

В runtime-образ backend тестовые файлы не включены, поэтому профильная
stage-проверка выполнена транзакционным smoke-тестом непосредственно против
PostgreSQL и завершена rollback.

Для L1 stage-smoke подтвердил первоначальное назначение, Round Robin,
исключение отсутствующего, работающего вне графика и занятого L1, сценарий без
кандидатов, запуск после отсутствия L2 и после полного отказа L2, защиту от
повторного назначения, а также неизменность L2-попыток и состояния L2. Все
временные строки удалены rollback.

Для сопровождения отклонённой карточки stage-проверка подтвердила запуск после
отсутствия L2 и полного отказа L2, полномочия назначенного L1, идемпотентную
отметку информирования, запрет для чужого L1 и другой роли, перенос времени и
описания с новым циклом L2, защиту от устаревших действий, историю, аудит и
отсутствие дублей notification intents. Проверка выполнена в одной транзакции с
rollback; рабочие данные не сохранялись. Перед применением миграции создана
резервная копия PostgreSQL. На stage подтверждены `current = heads =
20260903_0002`, `/health/live` и `/health/ready` возвращают `200`.

Для runtime-доставки на stage подтверждены очередь, повторные попытки,
восстановление устаревшей блокировки, защита от двойной обработки и безопасный
аудит. Проверка использовала изолированный внутренний HTTP-stub и была полностью
очищена; реальные внешние каналы и получатели не задействовались. Перед
применением миграции `20260904_0003` создана резервная копия PostgreSQL;
`current = heads = 20260904_0003`, `/health/live` и `/health/ready` возвращают
`200`, а свежие логи backend, worker и beat не содержат ошибок.

Для событийной менеджерской эскалации stage-smoke подтвердил выбор только
активных руководителей, исключение неактивного руководителя и администратора
без этой роли, создание Telegram/Bitrix24 intents, идемпотентность повторного
source event и единственную audit-запись. Проверены ветки отсутствия L2,
полного отказа L2 с передачей в L1, отсутствия L1, а также отсутствие
руководителей и каналов. Тест выполнялся в одной транзакции с rollback;
worker не видел intents и внешние API не вызывались. Alembic остаётся на
`20260904_0004`.

Для технической основы менеджерской эскалации на stage подтверждены возврат
числового ID `card_events`, source event metadata, серверный `dedupe_key`,
безопасный payload и совместимость runtime-очереди с legacy и новыми intents.
Миграция `20260904_0004` проверена на одноразовой PostgreSQL-БД в цепочке
upgrade/downgrade/re-upgrade, включая конкурентную вставку; перед применением
на stage создана резервная копия PostgreSQL. `current = heads = 20260904_0004`,
health endpoints возвращают `200`, а rollback-smoke не оставил тестовых строк.
Эта техническая основа не означает, что менеджерская эскалация уже вызывается
из L1/L2-процессов.

Следующий этап: подключить событийную менеджерскую эскалацию к подтверждённым
веткам L1/L2. Периодические напоминания и включение реальных внешних каналов
останутся отдельными stage-gate.

Для периодических напоминаний полный isolated harness подтвердил восемь
независимых сценариев: L1 lifecycle до `client_informed`, post-informed без
manager escalation, однократный `overdue_at`, catch-up без лавины событий,
`FOR UPDATE SKIP LOCKED`, savepoint isolation, temporary retry/terminal failure
и lifecycle-закрытие schedules. Проверка использовала только внутренние
stub-каналы и фиктивные IDs; backfill и реальные Telegram/Bitrix24 отправки не
выполнялись. Alembic находится на `20260904_0005`; интервалы и пороги
сохраняются в snapshot. На production stage `REMINDER_SCANNER_ENABLED=false` и
`NOTIFICATION_DELIVERY_ENABLED=false`.

Следующий этап: согласованный тест реальных Telegram/Bitrix24 каналов с
тестовым получателем и отдельное решение о включении фоновых задач.

Текущий контракт Bitrix24 для доставки — `imbot.message.add`: адаптер отправляет
POST на `BITRIX24_BOT_WEBHOOK_URL` с полями `BOT_ID`, `CLIENT_ID`, `DIALOG_ID` и
`MESSAGE`. Успех фиксируется только после HTTP 2xx с положительным числовым
`result`; application-level ошибки и недостоверные ответы не переводят intent
в `sent`. Переход на `imbot.v2` — отдельный будущий этап.

Frontend stage-smoke выполнил один controlled Telegram intent для `nimda` без
Bitrix24, периодической delivery или retry: intent получил `sent`, `attempts=1`
и одну audit-запись. После ручной проверки synthetic card, event, intent и audit
удалены по точному marker; очередь пуста. `/health/live` и `/health/ready`
возвращают `200`, Alembic остаётся на `20260904_0005`.

Dependency corrective обновил Vite до `6.4.3`; lockfile фиксирует esbuild
`0.25.12` и `@vitejs/plugin-vue 5.2.4`. На stage `npm audit` сообщил `0`
vulnerabilities, frontend build прошёл, а runtime-образ не содержит Node, npm
или `node_modules`. Controlled Telegram delivery после обновления также
подтверждена одной отправкой (`sent`, `attempts=1`, audit=1) и очищена по marker.
Предупреждение: при первом frontend rebuild Compose пересоздал backend как
зависимость; последствия проверены отдельно. Последующие frontend-only deploy
выполняются через `docker compose build frontend` и
`docker compose up -d --no-deps --force-recreate frontend`.

Telegram stage-gate завершён: пользователь `nimda` успешно авторизован,
private Telegram chat привязан, штатный `TelegramAdapter` выполнил ровно один
HTTP-вызов с безопасным текстом, а получение сообщения подтверждено вручную.
Повторных вызовов и retry не было; notification intent, карточки, клиентские
данные и ссылки не использовались. Scanner, delivery worker и beat оставались
выключенными.

Bitrix24 stage-gate завершён: один синтетический intent для согласованного
внутреннего получателя был доставлен штатным runtime через `imbot.message.add`.
Intent получил `sent`, `attempts=1` и одну audit-запись без retry и дублей;
получение и открытие внутренней ссылки подтверждены вручную. Временные
карточка, событие, intent и audit удалены по точному marker, очередь снова
пуста. Scanner оставался выключенным; delivery включалась только на один
адресный runtime-вызов и затем возвращена в `false`.

Детальная внутренняя UI-карточка подтверждена на stage (`6ff5717`). Отдельно
пересобраны и пересозданы только backend и frontend; health endpoints вернули
`200`, Alembic остался на `20260904_0005`, SPA fallback и same-origin API
доступны. Synthetic smoke подтвердил ожидаемые HTTP-ветки `401/403/404/409/422`,
решение назначенного L2, а также rollback-пути отказа L2, информирования и
переноса L1 без изменения общего распределения. Ручная проверка `nimda`
подтвердила сохранение маршрута после login, повторное открытие карточки в
сессии и logout. Одна контролируемая Telegram-доставка на synthetic-карточку
завершилась `sent`, `attempts=1`, одной audit-записью без retry и дублей;
получение и открытие ссылки подтверждены вручную. Все synthetic карточки,
пользователи и intents удалены по точному набору тестовых тикетов; очередь и
active reminder schedules равны нулю. Production scanner и delivery снова
выключены; Bitrix24 не использовался.

Панель руководителя подтверждена на stage (`8877eeb`). Полный candidate gate
прошёл с `122 passed`, Ruff, frontend audit/type-check/build, Docker build,
Compose validation и isolated PostgreSQL/Redis HTTP smoke. Последний подтвердил
`401/403/422`, все status slug, timezone-aware period, границы limit,
детерминированную PostgreSQL-сортировку, full-set summary и safe projection.
Ручной LAN smoke пользователя `nimda` подтвердил вход, счётчики, фильтры,
открытие карточки, возврат на панель и logout. Четыре synthetic-карточки разных
состояний и synthetic-владельцы удалены по точному marker; queue и active
schedules снова равны нулю.

В завершение цикла выполнены ровно одна контролируемая Telegram- и одна
Bitrix24-доставка для `nimda`: оба intent получили `sent`, `attempts=1` и по
одной audit-записи без retry или дублей; получение и открытие ссылки подтверждены
вручную. Synthetic card, source event, intents и audit удалены по marker.
`REMINDER_SCANNER_ENABLED` и `NOTIFICATION_DELIVERY_ENABLED` остаются `false`.

Календарный stage-gate (`52dc3a5`) прошёл с `122 passed`, Ruff, frontend
audit/type-check/build, Docker build, Compose validation и isolated
PostgreSQL/Redis HTTP/UI smoke. Ручная LAN-проверка `nimda` подтвердила вход,
фильтры дат, список/календарь, день/неделю, переход в карточки и logout.
Четыре synthetic-карточки и synthetic L2 удалены по marker. После окончательного
принятия интерфейса повторены по одной контролируемой Telegram- и Bitrix24-
доставке: обе завершились `sent`, `attempts=1`, audit=1 без retry и дублей;
получение и открытие ссылки подтверждены вручную. Marker-данные удалены,
queue=0, active schedules=0; scanner и delivery остаются `false`.

Manager card creation подтверждён на stage (`abf0580`). Isolated candidate
gate включал PostgreSQL lifecycle, rollback при ошибке schedule и обоих intent,
terminal L1 follow-up, а также реальные двухсоединительные HTTP-гонки с
ожидаемыми `201/409`. Controlled stage-smoke по выделенному Omnidesk-тикету
подтвердил ручное назначение L2-A, отказ и назначение L2-B, затем отдельное
automatic assignment. Две контролируемые доставки для `nimda` (Telegram и
Bitrix24) завершились `sent`, `attempts=1`, audit=1 без retry и дублей;
получение и открытие ссылки подтверждены вручную. Synthetic card, source event,
intents, audit и пользователи удалены по marker. Queue и active schedules равны
нулю, `REMINDER_SCANNER_ENABLED=false` и `NOTIFICATION_DELIVERY_ENABLED=false`.

Локальный candidate Omnidesk case index/backfill (`20260914_0006`) добавляет
внутренний индекс, checkpoint/conflict ledger и явно запускаемый ограниченный
resumable backfill с dry-run. Manager API и frontend пока не переключены;
candidate не считается stage-gate.

## Завершение Каждого Этапа

Каждый этап разработки считается завершённым только после полного цикла:

1. целевые тесты и статические проверки локально;
2. отдельный функциональный коммит;
3. проверка diff и отслеживаемых файлов на секреты;
4. `git push origin main`;
5. fast-forward обновление stage на Raspberry Pi `172.17.131.115`;
6. проверка health, Alembic, свежих логов и профильный smoke-тест;
7. актуализация разделов «Что Реализовано» и «Статус Stage» в этом README;
8. актуализация локального `PROJECT.md`: дата, подтверждённые этапы, открытые
   вопросы и следующий stage-gate;
9. отдельный документальный коммит, push и fast-forward обновление рабочей копии
   на RPi. Если изменились только документы, пересборка контейнеров не требуется.

Непроверенная локальная реализация должна быть явно отделена от функций,
подтверждённых на stage. Старые записи логов не считаются результатом текущей
проверки: в отчёт входят только события из временного окна нового smoke-теста.
