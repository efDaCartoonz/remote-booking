# RDM — фактическое состояние разработки

Дата reconciliation-аудита: 16 сентября 2026.
Git baseline: `origin/main` = `7ab7f38b16ce9b40204ad80b06e94e6f0d452666` (`fix: validate manager card creation form`).

Reconciliation 17 сентября 2026: локальный development baseline — merge
`7771821`; он объединяет текущую локальную линию с FI-01 (`1643140`) и DB-01
(`6de632d`). FI-01 и DB-01 завершены в этой локальной линии. Дальнейшие
разделы сохраняют read-only audit удалённого `origin/main` от 16 сентября и не
являются описанием текущего локального baseline.

BE-01 reconciliation: manager browser contract использует только
`case_number`; preflight URL, request и response не содержат внутренний
Omnidesk `case_id`. Внутренний resolver читает local case index, безопасно
отказывает для отсутствующего/ambiguous/unavailable тикета и затем повторно
проверяет тикет через Omnidesk. Backend regression и Frame suite пройдены;
Docker-only quality gate на `269b7ca` подтвердил PostgreSQL migration smoke,
frontend production build, backend/Frame regression и cleanup isolated
resources. **WS-03 / BE-01: DONE.**

BE-02 completion: `CardAction` policy централизует server-side checks для
create/assign/confirm/reject/start/complete/cancel/reschedule и L1 follow-up.
Role/ownership violations возвращают `403`, state violations — `409`; проверка
выполняется после блокирующего чтения и до записи, actor сохраняется в event и
audit. Docker-only quality gate на `8e5dc19`: `191 passed, 6 skipped`, frontend
tests/build, Compose validation и PostgreSQL migration contract PASS. **WS-03 /
BE-02: DONE**; следующий milestone — DB-02, не начат.

BL-01 completion: exact-SHA isolated acceptance на `10f71dd` прошёл на
PostgreSQL 16. Подтверждены manager reassignment, overdue L2 → L1, trusted
Omnidesk reschedule, persisted cycle/attempt/reminder/event/audit,
idempotency/repeat и stale/conflict behavior. Full candidate gate: backend
`221 passed, 13 skipped`, PostgreSQL behavioral `3 passed`, Ruff/format,
frontend tests/build, Compose config и migration `0001 → head → 0001 → head`.
Использовались только temporary Compose projects и `.env.example`; stage
checkout, `.env`, services и persistent volumes не изменялись. **WS-04 /
BL-01: DONE.**

BL-02 completion: exact-SHA isolated acceptance на `bee0f7d` подтвердил
централизованные ограничения времени и отмены: role/state/API matrix,
scheduling window и availability, urgent out-of-hours exception, manager
cancel для `in_progress`, освобождение L2 reservation, закрытие активных
cycle/attempt и reminder cleanup. Full candidate gate: backend
`284 passed, 18 skipped`, Ruff check/format, frontend tests/build, Compose
validation и migration `0001 → head → 0001 → head`; отдельная PostgreSQL
behavioral matrix — `118 passed`. Temporary archive checkout, containers и
volumes очищены; stage checkout, `.env`, services и persistent volumes не
изменялись. **WS-04 / BL-02: DONE.**

BL-03 implementation scope: в локальном workspace завершена реализация
срочных коллизий, ретроспективной регистрации, исполнения и завершения работ:
- вытеснение планов в статусах ASSIGNED и CONFIRMED при срочных коллизиях с
  автоматическим переназначением либо переводом в REJECTED→L1 follow-up, а
  также со сбросом флага и времени просрочки (overdue_flag, overdue_at);
- безопасный отказ при коллизии с карточкой в статусе IN_PROGRESS без каких-либо
  мутаций в БД (карточки, события и аудит остаются неизменными);
- ретроспективная саморегистрация L2 (retroactive self-registration):
  автоматический расчёт фактической длительности выполняется при создании уже
  завершённой ретроспективной карточки, а не при последующем завершении карточки
  in_progress (обычное завершение может принимать опциональную длительность);
  запрет неактивных кодов результата и отложенное создание outbox intent (для
  in_progress intent не создаётся до момента завершения);
- валидация активных кодов результата по каталогу connection_results
  (запрет неизвестных/неактивных кодов);
