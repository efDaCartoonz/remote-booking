# RDM — операционная дорожная карта

Дата актуализации статусов: 23 сентября 2026.
Remote baseline: `origin/main` = `7ab7f38b16ce9b40204ad80b06e94e6f0d452666`.
Local development baseline: merge `7771821`, объединяющий текущую локальную
линию с `1643140` (FI-01) и `6de632d` (DB-01).
Последний подтверждённый milestone baseline: `34aba3362bbfd8bf8ef37a511e540e1f916f232b` (BL-03).

## Правила статуса и планирования

Эта roadmap построена от фактического Git baseline, `PROJECT.md`,
`Docs/AI-WORK-STATE.md`, SRS, Concept и Technical Design. Код, старый commit
или прежний stage-smoke сами по себе не закрывают milestone: готовность требует
соответствия SRS, тестов в Git и профильной проверки окружения.

`DONE` означает, что на baseline есть реализация, тесты и достаточное для
workstream доказательство. `PARTIAL` означает, что имеется часть кода или
тестов, но SRS-контракт не закрыт. `NOT STARTED` означает отсутствие нужного
versioned результата. Статусы stage/RPi из `PROJECT.md` считаются историческим
документальным свидетельством и должны подтверждаться отдельно при новом
stage-gate.

После подтверждённого завершения milestone в этом файле обновляются его статус,
revision и Definition of Done, а в `Docs/AI-WORK-STATE.md` добавляется краткая
запись с результатами gate и границами проверки. Публикация, stage/RPi deploy
и production status фиксируются отдельно и не следуют из локального `DONE`.

Техническая граница всех следующих работ: внутренний Omnidesk `case_id` не
попадает в browser/API URL/DOM/ошибки/logs manager-контуров; он допускается
только во внутренних backend/DB/Redis слоях.

## 1. Foundation / Infrastructure

**Цель:** сделать каждую последующую поставку воспроизводимо проверяемой от
чистого Git revision.

**Статус:** DONE — FI-01 завершён в `1643140`; его quality-gate contract
доступен в local development baseline.

**Связанные SRS:** NFR-001..029, SEC-001..008; Technical Design §§2–5, 14, 17.

**Уже есть:** Docker Compose с PostgreSQL, Redis, FastAPI, Celery и Vue;
Dockerfiles и systemd unit (`docker-compose.yml`, `backend/Dockerfile`,
`frontend/Dockerfile`, `deploy/systemd/rdm-compose.service`); backend tests.
Commits: начальный baseline, `b8b5de6`/`5d03bdb` (Ruff corrective) и
`1643140`. `Makefile`, `scripts/quality-gate.sh`, native frontend test command
и no-port Compose overlay дают воспроизводимый local path.

**Критерий завершения workstream:** clean revision проходит единый
source-controlled backend/frontend/migration/Compose gate без ручной
подмены окружения; результат сохраняется в CI или воспроизводимом локальном
скрипте.

### FI-01 — Воспроизводимый quality gate

- **Результат в Git:** конфигурация проверок и один документированный command
  path для backend tests, lint/format, Alembic upgrade/downgrade, frontend
  type-check/build/tests и `docker compose config`.
- **SRS:** NFR-001..006, NFR-020..029; Technical Design §17.
- **Зависимости:** нет.
- **Статус:** DONE — source-controlled `make verify` объединяет backend tests,
  Ruff, frontend `npm ci`/test/build, Compose config и isolated migration smoke.
  Corrective commits `5da4ba5`..`269b7ca` исполняют этот путь целиком в
  versioned Docker containers без host Python/pytest/npm и подтверждены на
  isolated Docker runtime 17 сентября 2026.
- **Definition of Done:** выполнен в `1643140`, corrective evidence —
  `269b7ca`.

## 2. Database

**Цель:** довести схему PostgreSQL и миграции до всех принятых доменных
инвариантов, не смешивая schema presence с бизнес-готовностью.

**Статус:** PARTIAL — DB-01 завершён; последующие административные и
policy-изменения остаются в DB-02.

**Связанные SRS:** DATA-001..056, REQ-FR-028..035, 126..145, 156..165;
Technical Design §7.

