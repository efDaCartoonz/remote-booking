# Omnidesk case index rollout

Status note (24 September 2026): migration `20260914_0006` is on `main`,
and manager preflight/create resolve `case_number` through the internal index
with live Omnidesk verification. The candidate note below records the state
on 15 September, not the present Git state. Incremental sync and the stage
database state were not checked in this documentation pass.

## Stage 1 — internal index and manual backfill

Create the local `omnidesk_case_index`, checkpoint state, and conflict ledger.
Run the backfill only through the explicit CLI entrypoint. Keep manager API,
frontend, Frame API, Celery Beat, and periodic jobs unchanged. Do not store
secrets or response bodies and report only aggregate statistics.

### Historical candidate status — 15 September 2026

The local candidate is implemented in migration `20260914_0006` and commits
`6f53bbc` through `2815d2e`. It includes resumable checkpoints, conflict
ledger, bounded pages, dry-run, advisory locking, row-level isolation, and
page rollback that keeps the checkpoint consistent. Ruff was unavailable in
the current environment, so the full quality gate and PostgreSQL migration
smoke remain unverified. The candidate has not been published or applied to
stage; no manager lookup switch has been made.

## Stage 2 — verified incremental sync

Add the overlap-based `updated_at` sync and a separately reviewed worker. It
must have independent limits, retries, checkpoints, and operational metrics.

## Stage 3 — manager lookup switch

Implemented on `main`: manager preflight/create use a single public
`case_number`, resolve it through the internal index, and verify the ticket
again against Omnidesk before creating a card. This documents code state;
current stage data and index freshness were not checked here.

Frame API remains unchanged until a separate architecture decision.
