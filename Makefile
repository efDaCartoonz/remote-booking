.DEFAULT_GOAL := verify

.PHONY: verify verify-backend verify-frontend verify-compose verify-migrations

verify:
	./scripts/quality-gate.sh all

verify-backend:
	./scripts/quality-gate.sh backend

verify-frontend:
	./scripts/quality-gate.sh frontend

verify-compose:
	./scripts/quality-gate.sh compose

verify-migrations:
	./scripts/quality-gate.sh migrations