**Уже есть:** миграции `20260901_0001`…`20260904_0005`, таблицы карточек,
ролей, графиков, распределения, cycles/attempts, audit, notification intents
и reminders; exclusion constraint L2 (`backend/alembic/versions/`). README и
runtime head согласованы на `20260904_0005`.

**Критерий завершения workstream:** все новые доменные поля и ограничения
вводятся только миграциями; upgrade/downgrade/re-upgrade проверены на
поддерживаемой PostgreSQL 16, а schema docs и runtime head согласованы.

### DB-01 — Schema-contract reconciliation и миграционный gate

- **Результат в Git:** выровненные migration head/README, тест миграционной
  цепочки PostgreSQL 16 и зафиксированные инварианты существующих таблиц.
- **SRS:** DATA-001..020, NFR-020..029.
- **Зависимости:** FI-01.
- **Статус:** DONE — `make verify-migrations` поднимает изолированный
  PostgreSQL 16, сохраняет seeded данные через `0001 → head → 0001 → head` и
  проверяет active-ticket, L2-overlap и reminder-schedule invariants.
- **Definition of Done:** выполнен в `6de632d`.

### DB-02 — Административные данные и планировочные политики

- **Результат в Git:** миграции/репозитории для управляемых результатов,
производственного календаря, графиков, отсутствий, membership распределения и
настроек продления с audit constraints.
- **SRS:** REQ-FR-034, 038, 113..118, 126..145, 156..160; DATA-037..041.
- **Зависимости:** DB-01, BE-02.
- **Статус:** DONE — migration `20260917_0007`, repository boundary и audit
  constraints закрывают каталог результатов, calendar/schedules/absences,
  distribution membership и session-extension policy. Isolated Docker gate
  подтвердил backend/frontend/Compose и migration contract; DB-02 PostgreSQL
  integration tests прошли на PostgreSQL 16.
- **Definition of Done:** выполнен в DB-02 commit: database invariants и audit
  покрыты PostgreSQL tests; каждая business policy имеет один storage source и
  migration rollback/re-upgrade проверку.

## 3. Backend core

**Цель:** закрыть безопасный публичный API-контракт, аутентификацию и
авторизацию до расширения пользовательских сценариев.

**Статус:** PARTIAL.

**Связанные SRS:** REQ-FR-001..008, 023..035, 083..098; SEC-001..008;
Technical Design §§4, 8, 12.

**Уже есть:** cookie sessions/login/logout/me, базовые card/frame/manager API,
manager-create (`backend/app/api/*`, `backend/app/auth/*`). Commits `741acc3`,
`641aad2`, `39c6daa`, `c7105eb`, `6fc49c8`, `7ab7f38`. Текущий manager
контракт по-прежнему раскрывает и требует `case_id`; generic state-changing
endpoints не применяют полную role/action policy. См. `AI-WORK-STATE.md`.

**Критерий завершения workstream:** каждый изменяющий endpoint имеет
server-side authorization, ownership/state checks, стабильный публичный
контракт без внутреннего `case_id`, негативные tests и audit evidence.

### BE-01 — Безопасный manager-create: case-number-only контракт

- **Результат в Git:** backend-only resolver, позволяющий по номеру тикета
однозначно получить внутренний идентификатор без его выдачи клиенту; новый
manager request/response/preflight contract, миграция если storage необходима,
и regression tests на 401/403/404/409/422, ambiguity и отсутствие ID leak.
- **SRS:** REQ-FR-025, REQ-FR-028..033, SEC-001..008, DATA-049..052;
  Concept BR-001/BR-024; Technical Design §§6, 10, 12.
- **Зависимости:** FI-01 и DB-01 gate; точный Omnidesk lookup contract должен
  быть доказан до переключения public API.
- **Статус:** DONE — `case_number` является единственным browser-facing
  идентификатором; backend разрешает его через внутренний index, а `case_id`
  остаётся в backend/DB-контуре. Покрыты 401/403/404/409/422, ambiguity,
  повторная проверка ответа Omnidesk, conflicts и отсутствие public leak.
  Docker-only quality gate на `269b7ca` подтвердил PostgreSQL migration smoke,
  backend/Frame regression, frontend tests и production build; временные
  resources очищены. Функциональная реализация — `b5f9dc7`.

### BE-02 — Централизованная RBAC и action policy

