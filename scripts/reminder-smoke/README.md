# Reminder Smoke Harness

Run only with a unique project name and a copied `.env.example` outside the
working stage configuration. The Compose network is internal and exposes no
ports. Use `docker compose run --rm backend alembic upgrade head` for migration
and one-shot Python checks. Cleanup must target the same project name only.
