#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly MODE="${1:-all}"
readonly PYTHON_BIN="${PYTHON_BIN:-python3}"
readonly NPM_BIN="${NPM_BIN:-npm}"
readonly COMPOSE_BIN="${COMPOSE_BIN:-docker compose}"
readonly GATE_PROJECT="${RDM_GATE_PROJECT:-rdm-quality-gate}"
readonly GATE_COMPOSE_FILES="-f docker-compose.yml -f docker-compose.quality-gate.yml"

usage() {
    cat <<'EOF'
Usage: ./scripts/quality-gate.sh {all|backend|frontend|compose|migrations}

Runs only checks that use source-controlled inputs. The migration smoke creates
an isolated Docker Compose project and removes its containers and volumes when
it finishes. It never reads a local .env file: .env.example is used instead.

Optional command overrides: PYTHON_BIN, NPM_BIN, COMPOSE_BIN, RDM_GATE_PROJECT.
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
    require_command "$PYTHON_BIN"
    (
        cd "$ROOT_DIR/backend"
        "$PYTHON_BIN" -m pytest tests
        "$PYTHON_BIN" -m ruff check --no-cache app tests
        "$PYTHON_BIN" -m ruff format --check --no-cache app tests
    )
}

run_frontend() {
    require_command "$NPM_BIN"
    (
        cd "$ROOT_DIR/frontend"
        "$NPM_BIN" ci
        "$NPM_BIN" run test
        "$NPM_BIN" run build
    )
}

run_compose_config() {
    require_command docker
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN --env-file .env.example config --quiet
    )
}

cleanup_migration_project() {
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example down --volumes --remove-orphans
    ) >/dev/null 2>&1 || true
}

run_migrations() {
    require_command docker
    trap cleanup_migration_project EXIT
    (
        cd "$ROOT_DIR"
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example up --detach postgres
        RDM_ENV_FILE=.env.example $COMPOSE_BIN $GATE_COMPOSE_FILES --project-name "$GATE_PROJECT" \
            --env-file .env.example run --rm backend sh -ec \
            'alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic current && alembic heads'
    )
}

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