- **Результат в Git:** единый policy layer для manager/L1/L2/admin и ownership
checks для create, assign, confirm, reject, start, complete, cancel,
reschedule; запрещённые действия возвращают согласованные 403/409.
- **SRS:** REQ-FR-001..005, 046..054, 071..098, SEC-001..008.
- **Зависимости:** FI-01, BE-01.
- **Статус:** DONE — единый `CardAction` policy применяется к manager/L1/L2
  card actions до записи, различает role/ownership (`403`) и state (`409`) и
  сохраняет фактического actor в event/audit. Commit `8e5dc19`; Docker-only
  quality gate прошёл на isolated runtime.
- **Definition of Done:** выполнен в `8e5dc19`: role × action × card-status
  matrix, negative API regressions, BE-01 contract и Docker quality gate
  подтверждены.

### BE-03 — Контракты создания по ролям

- **Результат в Git:** отдельные service/API contracts для L1 create, L2
self-create, urgent и retroactive L2; общая validation policy для 120 минут,
14 дней, длительности и creator restrictions.
- **SRS:** REQ-FR-023..035, 099..109, 119..125, 156..160.
- **Зависимости:** BE-01, BE-02, DB-02.
- **Статус:** DONE — отдельные public contracts `/cards/l1`, `/cards/l2`,
  `/cards/l2/urgent` и `/cards/l2/retroactive` принимают только публичный
  номер тикета; generic create route удалён. Общий server-side policy
  применяет role/self-create, 120 минут, 14 дней, duration, urgent reason и
  retrospective result/report rules до записи. Commit `587accb`.
- **Definition of Done:** выполнен в `587accb`: API/service/PostgreSQL tests
  покрывают L1, L2 self, urgent и retroactive scenarios, негативные RBAC/
  validation/conflict paths, audit actor, BE-01 privacy boundary и BE-02
  policy reuse. Isolated Docker quality and migration contracts прошли.

## 4. Backend business logic

**Цель:** довести lifecycle, назначение и фоновые действия до полного
SRS-процесса с транзакционными инвариантами.

**Статус:** PARTIAL.

**Связанные SRS:** REQ-FR-009..022, 036..165; Concept §§8–12; Technical
Design §§7, 9, 11, 13.

**Уже есть:** статусы и card history/audit; L2 Round Robin, cycles/attempts,
reject → reassign, L1 follow-up, notification intents и reminder schedules.
Доказательства: `backend/app/cards/service.py`,
`backend/app/assignments/{service,l1_service,manager_escalation}.py`,
`backend/app/reminders.py`, `backend/app/notifications.py`; commits `abf0580`
и `741acc3`; backend test files `test_cards.py`, `test_l1_distribution.py`,
`test_l2_distribution.py`, `test_reminders.py`.

**Критерий завершения workstream:** все переходы из SRS имеют policy,
транзакционный storage, event/audit, notifications where required и tests на
success, conflict, retry and unauthorized paths.

### BL-01 — Полное назначение, переназначение и overdue L2/L1

- **Результат в Git:** завершённые policies для manager reject-for-L2,
manual reassignment, overdue → L1 assignment, повторной criticality/escalation
и закрытия L1 tasks после self-reschedule клиента.
- **SRS:** REQ-FR-036..082, 083..090.
- **Зависимости:** BE-02, DB-02.
- **Статус:** DONE — functional completion `10f71dd`. Exact-SHA isolated
  PostgreSQL 16 acceptance подтвердил manager reassignment, overdue L2 → L1,
  trusted Omnidesk reschedule, persisted cycle/attempt/reminder/event/audit,
  repeat/idempotency и stale/conflict branches без duplicate lifecycle records.
- **Definition of Done:** выполнен: exact-SHA gate прошёл backend regression,
  PostgreSQL behavioral integration, migration `0001 → head → 0001 → head`,
  Ruff, frontend tests/production build и Compose validation в temporary
  projects без stage checkout, `.env` или persistent volumes.

### BL-02 — Время, отмена и планировочные ограничения