- при старте карточки (start_card) фиксируется только время фактического начала
  (actual_start_at); код результата и фактическая длительность при старте не
  фиксируются (фиксируются при завершении);
- идемпотентное создание intent во внутренний outbox заметок Omnidesk
  (omnidesk_internal_note_outbox) при завершении карточки без раскрытия case_id
  (заметка Omnidesk представляет собой только идемпотентный внутренний outbox
  intent, внешняя доставка относится к IE-01);
- генерация событий (CardEventType.URGENT_COLLISION), записей аудита и
  уведомлений;
- метрики срочных коллизий в панели руководителя (подсчёт суммарного количества
  событий urgent_collision по карточкам выборки; REQ-FR-154 не заявляется
  полностью закрытым: реализованы только суммарные счётчики в dashboard summary).
Границы SRS: BL-03 охватывает REQ-FR-099..125, 144..145 и частичную реализацию
REQ-FR-154 (только dashboard summary counters; REQ-FR-154 не заявляется полностью
закрытым; требования REQ-FR-137..143 и 156..160 не входят в скоуп BL-03).
Автопродление сессий BL-04 имеет статус NOT STARTED и владеет REQ-FR-137..143.
Статус и доказательства: создан целевой scoped коммит `09cb6cc`.
Локальные сфокусированные проверки: `75 passed, 7 skipped`, проверки Ruff
check и format PASS. Обязательный exact-SHA quality gate (`make verify`)
недоступен из-за отсутствия Docker в локальном окружении (`UNAVAILABLE:
required command not found: docker`). Статус DONE не достигнут; финальный
exact-SHA Docker/PostgreSQL gate, stage, deployment и push не заявляются.
Milestone BL-03 остаётся NOT DONE в ожидании isolated exact-SHA Docker/PostgreSQL
gate. **WS-04 / BL-03: NOT DONE (scoped commit `09cb6cc` created, gate unavailable: Docker missing).**

## Границы и метод

Это отчёт о содержимом актуального удалённого Git baseline, а не о локальной
рабочей ветке. На момент аудита локальный `main` находится на 10 коммитов
впереди `origin/main` и имеет незакоммиченные изменения документации; они не
учитываются как реализованное состояние. `git diff --check origin/main` не
выявил пробельных ошибок в разнице локальной ветки, но это не является
функциональной проверкой.

Приоритет доказательств: Git/код `origin/main`, затем локальный `PROJECT.md`,
затем `Docs/RDM-SRS.md`, `Docs/RDM-Concept.md` и
`Docs/RDM-Technical-Design-v0.1.md`. Старые чаты и планы не использовались как
доказательство. Тесты, Docker, PostgreSQL, stage и RPi в этом аудите не
запускались и не изменялись.

Операционный порядок закрытия выявленных разрывов приведён в
[`Docs/RDM-ROADMAP.md`](RDM-ROADMAP.md). Этот файл сохраняет только
доказанный статус baseline и не дублирует roadmap.

Последние значимые коммиты baseline:

| Commit | Факт по diff |
| --- | --- |
| `741acc3` | Добавлен backend-контракт создания карточки руководителем. |
| `641aad2`, `39c6daa`, `c7105eb` | Корректировки валидации тикета и конфликтов назначения. |
| `abf0580` | Восстановлен follow-up после переназначения L2. |
| `6fc49c8`, `7ab7f38` | Добавлена и уточнена Vue-форма создания карточки руководителем. |

## IMPLEMENTED

### PostgreSQL и доменная основа

- Alembic baseline содержит последовательность `20260901_0001` →
  `20260903_0002` → `20260904_0003` → `20260904_0004` → `20260904_0005`.
  Начальная миграция создаёт пользователей/роли/сессии, клиентов,
  `connection_cards`, события, циклы и попытки назначения, графики/отсутствия,
  пулы распределения, notification intents, аудит и системные настройки.
  Ограничения включают публичный UUID, уникальный номер, формат номера тикета,
  длительность и exclusion constraint пересечений L2. Доказательства:
  `backend/alembic/versions/20260901_0001_initial_schema.py`,
  `backend/alembic/versions/20260904_0005_reminder_schedules.py`.
