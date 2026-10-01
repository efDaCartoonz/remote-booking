#!/usr/bin/env bash
set -euo pipefail
set -E  # let the ERR trap fire inside functions

# The whole flow lives in main() and is invoked on the last line: bash parses
# a function completely before running it, so `git checkout` replacing this
# file in the middle of a deploy cannot corrupt the running script.

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly STATE_DIR="$ROOT_DIR/deploy/stage/.state"

usage() {
    cat <<'EOF'
Usage: ./scripts/stage-deploy.sh --sha <COMMIT_SHA> [OPTIONS]

Deploys a specific commit to the RDM Raspberry Pi staging environment.
Run it on the stage host from the repository checkout. It never pushes and
never writes to external systems.

Required arguments:
  --sha <SHA>               Commit SHA (7-40 hex chars). Branch/tag names are rejected.

Options:
  --env-file PATH           Environment file (default: .env.stage or RDM_STAGE_ENV_FILE)
  --allow-delivery          Allow NOTIFICATION_DELIVERY_ENABLED / REMINDER_SCANNER_ENABLED=true
  --fresh                   Recreate the stage database and volumes (DESTROYS stage data)
  --yes-destroy-stage-data  Mandatory confirmation when --fresh is used
  --dry-run                 Print the plan and commands without executing them
  -h, --help                Show this help message and exit

Exit codes:
  0 - Deployment succeeded
  1 - Deployment failed
  2 - Invalid arguments
EOF
}

read_release() {
    # Print RDM_RELEASE from a KEY=VALUE state file, empty when absent.
    local file="$1" line value=""
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^RDM_RELEASE=(.*)$ ]]; then
            value="${BASH_REMATCH[1]}"
            value="${value%\"}"
            value="${value#\"}"
        fi
    done <"$file"
    printf '%s' "$value"
}

