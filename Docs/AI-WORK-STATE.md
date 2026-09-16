# RDM — текущий operational state

Дата: 16 сентября 2026.
Baseline: `origin/main` = `7ab7f38b16ce9b40204ad80b06e94e6f0d452666`.

## Текущее состояние

- **WS-01 / FI-01 — Воспроизводимый quality gate: DONE.** В Git добавлены
  `Makefile`, `scripts/quality-gate.sh`, Compose no-port overlay и native
  frontend test entrypoint. Gate использует только `.env.example`, создаёт
  отдельный Docker project для migration smoke и выполняет cleanup.
- Проверено для FI-01 revision: backend `144 passed, 6 skipped`, Ruff check и
  format; frontend `npm ci`, native test и production build; Compose config;
  PostgreSQL `upgrade → downgrade → upgrade` до head `20260904_0005`.
- Stage verification выполнялась на временном Git archive с project name
  `rdm-fi01-6909fb6-r2`; stage checkout, `.env`, работающие services и volumes
  не изменялись. После проверки временные containers, volumes и `/tmp` каталог
  отсутствуют.
- **WS-02 / DB-01 — Schema-contract reconciliation и миграционный gate: DONE.**
  `make verify-migrations` теперь проверяет на изолированном PostgreSQL 16
  `0001 → head → 0001 → head`, сохранность seeded baseline data и invariants
  active-ticket, L2-overlap и active reminder schedule. README фиксирует head
  `20260904_0005`.

## Следующий разрешённый фокус

- **CURRENT WORKSTREAM:** Backend core.
- **CURRENT MILESTONE:** BE-01 — безопасный case-number-only manager-create.
- **NEXT MILESTONE:** BE-02 — централизованная RBAC и action policy; другой
  scope, не начинать автоматически.

Полная последовательность и Definition of Done находятся в
[`RDM-ROADMAP.md`](RDM-ROADMAP.md). Состояние реализации prior baseline и
доказательства reconciliation-аудита сохранены в Git history и связанной
документации; этот файл намеренно остаётся кратким.