- Домен карточки и базовый lifecycle представлены статусами `created`,
  `assigned`, `confirmed`, `in_progress`, `rejected`, `completed`, `cancelled`,
  таблицей допустимых переходов, историей и audit log. Доказательства:
  `backend/app/cards/constants.py`, `backend/app/cards/service.py`,
  `backend/app/cards/repository.py`, `backend/tests/test_cards.py` (19 тестов).
- Внутренняя аутентификация использует пароль, cookie с хешированным session
  token, срок действия и отзыв сессии. Пользователь получает набор ролей из
  `user_roles`; доступны `login`, `logout`, `me`. Доказательства:
  `backend/app/api/auth.py`, `backend/app/auth/dependencies.py`,
  `backend/app/auth/store.py`, `backend/tests/test_auth.py` (5 тестов).
- Frame-контур отделён от внутренней cookie-аутентификации: создаёт привязанную
  к origin session, сверяет тикет Omnidesk и даёт чтение/создание карточек в
  рамках session. Доказательства: `backend/app/api/frame.py`,
  `backend/app/frame/service.py`, `backend/app/frame/sessions.py`,
  `backend/tests/test_frame_api.py` (15 тестов),
  `backend/tests/test_omnidesk_client.py` (7 тестов).
- Автоматическое L2-распределение реализует eligible candidates, проверку
  графика/absence/пересечения и Round Robin. Оно создаёт `assignment_cycle` и
  pending `assignment_attempt`, назначает L2 либо переводит карточку в
  `rejected`; отказ запускает выбор следующего кандидата. Доказательства:
  `backend/app/assignments/service.py`, `backend/app/assignments/repository.py`,
  `backend/tests/test_l2_distribution.py` (7 тестов),
  `backend/tests/test_cards.py`.
- Ручное L2-назначение для manager endpoint проверяет роль/активность/доступность
  кандидата и также создаёт cycle/attempt. Доказательства:
  `backend/app/api/manager.py`, `backend/app/assignments/service.py`,
  `backend/tests/test_manager_create_postgres.py` (5 тестов),
  commit `741acc3`.
- Follow-up L1 после отсутствия кандидатов или полного отказа L2 реализован с
  отдельным L1 Round Robin, предпочтением создавшего карточку L1 и эскалацией
  при отсутствии L1. Доказательства: `backend/app/assignments/l1_service.py`,
  `backend/tests/test_l1_distribution.py` (12 тестов),
  `backend/tests/test_manager_escalation.py` (5 тестов).
- Reminder schedules реализованы в миграции `20260904_0005`: активное
  расписание на пару card/kind, claim с `FOR UPDATE SKIP LOCKED`, L2/L1
  эскалация, закрытие при confirm/terminal status и overdue flag. Доказательства:
  `backend/alembic/versions/20260904_0005_reminder_schedules.py`,
  `backend/app/reminders.py`, `backend/app/cards/service.py`,
  `backend/tests/test_reminders.py` (10 тестов).
- Notification intents сохраняются идемпотентно; worker умеет claim/retry/fail,
  а адаптеры Telegram и Bitrix24 изолированы от intent creation. Доказательства:
  `backend/alembic/versions/20260904_0003_notification_runtime.py`,
  `backend/alembic/versions/20260904_0004_idempotent_notification_intents.py`,
  `backend/app/notifications.py`, `backend/app/worker.py`,
  `backend/tests/test_notification_runtime.py` (24 теста).

### Доступные API и frontend

- Реализованы health API, auth API, internal card API
  (create/read/history/assign/confirm/reject/start/complete/cancel/L1 follow-up),
  Frame API и manager API (list/cards, L2 options, ticket preflight, create).
  Доказательства: `backend/app/main.py`, `backend/app/api/cards.py`,
  `backend/app/api/frame.py`, `backend/app/api/manager.py`,
  `backend/app/api/health.py`.
- Vue frontend содержит login/logout, internal card detail с действиями L1/L2,
  manager dashboard/list/calendar и manager-create форму. Сборочный скрипт
  frontend существует (`vue-tsc --noEmit && vite build`), но frontend test
  suite отсутствует. Доказательства: `frontend/src/App.vue`,
  `frontend/src/style.css`, `frontend/package.json`, commits `6fc49c8`,
  `7ab7f38`.