main() {
    local target_sha="" env_file="${RDM_STAGE_ENV_FILE:-.env.stage}"
    local allow_delivery=false fresh=false confirmed=false dry_run=false
    local project="${COMPOSE_PROJECT_NAME:-rdm-stage}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --sha)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    printf 'ERROR: --sha requires a hexadecimal commit SHA argument\n' >&2
                    return 2
                fi
                target_sha="$2"
                shift 2
                ;;
            --env-file)
                if [[ $# -lt 2 || -z "${2:-}" ]]; then
                    printf 'ERROR: --env-file requires a path argument\n' >&2
                    return 2
                fi
                env_file="$2"
                shift 2
                ;;
            --allow-delivery) allow_delivery=true; shift ;;
            --fresh) fresh=true; shift ;;
            --yes-destroy-stage-data) confirmed=true; shift ;;
            --dry-run) dry_run=true; shift ;;
            -h|--help) usage; return 0 ;;
            *)
                printf 'ERROR: Unknown option: %s\n\n' "$1" >&2
                usage >&2
                return 2
                ;;
        esac
    done

    if [[ -z "$target_sha" ]]; then
        printf 'ERROR: --sha is mandatory. Provide a commit SHA (e.g. --sha 66c6439c8702)\n' >&2
        return 2
    fi
    if [[ ! "$target_sha" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
        printf 'ERROR: Invalid commit SHA: "%s". Must be a 7 to 40 character hexadecimal string (branch and tag names are rejected).\n' "$target_sha" >&2
        return 2
    fi
    if [[ "$fresh" == true && "$confirmed" != true ]]; then
        printf 'ERROR: --fresh destroys all staging database volumes and requires --yes-destroy-stage-data confirmation.\n' >&2
        return 2
    fi
    if [[ "$env_file" != /* ]]; then
        env_file="$(pwd)/$env_file"
    fi

    local checkout_done=false release="" full_sha="$target_sha"

    run() {
        # Execute (or only print, in --dry-run) one command from the repo root.
        if [[ "$dry_run" == true ]]; then
            printf '[DRY-RUN] Would run: %s\n' "$*"
        else
            (cd "$ROOT_DIR" && "$@")
        fi
    }

    compose() {
        run env RDM_RELEASE="$release" RDM_ENV_FILE="$env_file" \
            docker compose -p "$project" \
            -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.stage.yml" \
            --env-file "$env_file" "$@"
    }

    wait_http() {
        local url="$1" i
        if [[ "$dry_run" == true ]]; then
            printf '[DRY-RUN] Would verify: curl -fsS %s (timeout 120s)\n' "$url"
            return 0
        fi
        for i in $(seq 1 120); do
            if curl -fsS "$url" >/dev/null 2>&1; then
                printf 'OK: %s\n' "$url"
                return 0
            fi
            sleep 1
        done
        printf 'ERROR: Timed out waiting for %s\n' "$url" >&2
        return 1
    }

    on_failure() {
        local code=$?
        printf '\n[ERROR] Deployment aborted with exit code %d.\n' "$code" >&2
        if [[ "$checkout_done" == true ]]; then
            printf 'The working tree is detached at %s. No automatic rollback was done,\n' "$target_sha" >&2
            printf 'so that failure diagnostics stay available. To restore the last good release run:\n' >&2
            printf '    ./scripts/stage-rollback.sh --env-file "%s"\n\n' "$env_file" >&2
        fi
        exit "$code"
    }
    trap on_failure ERR

    printf '=== RDM Stage Deployment ===\n'
    printf 'Target SHA:     %s\n' "$target_sha"
    printf 'Env file:       %s\n' "$env_file"
    printf 'Project name:   %s\n' "$project"
    printf 'Mode:           %s\n\n' "$([[ "$fresh" == true ]] && echo 'FRESH (recreate DB)' || echo 'standard upgrade')"

    printf '[Step 1/7] Fetching and verifying commit %s...\n' "$target_sha"
    if [[ "$dry_run" == true ]]; then
        printf '[DRY-RUN] Would run: git fetch origin\n'
        printf '[DRY-RUN] Would verify: git cat-file -e "%s^{commit}"\n' "$target_sha"
        full_sha="$(printf '%-40s' "$target_sha" | tr ' ' '0')"
    else
        if ! (cd "$ROOT_DIR" && git fetch origin); then
            printf 'WARN: git fetch origin failed; using commits already present locally.\n' >&2
        fi
        if ! (cd "$ROOT_DIR" && git cat-file -e "${target_sha}^{commit}" 2>/dev/null); then
            printf 'ERROR: Commit %s not found in local git history.\n' "$target_sha" >&2
            return 1
        fi
        full_sha="$(cd "$ROOT_DIR" && git rev-parse "${target_sha}^{commit}")"
    fi
    release="${full_sha:0:12}"
    printf 'Resolved full SHA: %s (release tag: %s)\n' "$full_sha" "$release"

    printf '\n[Step 2/7] Checking git working tree and checking out the commit...\n'
    if [[ "$dry_run" == true ]]; then
        printf '[DRY-RUN] Would verify tracked files are clean (git diff-index --quiet HEAD --)\n'
        printf '[DRY-RUN] Would run: git checkout --detach %s\n' "$full_sha"
    else
        if ! (cd "$ROOT_DIR" && git diff-index --quiet HEAD --); then
            printf 'ERROR: Uncommitted tracked changes detected. Commit or stash them before deploying.\n' >&2
            return 1
        fi
        (cd "$ROOT_DIR" && git checkout --detach "$full_sha")
        checkout_done=true
    fi

    # Preflight runs on the NEW tree so it validates the overlay being deployed.
    printf '\n[Step 3/7] Running preflight checks on the checked-out tree...\n'
    local preflight=("$ROOT_DIR/scripts/stage-preflight.sh" --env-file "$env_file")
    if [[ "$allow_delivery" == true ]]; then
        preflight+=(--allow-delivery)
    fi
    if [[ "$dry_run" == true ]]; then
        printf '[DRY-RUN] Would run: %s\n' "${preflight[*]}"
    else
        "${preflight[@]}"
    fi

    local last_good="" attempt_file="$STATE_DIR/deploying.env"
    last_good="$(read_release "$STATE_DIR/release.env")"
    if [[ "$dry_run" != true ]]; then
        mkdir -p "$STATE_DIR"
        printf 'RDM_RELEASE=%s\n' "$release" >"$attempt_file"
    fi

    printf '\n[Step 4/7] Building images for release %s...\n' "$release"
    compose build

    if [[ "$fresh" == true ]]; then
        printf '\n[Step 5/7] --fresh: removing the stage containers and volumes of project %s...\n' "$project"
        compose down --volumes --remove-orphans
    else
        printf '\n[Step 5/7] Standard upgrade: existing volumes are kept.\n'
    fi

    printf '\n[Step 6/7] Starting PostgreSQL/Redis, applying migrations, starting the stack...\n'
    compose up -d --wait --wait-timeout 120 postgres redis
    compose run --rm -T backend alembic upgrade head
    compose up -d --wait --wait-timeout 180
    wait_http http://127.0.0.1:8000/health/ready
    wait_http http://127.0.0.1:8080/
    wait_http http://127.0.0.1:8080/omnidesk-frame.js

    printf '\n[Step 7/7] Recording deployment state (no secrets)...\n'
    if [[ "$dry_run" == true ]]; then
        printf '[DRY-RUN] Would write release.env, previous.env, last-deploy.txt to %s\n' "$STATE_DIR"
    else
        local alembic_current
        alembic_current="$(cd "$ROOT_DIR" && RDM_RELEASE="$release" RDM_ENV_FILE="$env_file" \
            docker compose -p "$project" -f docker-compose.yml -f docker-compose.stage.yml \
            --env-file "$env_file" run --rm -T backend alembic current 2>/dev/null \
            | grep -oE '^[0-9]{8}_[0-9]{4}' | head -1 || true)"
        if [[ -n "$last_good" && "$last_good" != "$release" ]]; then
            printf 'RDM_RELEASE=%s\n' "$last_good" >"$STATE_DIR/previous.env"
        fi
        printf 'RDM_RELEASE=%s\n' "$release" >"$STATE_DIR/release.env"
        rm -f "$attempt_file"
        {
            printf 'DEPLOYED_AT_UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf 'GIT_COMMIT_SHA=%s\n' "$full_sha"
            printf 'RDM_RELEASE=%s\n' "$release"
            printf 'ALEMBIC_CURRENT=%s\n' "${alembic_current:-unknown}"
            printf 'COMPOSE_PROJECT_NAME=%s\n' "$project"
        } >"$STATE_DIR/last-deploy.txt"
    fi

    printf '\n=======================================================\n'
    printf '  RDM staging deployment completed (release %s)\n' "$release"
    printf '  Frontend:      http://localhost:8080/\n'
    printf '  Backend ready: http://localhost:8000/health/ready\n'
    printf '=======================================================\n'
    return 0
}

main "$@"
exit $?