- **Результат в Git:** единая validation policy и endpoints для разрешённых
time changes/cancel by actor, release reservations, audit reason и
notifications; schedule/absence/calendar/out-of-hours computation.
- **SRS:** REQ-FR-034..035, 083..098, 126..136, 156..160.
- **Зависимости:** BE-02, DB-02, BL-01.
- **Статус:** DONE — functional completion и exact-SHA acceptance на
  `bee0f7d`. Подтверждены role/state/API matrix для переноса и отмены,
  scheduling window и availability policy, urgent out-of-hours exception,
  manager cancel для `in_progress`, освобождение L2 reservation, закрытие
  assignment cycle/attempt и reminder cleanup без duplicate lifecycle records.
- **Definition of Done:** выполнен: exact-SHA isolated gate прошёл backend
  regression (`284 passed, 18 skipped`), Ruff check/format, frontend tests и
  production build, Compose validation и migration
  `0001 → head → 0001 → head`; отдельная PostgreSQL behavioral matrix
  прошла с результатом `118 passed`. Temporary checkout, containers и volumes
  очищены; stage checkout, `.env`, services и persistent volumes не изменялись.

### BL-03 — Urgent, retroactive, execution and completion

- **Результат в Git:** вытеснение планов при срочных коллизиях (reassign или
  REJECTED→L1), безопасный отказ при коллизии с `in_progress`, ретроспективная
  саморегистрация L2 с авторасчётом длительности при создании уже завершённой
  карточки (при завершении in_progress авторасчёта нет; обычное завершение
  принимает опциональную длительность), валидация активных кодов результата по
  каталогу, фиксация только `actual_start_at` при старте (код результата и
  длительность при старте не фиксируются; фиксируются при завершении),
  идемпотентный intent во внутренний Omnidesk note outbox без раскрытия `case_id`
  (заметка Omnidesk — только внутренний outbox intent, внешняя доставка
  относится к IE-01), события/аудит/уведомления и суммарные счётчики срочных
  коллизий в dashboard summary руководителя.
- **SRS:** REQ-FR-099..125, 144..145, частичный REQ-FR-154 (только dashboard
  summary counters; REQ-FR-154 не заявляется полностью закрытым;
  REQ-FR-137..143 и 156..160 не входят в границы BL-03; автопродление сессий
  относится к BL-04).
- **Зависимости:** BE-03, DB-02, BL-02.
- **Статус:** DONE — реализация и exact candidate quality gate на
  `34aba3362bbfd8bf8ef37a511e540e1f916f232b`. Подтверждены вытеснение планов
  в ASSIGNED/CONFIRMED при срочных коллизиях (переназначение либо перевод в
  REJECTED→L1 со сбросом overdue), безопасный отказ при коллизии с IN_PROGRESS
  без мутаций в БД, ретроспективная L2 саморегистрация с авторасчётом
  длительности при создании уже завершённой карточки (при обычном завершении
  in_progress авторасчёта нет, принимается опциональная длительность),
  валидация активных кодов результата по каталогу connection_results, фиксация
  только actual_start_at при старте (код и длительность не фиксируются при
  старте, фиксируются при завершении), идемпотентный intent во внутренний
  Omnidesk note outbox без раскрытия case_id (только внутренний outbox intent,
  внешняя доставка относится к IE-01), события/аудит/уведомления и суммарные
  счётчики срочных коллизий в dashboard summary руководителя (REQ-FR-154 не
  заявляется полностью закрытым сверх счётчиков dashboard). Финальный exact-SHA
  quality gate документационного коммита ещё не выполнялся; внешняя доставка
  Omnidesk, stage и deployment не заявляются.
- **Definition of Done:** выполнен на candidate HEAD
  `34aba3362bbfd8bf8ef37a511e540e1f916f232b`: exact quality gate прошёл
  backend regression (297 passed, 26 skipped), frontend native suite
  (3 tests passed) и build passed, Ruff/format passed, migration round-trip
  `0001 -> 20260922_0008 -> 0001 -> 0008` (`20260901_0001 → 20260922_0008 → 20260901_0001 → 20260922_0008`);
  отдельная изолированная PostgreSQL BL-03 behavioral matrix прошла 7/7,
  cleanup временных ресурсов подтверждён. Финальный exact-SHA gate после
  обновления документации ещё не проводился; внешняя доставка Omnidesk не
  заявляется (IE-01); REQ-FR-154 не заявляется сверх счётчиков dashboard.

### BL-04 — Автопродление и background policy completion

