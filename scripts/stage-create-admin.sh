#!/usr/bin/env bash
set -euo pipefail

# Creates the first administrator of the stage stand. The password is typed
# silently, passed to the container through stdin only (never argv/env) and is
# never printed. An existing username is refused, nothing is overwritten.

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly STATE_DIR="$ROOT_DIR/deploy/stage/.state"

usage() {
    cat <<'EOF'
Usage: ./scripts/stage-create-admin.sh --username NAME --full-name "FULL NAME" [OPTIONS]

Options:
  --email ADDRESS     Optional e-mail of the administrator
  --env-file PATH     Environment file (default: .env.stage or RDM_STAGE_ENV_FILE)
  -h, --help          Show this help message and exit

The stack must be deployed first (./scripts/stage-deploy.sh). Other users and
roles are then created in the /admin interface.

Exit codes: 0 created, 1 failed or user exists, 2 invalid arguments
EOF
}

main() {
    local username="" full_name="" email="" env_file="${RDM_STAGE_ENV_FILE:-.env.stage}"
    local project="${COMPOSE_PROJECT_NAME:-rdm-stage}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --username) username="${2:-}"; shift 2 || { usage >&2; return 2; } ;;
            --full-name) full_name="${2:-}"; shift 2 || { usage >&2; return 2; } ;;
            --email) email="${2:-}"; shift 2 || { usage >&2; return 2; } ;;
            --env-file) env_file="${2:-}"; shift 2 || { usage >&2; return 2; } ;;
            -h|--help) usage; return 0 ;;
            *) printf 'ERROR: Unknown option: %s\n\n' "$1" >&2; usage >&2; return 2 ;;
        esac
    done
    if [[ -z "$username" || -z "$full_name" ]]; then
        printf 'ERROR: --username and --full-name are required\n' >&2
        return 2
    fi
    if [[ "$env_file" != /* ]]; then
        env_file="$(pwd)/$env_file"
    fi
    if [[ ! -f "$STATE_DIR/release.env" ]]; then
        printf 'ERROR: No deployed release recorded (%s). Run stage-deploy.sh first.\n' "$STATE_DIR/release.env" >&2
        return 1
    fi

    local release="" line
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^RDM_RELEASE=(.*)$ ]]; then release="${BASH_REMATCH[1]}"; fi
    done <"$STATE_DIR/release.env"
    if [[ -z "$release" ]]; then
        printf 'ERROR: release.env does not define RDM_RELEASE\n' >&2
        return 1
    fi

    local password="" repeat=""
    if [[ ! -t 0 ]]; then
        printf 'ERROR: run this script from an interactive terminal (the password is typed silently)\n' >&2
        return 2
    fi
    read -r -s -p "Password (8..128 characters): " password
    printf '\n'
    read -r -s -p "Repeat password: " repeat
    printf '\n'
    if [[ "$password" != "$repeat" ]]; then
        printf 'ERROR: passwords do not match\n' >&2
        return 2
    fi

    local args=(--username "$username" --full-name "$full_name" --password-stdin)
    if [[ -n "$email" ]]; then
        args+=(--email "$email")
    fi

    printf '%s\n' "$password" | (
        cd "$ROOT_DIR"
        RDM_RELEASE="$release" RDM_ENV_FILE="$env_file" docker compose -p "$project" \
            -f docker-compose.yml -f docker-compose.stage.yml --env-file "$env_file" \
            run --rm -T -e PYTHONPATH=/app backend python scripts/create_admin.py "${args[@]}"
    )
}

main "$@"
exit $?
