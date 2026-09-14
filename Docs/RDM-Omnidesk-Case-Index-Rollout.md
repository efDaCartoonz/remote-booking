# Omnidesk case index rollout

## Stage 1 — internal index and manual backfill

Create the local `omnidesk_case_index`, checkpoint state, and conflict ledger.
Run the backfill only through the explicit CLI entrypoint. Keep manager API,
frontend, Frame API, Celery Beat, and periodic jobs unchanged. Do not store
secrets or response bodies and report only aggregate statistics.

## Stage 2 — verified incremental sync

Add the overlap-based `updated_at` sync and a separately reviewed worker. It
must have independent limits, retries, checkpoints, and operational metrics.

## Stage 3 — manager lookup switch

After the index is proven complete and fresh, switch manager contracts to a
single `case_number` and keep live verification immediately before creation.

Frame API remains unchanged until a separate architecture decision.
