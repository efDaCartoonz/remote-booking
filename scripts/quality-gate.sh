#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly MODE="${1:-all}"
readonly COMPOSE_BIN="${COMPOSE_BIN:-docker compose}"
readonly GATE_PROJECT="${RDM_GATE_PROJECT:-rdm-quality-gate-$$-$RANDOM}"
readonly GATE_COMPOSE_FILES="-f docker-compose.yml -f docker-compose.quality-gate.yml"

usage() {
    cat <<'EOF'
Usage: ./scripts/quality-gate.sh {all|backend|frontend|compose|migrations}

Runs only checks that use source-controlled inputs in isolated Docker Compose
containers. It removes its containers and volumes when it finishes. It never
reads a local .env file: .env.example is used instead.

Optional command overrides: COMPOSE_BIN, RDM_GATE_PROJECT.
EOF
}

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'UNAVAILABLE: required command not found: %s\n' "$command_name" >&2
        exit 127
    fi
}

run_backend() {
    require_command docker
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example run --build --rm quality-backend
    )
}

run_frontend() {
    require_command docker
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example run --build --rm quality-frontend
    )
}

run_compose_config() {
    require_command docker
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN --env-file .env.example config --quiet
    )
}

cleanup_gate_project() {
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example down --volumes --remove-orphans
    ) >/dev/null 2>&1 || true
}

run_migrations() {
    require_command docker
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example up --detach postgres
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example run --rm backend sh -ec \
            'alembic upgrade 20260901_0001 \
             && PYTHONPATH=/app python scripts/migration_contract_check.py seed-baseline \
             && alembic upgrade head \
             && PYTHONPATH=/app python scripts/migration_contract_check.py check-head \
             && alembic downgrade 20260901_0001 \
             && PYTHONPATH=/app python scripts/migration_contract_check.py check-baseline \
             && alembic upgrade head \
             && PYTHONPATH=/app python scripts/migration_contract_check.py check-head \
             && alembic current | grep -qx "20260914_0006 (head)" \
             && alembic heads | grep -qx "20260914_0006 (head)"'
    )
}

# The project name is generated per run, so cleanup cannot affect the user's
# Compose stack even when a check fails partway through.
trap cleanup_gate_project EXIT

case "$MODE" in
    all)
        run_backend
        run_frontend
        run_compose_config
        run_migrations
        ;;
    backend) run_backend ;;
    frontend) run_frontend ;;
    compose) run_compose_config ;;
    migrations) run_migrations ;;
    -h|--help) usage ;;
    *)
        usage >&2
        exit 2
        ;;
esac