- **Результат в Git:** scheduled extension processor with configurable interval,
automatic end after 12 hours, result-report completion, collision delegation,
event/audit and idempotency/lock behavior. Automatic end sets
`COMPLETED_PENDING_RESULT` («Окончено»), records the exact comment
`завершено автоматически`, and notifies the assigned L2 and manager. A valid
report permits direct `IN_PROGRESS` → «Завершено»; only the assigned L2 may
submit a missing report. Each L2 may have only one `IN_PROGRESS` card.
- **Правила коллизий:** extension displaces only `ASSIGNED`/`CONFIRMED` cards
under BL-03 rules; otherwise extension is rejected and the affected card is
returned to L1 under BL-03 rules.
- **Предпосылка данных:** clean-model premise; no deployed legacy database with
the old constraints exists.
- **SRS:** REQ-FR-137..143 (владеет требованиями автопродления); Technical Design §9.
- **Зависимости:** DB-02, BL-03, FI-01.
- **Статус:** DONE; commit `40ab192` pushed to `main` (`9d5863e..40ab192`).
- **Подтверждённое evidence:** exact-SHA `make verify` на `40ab192` прошёл:
  backend 300 passed, 37 skipped; frontend 3 tests passed и build passed;
  migration round-trip `0001 -> 0009 -> 0001 -> 0009`. Изолированная
  PostgreSQL BL-04 matrix — 11 passed; временные Docker-ресурсы очищены.
- **Definition of Done:** выполнен на exact SHA `40ab192`; commit доставлен
  в `main`. Stage/deployment и внешняя доставка Omnidesk не заявляются.

## 5. Frontend

**Цель:** дать каждой роли минимальный, безопасный и тестируемый UI поверх
закрытых backend contracts.

**Статус:** PARTIAL.

**Связанные SRS:** UI-001..051, REQ-FR-023..165; Technical Design §§5–6, 13, 17.

**Уже есть:** один Vue SPA с login/logout, card detail/actions, manager list/
calendar и manager-create (`frontend/src/App.vue`); type-check/build script.
Commits `6fc49c8`, `7ab7f38`. Нет frontend unit/e2e tests и Frame UI.

**Критерий завершения workstream:** UI не раскрывает internal IDs, отображает
только разрешённые действия, обрабатывает required errors и имеет automated
unit/e2e coverage по ролям и timezones.

### FE-01 — Test foundation и безопасная manager-create форма

- **Результат в Git:** frontend test runner, tests validation/loading/error/
double-submit и case-number-only форма без поля/маршрута/DOM `case_id`.
- **SRS:** REQ-FR-025, 028..033, UI-031..037, SEC-001..008.
- **Зависимости:** BE-01, FI-01.
- **Статус:** PARTIAL — форма и client-side bounds есть, но она требует
  `case_id` и тестов нет.
- **Definition of Done:** type-check/build/unit tests pass; browser regression
confirms no internal ID exposure and only one create POST on rapid submit.

### FE-02 — Рабочие места L1, L2 и руководителя

- **Результат в Git:** role-aware flows for assignment decision, L1 follow-up,
time/cancel/manual assignment, history and notifications state.
- **SRS:** REQ-FR-046..098, UI-009..047.
- **Зависимости:** BE-02, BL-01, BL-02, FE-01.
- **Статус:** PARTIAL — card actions and manager dashboard exist; role workspaces
  and full policies отсутствуют.
- **Definition of Done:** end-to-end tests cover allowed and forbidden actions
for each role; UI matches backend policy and supports 401/403/409/422 states.

### FE-03 — Клиентский Frame и часовые пояса

- **Результат в Git:** isolated Frame UI for current ticket, create/list, browser
timezone detection/manual override and internal-user timezone rendering.
- **SRS:** REQ-FR-023, 091..092, 126..136, 161..165; UI-001..008.
- **Зависимости:** BE-03, BL-02, FI-01.
- **Статус:** NOT STARTED — Frame API есть, UI отсутствует.
- **Definition of Done:** e2e tests prove ticket scoping, no internal history/ID
leak, create behavior and display in at least two timezones.

### FE-04 — Администрирование и отчёты

