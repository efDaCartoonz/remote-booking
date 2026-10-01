#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_FILE="${RDM_STAGE_ENV_FILE:-.env.stage}"
DOWNGRADE_REV=""
DRY_RUN=false
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-rdm-stage}"

usage() {
    cat <<'EOF'
Usage: ./scripts/stage-rollback.sh [OPTIONS]

Rolls back the RDM staging deployment to the previous recorded release.

Options:
  --env-file PATH             Path to environment file (default: .env.stage or RDM_STAGE_ENV_FILE)
  --downgrade-to REV          Optional Alembic migration revision to downgrade database to before rollback
  --dry-run                   Print rollback plan and commands without modifying state
  -h, --help                  Show this help message and exit

Exit codes:
  0 - Rollback succeeded
  1 - Rollback failed or no previous release recorded
  2 - Invalid arguments
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            if [[ $# -lt 2 || -z "${2:-}" ]]; then
                printf 'ERROR: --env-file requires a path argument\n' >&2
                exit 2
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --downgrade-to)
            if [[ $# -lt 2 || -z "${2:-}" ]]; then
                printf 'ERROR: --downgrade-to requires an Alembic revision argument\n' >&2
                exit 2
            fi
            DOWNGRADE_REV="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: Unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Convert relative ENV_FILE path to absolute
if [[ "$ENV_FILE" != /* ]]; then
    ENV_FILE="$(pwd)/$ENV_FILE"
fi

STATE_DIR="$ROOT_DIR/deploy/stage/.state"
PREVIOUS_ENV_FILE="$STATE_DIR/previous.env"
CURRENT_ENV_FILE="$STATE_DIR/release.env"

# 1. Pick the rollback target.
# A deploy that stopped midway leaves deploying.env behind: the last good
# release is then release.env. After a finished deploy it is previous.env.
ATTEMPT_ENV_FILE="$STATE_DIR/deploying.env"

read_release() {
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

if [[ -f "$ATTEMPT_ENV_FILE" ]]; then
    TARGET_FILE="$CURRENT_ENV_FILE"
    printf 'Unfinished deploy detected: rolling back to the last good release.\n'
else
    TARGET_FILE="$PREVIOUS_ENV_FILE"
fi

if [[ ! -f "$TARGET_FILE" ]]; then
    printf 'ERROR: No rollback target recorded at %s. Cannot perform automated rollback.\n' "$TARGET_FILE" >&2
    exit 1
fi

PREV_RELEASE="$(read_release "$TARGET_FILE")"
if [[ -z "$PREV_RELEASE" ]]; then
    printf 'ERROR: %s is empty or does not define RDM_RELEASE.\n' "$TARGET_FILE" >&2
    exit 1
fi

if [[ -f "$ATTEMPT_ENV_FILE" ]]; then
    CURRENT_RELEASE="$(read_release "$ATTEMPT_ENV_FILE")"
else
    CURRENT_RELEASE="$(read_release "$CURRENT_ENV_FILE")"
fi

printf '=== RDM Stage Rollback ===\n'
printf 'Current release:  %s\n' "${CURRENT_RELEASE:-unknown}"
printf 'Target rollback:  %s\n' "$PREV_RELEASE"
printf 'Env file:         %s\n' "$ENV_FILE"
printf 'Project name:     %s\n' "$COMPOSE_PROJECT_NAME"
if [[ -n "$DOWNGRADE_REV" ]]; then
    printf 'DB Downgrade to:  %s\n' "$DOWNGRADE_REV"
fi
printf '\n'

# 2. Check that target rollback images exist locally
printf '[Step 1/4] Checking availability of previous release images (release %s)...\n' "$PREV_RELEASE"
if [[ "$DRY_RUN" == "true" ]]; then
    printf '[DRY-RUN] Would inspect: docker image inspect rdm-backend:%s and rdm-frontend:%s\n' \
        "$PREV_RELEASE" "$PREV_RELEASE"
else
    if ! docker image inspect "rdm-backend:${PREV_RELEASE}" >/dev/null 2>&1; then
        printf 'ERROR: Docker image rdm-backend:%s not found locally. Cannot rollback to missing image.\n' "$PREV_RELEASE" >&2
        exit 1
    fi
    if ! docker image inspect "rdm-frontend:${PREV_RELEASE}" >/dev/null 2>&1; then
        printf 'ERROR: Docker image rdm-frontend:%s not found locally. Cannot rollback to missing image.\n' "$PREV_RELEASE" >&2
        exit 1
    fi
    printf 'Found required images: rdm-backend:%s and rdm-frontend:%s\n' "$PREV_RELEASE" "$PREV_RELEASE"
fi

COMPOSE_CMD=(
    docker compose
    -p "$COMPOSE_PROJECT_NAME"
    -f "$ROOT_DIR/docker-compose.yml"
    -f "$ROOT_DIR/docker-compose.stage.yml"
    --env-file "$ENV_FILE"
)

# 3. If downgrade requested, run Alembic downgrade on current image first
if [[ -n "$DOWNGRADE_REV" ]]; then
    printf '\n[Step 2/4] Running Alembic downgrade to revision %s...\n' "$DOWNGRADE_REV"
    RUN_IMG_RELEASE="${CURRENT_RELEASE:-$PREV_RELEASE}"
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '[DRY-RUN] Would run: RDM_RELEASE=%s RDM_ENV_FILE=%s %s run --rm -T backend alembic downgrade %s\n' \
            "$RUN_IMG_RELEASE" "$ENV_FILE" "${COMPOSE_CMD[*]}" "$DOWNGRADE_REV"
    else
        (
            cd "$ROOT_DIR"
            RDM_RELEASE="$RUN_IMG_RELEASE" RDM_ENV_FILE="$ENV_FILE" "${COMPOSE_CMD[@]}" \
                run --rm -T backend alembic downgrade "$DOWNGRADE_REV"
        )
    fi
else
    printf '\n[Step 2/4] Database downgrade not requested (schema remains as is).\n'
fi

# 4. Start previous release containers
printf '\n[Step 3/4] Starting previous release containers (release %s)...\n' "$PREV_RELEASE"
if [[ "$DRY_RUN" == "true" ]]; then
    printf '[DRY-RUN] Would run: RDM_RELEASE=%s RDM_ENV_FILE=%s %s up -d\n' \
        "$PREV_RELEASE" "$ENV_FILE" "${COMPOSE_CMD[*]}"
    printf '[DRY-RUN] Would wait for backend /health/ready\n'
else
    (
        cd "$ROOT_DIR"
        RDM_RELEASE="$PREV_RELEASE" RDM_ENV_FILE="$ENV_FILE" "${COMPOSE_CMD[@]}" up -d

        printf 'Waiting for backend /health/ready...\n'
        for i in {1..120}; do
            if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
                printf 'Backend /health/ready is OK.\n'
                break
            fi
            if [[ $i -eq 120 ]]; then
                printf 'ERROR: Timed out waiting for backend /health/ready after rollback.\n' >&2
                exit 1
            fi
            sleep 1
        done
    )
fi

# 5. Update state files
printf '\n[Step 4/4] Updating state records...\n'
if [[ "$DRY_RUN" == "true" ]]; then
    printf '[DRY-RUN] Would update %s to RDM_RELEASE=%s\n' "$CURRENT_ENV_FILE" "$PREV_RELEASE"
else
    cat > "$CURRENT_ENV_FILE" <<EOF
RDM_RELEASE=${PREV_RELEASE}
EOF
    rm -f "$ATTEMPT_ENV_FILE"

    cat >> "$STATE_DIR/last-deploy.txt" <<EOF
ROLLBACK_AT_UTC=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ROLLED_BACK_TO_RELEASE=${PREV_RELEASE}
DOWNGRADE_REV=${DOWNGRADE_REV:-none}
EOF
fi

printf '\n=======================================================\n'
printf '  RDM Staging Rollback Completed Successfully!\n'
printf '  Active Release: %s\n' "$PREV_RELEASE"
printf '  Backend Ready:  http://localhost:8000/health/ready\n'
printf '=======================================================\n'
exit 0