- Compose описывает PostgreSQL 16, Redis 7, backend, worker, beat и nginx/Vue;
  присутствует systemd unit для `/home/user-rdm/remote-booking-new`. Доказательства:
  `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`,
  `deploy/systemd/rdm-compose.service`, `.env.example`.

## PARTIAL

### RBAC и lifecycle

- Реализованы проверка наличия ролей и ownership checks для решения назначенного
  L2 и назначенного L1. Однако generic endpoints `assign`, `start`, `complete`
  и `cancel` требуют только аутентификацию, а не явно manager/L2/owner policy;
  сервис переходов также не получает роли актёра. Поэтому соответствие полной
  матрице SRS (`REQ-FR-001..005`) частичное. Доказательства:
  `backend/app/auth/dependencies.py`, `backend/app/api/cards.py`,
  `backend/app/cards/service.py`, `backend/tests/test_auth.py`,
  `backend/tests/test_cards.py`.
- Lifecycle и L2/L1 follow-up покрывают базовые переходы, но не доказывают весь
  SRS: отдельные сценарии overdue → назначение L1, работа вне графика,
  срочность, ретроспективные карточки, изменение времени клиентом и завершение
  по всем политикам не имеют выделенного endpoint/UI/test coverage. Код хранит
  соответствующие флаги, но их бизнес-правила не образуют полный контракт.
  Доказательства: `backend/app/cards/schemas.py`, `backend/app/cards/service.py`,
  `backend/app/reminders.py`, `backend/tests/test_cards.py`, `Docs/RDM-SRS.md`
  (`REQ-FR-009..082`, `REQ-FR-126..165`).
- Создание карточек существует для manager (`/api/v1/manager/cards`) и клиента
  через Frame. Общий `/api/v1/cards` также допускает только manager. Отдельных
  контрактов/endpoint'ов создания L1 и self/urgent/retroactive L2, требуемых
  `REQ-FR-024`, `REQ-FR-026..029`, в baseline нет. Доказательства:
  `backend/app/api/cards.py`, `backend/app/api/frame.py`,
  `backend/app/api/manager.py`, `backend/app/cards/schemas.py`,
  `Docs/RDM-SRS.md`.
- Reminder и intent runtime реализованы, но в `.env.example` оба runtime flag
  выключены по умолчанию; текущий audit не выполнял live delivery/scanner или
  stage smoke. `README.md` и `PROJECT.md` описывают прежние stage-gates, что
  является документальным свидетельством, но не live-подтверждением на 16
  сентября. Доказательства: `.env.example`, `backend/app/worker.py`,
  `README.md`, `PROJECT.md`.
- Deployment/RPi конфигурация есть, но репозиторий не содержит отдельного
  versioned stage inventory или проверенного текущего состояния хоста. Более
  того, `README.md` указывает migration head `20260904_0004`, тогда как Git
  содержит `20260904_0005`; эта документация нуждается в синхронизации в
  отдельной, разрешённой документационной работе. Доказательства:
  `README.md`, `docker-compose.yml`, `deploy/systemd/rdm-compose.service`,
  `backend/alembic/versions/20260904_0005_reminder_schedules.py`.

### Безопасный backend-контракт создания карточки руководителем после baseline `ce4802a`

**Не завершён; статус PARTIAL.** Commit `741acc3` добавил manager-create,
последующие `641aad2`, `39c6daa`, `c7105eb`, `6fc49c8` и `7ab7f38` улучшили
валидацию, конфликтную обработку и форму. Но контракт на `origin/main` всё ещё:

- требует `case_id` в `ManagerCreateRequest`;
- передаёт `case_id` в URL preflight (`/tickets/{case_id}/preflight`);
- возвращает `case_id` в preflight response;
- заставляет Vue-пользователя заполнить поле «ID обращения» и отправляет его в
  `POST /api/v1/manager/cards`.

Следовательно, browser-facing manager flow не является case-number-only и не
соблюдает требуемую границу, при которой внутренний Omnidesk `case_id` остаётся
только в backend/DB/Redis. Доказательства: `backend/app/manager_create.py`,
`backend/app/api/manager.py`, `frontend/src/App.vue`,
`backend/tests/test_manager_create_contract.py` (5 тестов),
`backend/tests/test_manager_omnidesk_reopen.py` (2 теста), commit `741acc3`.
В `origin/main` отсутствует миграция/код internal case-number index или
однозначного server-side lookup, поэтому безопасную замену утверждать нельзя.