- **Результат в Git:** UI for user/role/schedule/calendar/distribution/result
management and required operational reports.
- **SRS:** REQ-FR-113..118, 126..155; UI-048..051; ADM-001..014.
- **Зависимости:** DB-02, BE-02, BL-03.
- **Статус:** NOT STARTED.
- **Definition of Done:** each management action is RBAC-protected and audited;
reports reconcile to database fixtures and have UI tests.

## 6. Integration / E2E

**Цель:** надёжно соединить RDM с Omnidesk и delivery channels без изменения
бизнес-состояния от сбоя внешней системы.

**Статус:** PARTIAL.

**Связанные SRS:** REQ-FR-023, 098, 118, 161..165; INT-001..021;
Technical Design §§6, 9–11.

**Уже есть:** Omnidesk read/reopen client, Frame validation, idempotent
notification intents, Telegram/Bitrix24 adapters and worker. Доказательства:
`backend/app/frame/omnidesk.py`, `backend/app/frame/service.py`,
`backend/app/notifications.py`, `backend/app/worker.py`, tests
`test_frame_api.py`, `test_omnidesk_client.py`, `test_notification_runtime.py`.

**Критерий завершения workstream:** every external effect originates from a
durable intent/outbox, is retry-safe, observably classified and covered by
mock contract plus controlled stage E2E.

### IE-01 — Omnidesk write/outbox contract

- **Результат в Git:** durable intents/outbox and adapters for assignment,
completion internal note, client-facing planned notifications and incoming
events; retry/rate-limit/error classification.
- **SRS:** REQ-FR-098, 118; INT-001..021; Technical Design §§9–10.
- **Зависимости:** BE-01, BL-02, BL-03, FI-01; TD-OQ-002/003 must be resolved
before writer payloads.
- **Статус:** PARTIAL — read/reopen and notification delivery exist; the full
Omnidesk writer/event/outbox contract does not.
- **Definition of Done:** mocked client tests cover success, 4xx, retryable,
429 and idempotent repeats; no external response body/secret/internal ID leaks.

### IE-02 — Notification and reminder E2E matrix

- **Результат в Git:** integration fixtures/tests for intent creation, worker
claim/retry, L2/L1/manager recipients, dedupe and disabled-runtime behavior.
- **SRS:** REQ-FR-056..082, 098, 103..105; Technical Design §§9, 11.
- **Зависимости:** BL-01..04, IE-01.
- **Статус:** PARTIAL — unit/runtime tests exist but full business matrix and
current controlled E2E evidence do not.
- **Definition of Done:** each required event is traceable to one durable intent
per recipient/channel/event and failure never rolls back the business change.

### IE-03 — Role-to-role MVP E2E suite

- **Результат в Git:** deterministic E2E scenarios for client → L2 → L1/
manager → completion, including conflicts, retries and timezone paths.
- **SRS:** acceptance criteria §17; REQ-FR-023..165.
- **Зависимости:** FE-01..03, BL-01..04, IE-01..02.
- **Статус:** NOT STARTED.
- **Definition of Done:** suite runs against isolated Compose without real
secrets; controlled external stage smoke is a separate subsequent gate.

## 7. Deployment / staging

**Цель:** доставлять только проверенный commit в повторяемый stage contour,
сохраняя services, data, volumes, migrations и secrets.

**Статус:** PARTIAL.

**Связанные SRS:** NFR-001..029, SEC-001..008; Technical Design §14.

**Уже есть:** Compose, `.env.example`, systemd unit; `PROJECT.md` и README
фиксируют historical stage evidence for selected revisions. Однако текущий RPi
state не проверялся в reconciliation и README migration head устарел.

**Критерий завершения workstream:** stage deployment uses a pinned, published
Git revision, preflight/backup/migration/smoke/rollback evidence and no
unapproved changes to host state.

### DS-01 — Versioned stage deployment contract

- **Результат в Git:** source-controlled stage checklist/config validation,
migration-head consistency check and explicit rollback/preflight procedure.
- **SRS:** NFR-001..006, NFR-020..029; Technical Design §14.
- **Зависимости:** FI-01, DB-01.
- **Статус:** PARTIAL — Compose/systemd exist; current versioned deployment
contract and exact host inventory отсутствуют.
- **Definition of Done:** a clean revision can be preflighted without secrets in
output; migration, image, env and rollback targets are explicit.

### DS-02 — Profiled stage gates