## NOT IMPLEMENTED

- Полный SRS MVP не реализован: нет отдельных frontend/API контуров L1, L2,
  admin, reporting и клиентского frame UI, предусмотренных SRS; текущий Vue
  является внутренним минимальным UI. Доказательства: `frontend/src/App.vue`,
  `frontend/package.json`, `Docs/RDM-SRS.md` разделы 8 и 17.
- Не найдены endpoint'ы/тесты для управления ролями, пользователями, графиками,
  отсутствиями, production calendar, distribution membership и templates;
  таблицы есть, публичного слоя управления нет. Доказательства:
  `backend/alembic/versions/20260901_0001_initial_schema.py`,
  `backend/app/api/`, `Docs/RDM-SRS.md` раздел 13.
- Не найдены реализованные API/UI для планового ручного переназначения manager,
  отмены manager за L2 с причиной, клиентского self-reschedule/cancel,
  срочных/внерабочих/ретроспективных flows, продления выполняющейся сессии и
  отчётности. Доказательства: `backend/app/api/cards.py`,
  `backend/app/api/manager.py`, `frontend/src/App.vue`, `Docs/RDM-SRS.md`
  (`REQ-FR-053..054`, `REQ-FR-083..165`).
- Frontend automated tests отсутствуют; в `frontend/package.json` нет `test`.
  Не выполнено и не заявляется живое подтверждение origin/main на текущем RPi.
  Доказательства: `frontend/package.json`, `README.md`,
  `deploy/systemd/rdm-compose.service`.

## Матрица ключевых требований SRS

| SRS | Backend | Frontend | Tests | Итог |
| --- | --- | --- | --- | --- |
| `REQ-FR-001..005` роли/RBAC | Sessions и role dependencies есть; policy неполна для generic card actions | Login и видимость manager entry | `test_auth.py`, часть `test_cards.py` | PARTIAL |
| `REQ-FR-009..022` lifecycle | Статусы, transitions, history/audit | Card detail отображает и вызывает часть действий | `test_cards.py` | PARTIAL |
| `REQ-FR-023` клиент/frame create | Frame sessions, Omnidesk check, create | Отдельного frame UI нет | `test_frame_api.py`, `test_omnidesk_client.py` | PARTIAL |
| `REQ-FR-024..035` все способы создания | Manager и generic manager-only create; L1/L2 special flows нет | Только manager create | `test_manager_create_*`, `test_cards.py` | PARTIAL |
| `REQ-FR-036..054` L2 distribution/decision | Auto RR, cycles/attempts, manual manager assignment, confirm/reject | L2 confirm/reject visible in card view | `test_l2_distribution.py`, `test_manager_create_postgres.py` | PARTIAL |
| `REQ-FR-055..082` reminders/L1 follow-up | Schedules, scanner, L1 assignment/escalation | Нет dedicated reminder/L1 queue UI | `test_reminders.py`, `test_l1_distribution.py`, `test_manager_escalation.py` | PARTIAL |
| `REQ-FR-083..145` reschedule, urgent, work, completion, extension | Лишь L1 rejected reschedule и base start/complete; остальное не найдено | Нет специальных flows | `test_cards.py` | NOT IMPLEMENTED |
| `REQ-FR-146..165` reports, out-of-hours, client timezone | Поля частично хранятся; policy/API/reporting нет | Browser timezone только в manager view | Нет выделенных тестов | NOT IMPLEMENTED |
| API/security manager create | Backend contract есть, но public `case_id` leak | Форма требует/sends `case_id` | `test_manager_create_contract.py`, `test_manager_omnidesk_reopen.py` | PARTIAL |
| Technical Design deployment | Compose/systemd/worker/beat есть | Nginx/Vite bundle | Нет versioned deployment test | PARTIAL |

## Ровно один следующий milestone

**BL-04 — Автопродление и background policy completion.** Создан scoped commit
`09cb6cc` для BL-03, но milestone BL-03 остаётся NOT DONE в ожидании недоступного
exact-SHA Docker/PostgreSQL quality gate. Milestone BL-04 имеет статус
NOT STARTED, владеет REQ-FR-137..143 и не начинается автоматически.