- **Результат в Git:** repeatable, scoped gates for backend, frontend, migration,
notification and Omnidesk E2E, with cleanup proof and outcome record.
- **SRS:** acceptance criteria §17, NFR-001..029.
- **Зависимости:** DS-01 and the corresponding completed FI/DB/BE/BL/FE/IE
milestone.
- **Статус:** PARTIAL — historical gates are documented but not a current
baseline verification.
- **Definition of Done:** each gate states commit SHA, inputs, passed checks,
warnings, cleanup and unavailable checks; failures block promotion.

## 8. Operations / production readiness

**Цель:** обеспечить безопасную эксплуатацию production после полного MVP, а
не использовать stage как замену operational readiness.

**Статус:** NOT STARTED.

**Связанные SRS:** NFR-001..029, SEC-001..008, ADM-001..014; Technical Design
§§14–16, Appendix A.

**Уже есть:** health endpoints, audit log, worker skeleton and runtime flags.
Нет versioned backup/restore proof, metrics/monitoring stack, production secret
management или production acceptance evidence.

**Критерий завершения workstream:** monitored production contour has tested
restore, least-privilege secrets, alerting/runbooks and a go/no-go record after
all prior SRS MVP milestones are done.

### OP-01 — Backup, restore, monitoring and secret operations

- **Результат в Git:** backup/restore automation and verification, metrics/
alerting configuration, structured safe logging, secret rotation/retention
runbooks.
- **SRS:** NFR-001..029, SEC-001..008; Technical Design §§15–16.
- **Зависимости:** DS-01, DB-01, IE-02.
- **Статус:** NOT STARTED.
- **Definition of Done:** restore is proven on isolated stage; dashboards/alerts
cover API, DB, queues, integrations and overdue cards; no secret is committed
or logged.

### OP-02 — Production readiness and controlled go-live

- **Результат в Git:** approved release checklist, rollback plan, production
configuration manifest and acceptance evidence for the exact release SHA.
- **SRS:** all MVP MUST requirements; acceptance criteria §17.
- **Зависимости:** FI-01, DB-01..02, BE-01..03, BL-01..04, FE-01..04,
IE-01..03, DS-01..02, OP-01.
- **Статус:** NOT STARTED.
- **Definition of Done:** all prerequisite gates passed, backup restore and
security review complete, production deployment is separately authorized and
post-deploy smoke/rollback results are recorded.

## Общий порядок

```text
Foundation/FI-01
  → Database/DB-01
  → Backend core/BE-01
  → Backend core/BE-02
  → Database/DB-02
  → Backend core/BE-03
  → Backend business logic/BL-01
  → Backend business logic/BL-02
  → Backend business logic/BL-03
  → Backend business logic/BL-04
  → Integration/E2E/IE-01
  → Integration/E2E/IE-02
  → Integration/E2E/IE-03
  → Deployment/staging/DS-01
  → Deployment/staging/DS-02
  → Operations/OP-01
  → Operations/OP-02

In parallel after the stated backend contracts:
  BE-01 → Frontend/FE-01
  BL-01 + BL-02 → Frontend/FE-02
  BE-03 + BL-02 → Frontend/FE-03
  DB-02 + BL-03 → Frontend/FE-04
```

`DB-01` may be performed immediately after FI-01 and must finish before any
migration-dependent BE/BL work. `FE-01` is implemented alongside BE-01, but
its final acceptance depends on the closed case-number-only backend contract.
`DS-02` is repeated after each eligible milestone; it does not authorize the
next functional milestone and never substitutes for its tests.

## Текущий фокус

- **CURRENT WORKSTREAM:** Backend business logic.
- **CURRENT MILESTONE:** Backend business logic — завершена.
- **NEXT MILESTONE:** IE-01 — NOT STARTED.
- **LAST COMPLETED MILESTONE:** BL-04 — Auto-extension and background policy completion
  (DONE на exact SHA `40ab192`; exact-SHA gate прошёл, commit pushed to `main`);
  BL-03 — Urgent, retroactive, execution and completion
  (DONE на implementation candidate `34aba3362bbfd8bf8ef37a511e540e1f916f232b`;
  финальный exact-SHA gate документации ещё не проводился, внешняя доставка Omnidesk
  не заявляется, REQ-FR-154 не заявляется сверх счётчиков dashboard).
