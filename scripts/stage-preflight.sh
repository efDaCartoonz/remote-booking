#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ALLOW_DELIVERY=false
ENV_FILE="${RDM_STAGE_ENV_FILE:-.env.stage}"

usage() {
    cat <<'EOF'
Usage: ./scripts/stage-preflight.sh [OPTIONS]

Preflight check for RDM staging environment on Raspberry Pi 5 / ARM64.

Options:
  --env-file PATH     Path to environment file (default: .env.stage or RDM_STAGE_ENV_FILE)
  --allow-delivery    Allow NOTIFICATION_DELIVERY_ENABLED=true or REMINDER_SCANNER_ENABLED=true
  -h, --help          Show this help message and exit

Exit codes:
  0 - All checks passed (or warned)
  1 - One or more checks failed
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
        --allow-delivery)
            ALLOW_DELIVERY=true
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

# Convert relative ENV_FILE path to absolute relative to current directory if not absolute
if [[ "$ENV_FILE" != /* ]]; then
    ENV_FILE="$(pwd)/$ENV_FILE"
fi

FAILURES=0
WARNINGS=0

report_ok() {
    printf '[OK]   %s\n' "$1"
}

report_warn() {
    printf '[WARN] %s\n' "$1"
    WARNINGS=$((WARNINGS + 1))
}

report_fail() {
    printf '[FAIL] %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

printf '=== RDM Staging Preflight Checks ===\n\n'

# 1. Check docker and docker compose
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    report_ok "Docker and Docker Compose are available"
else
    report_fail "Docker or 'docker compose' is not installed or not in PATH"
fi

# 2. Architecture check
ARCH="$(uname -m)"
report_ok "Host architecture: ${ARCH}"

# 3. Disk space check (>= 5 GB required)
check_disk_space() {
    local target_dir="$ROOT_DIR"
    local avail_kb
    avail_kb="$(df -P -k "$target_dir" 2>/dev/null | tail -n 1 | awk '{print $4}')"
    if [[ -z "$avail_kb" || ! "$avail_kb" =~ ^[0-9]+$ ]]; then
        avail_kb="$(df -k "$target_dir" 2>/dev/null | tail -n 1 | awk '{print $(NF-2)}')"
    fi

    if [[ -n "$avail_kb" && "$avail_kb" =~ ^[0-9]+$ ]]; then
        local req_kb=$((5 * 1024 * 1024))
        local avail_gb=$((avail_kb / 1024 / 1024))
        if (( avail_kb >= req_kb )); then
            report_ok "Free disk space: ~${avail_gb} GB (>= 5 GB required)"
        else
            report_fail "Insufficient disk space: ~${avail_gb} GB available (< 5 GB required)"
        fi
    else
        report_warn "Could not determine free disk space"
    fi
}
check_disk_space

# 4. Check env file existence and permissions
if [[ ! -f "$ENV_FILE" ]]; then
    report_fail "Environment file not found: ${ENV_FILE}"
else
    local_perm_ok=true
    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -c '
import os, sys, stat
st = os.stat(sys.argv[1])
mode = st.st_mode
if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
    sys.exit(1)
sys.exit(0)
' "$ENV_FILE"; then
            local_perm_ok=false
        fi
    else
        local ls_out
        ls_out="$(ls -ld "$ENV_FILE" 2>/dev/null | awk '{print $1}')"
        if [[ "${ls_out: -3}" != "---" ]]; then
            local_perm_ok=false
        fi
    fi

    if [[ "$local_perm_ok" == "true" ]]; then
        report_ok "Environment file exists with secure permissions (no 'others' access)"
    else
        report_fail "Environment file permissions are too open (accessible by others). Run: chmod 600 '${ENV_FILE}'"
    fi
fi

# Parse env file securely without eval/source
declare -A ENV_VARS
if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        if [[ -z "$line" || "$line" =~ ^# ]]; then
            continue
        fi
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
                val="${BASH_REMATCH[1]}"
            fi
            ENV_VARS["$key"]="$val"
        fi
    done < "$ENV_FILE"
fi

# 5. Required keys check (never print secret values!)
REQUIRED_KEYS=(
    "APP_SECRET_KEY"
    "POSTGRES_PASSWORD"
    "DATABASE_URL"
    "REDIS_URL"
    "CELERY_BROKER_URL"
    "CELERY_RESULT_BACKEND"
    "BACKEND_CORS_ORIGINS"
)

for key in "${REQUIRED_KEYS[@]}"; do
    val="${ENV_VARS[$key]:-}"
    if [[ -z "$val" ]]; then
        report_fail "Required variable '${key}' is missing or empty in ${ENV_FILE}"
    else
        val_lower="$(echo "$val" | tr '[:upper:]' '[:lower:]')"
        if [[ "$val_lower" == *"change_me"* || "$val_lower" == *"change-me"* || "$val_lower" == *"rdm-dev-password"* ]]; then
            report_fail "Variable '${key}' contains an unconfigured placeholder/default value"
        else
            report_ok "Required variable '${key}' is configured"
        fi
    fi
done

# 6. Delivery flags check
notif_delivery="${ENV_VARS["NOTIFICATION_DELIVERY_ENABLED"]:-false}"
reminder_scanner="${ENV_VARS["REMINDER_SCANNER_ENABLED"]:-false}"

notif_lower="$(echo "$notif_delivery" | tr '[:upper:]' '[:lower:]')"
scanner_lower="$(echo "$reminder_scanner" | tr '[:upper:]' '[:lower:]')"

if [[ "$notif_lower" == "true" || "$scanner_lower" == "true" ]]; then
    if [[ "$ALLOW_DELIVERY" == "true" ]]; then
        report_warn "External notification delivery / reminder scanner is ENABLED (--allow-delivery specified)"
    else
        report_fail "NOTIFICATION_DELIVERY_ENABLED or REMINDER_SCANNER_ENABLED is true. Use --allow-delivery if intentional on staging."
    fi
else
    report_ok "External notification delivery and reminder scanner are DISABLED (safe default)"
fi

# 6b. Omnidesk outbox: the application default is TRUE, so an absent key is unsafe
omnidesk_outbox="${ENV_VARS["OMNIDESK_OUTBOX_DELIVERY_ENABLED"]:-}"
omnidesk_outbox_lower="$(echo "$omnidesk_outbox" | tr '[:upper:]' '[:lower:]')"
if [[ "$omnidesk_outbox_lower" == "false" ]]; then
    report_ok "Omnidesk outbox delivery is DISABLED (no writes to Omnidesk tickets)"
elif [[ "$ALLOW_DELIVERY" == "true" ]]; then
    report_warn "Omnidesk outbox delivery is ENABLED or unset (--allow-delivery specified): the worker WILL write to Omnidesk tickets"
else
    report_fail "OMNIDESK_OUTBOX_DELIVERY_ENABLED must be set to false (unset means enabled). Use --allow-delivery if writes to Omnidesk are intended."
fi

# 7. CANCELLATION_PUBLIC_BASE_URL check
cancel_url="${ENV_VARS["CANCELLATION_PUBLIC_BASE_URL"]:-}"
if [[ -n "$cancel_url" ]]; then
    if [[ "$cancel_url" =~ ^https:// ]]; then
        report_ok "CANCELLATION_PUBLIC_BASE_URL uses secure HTTPS protocol"
    else
        report_fail "CANCELLATION_PUBLIC_BASE_URL must start with 'https://' (current: non-HTTPS or invalid)"
    fi
else
    report_ok "CANCELLATION_PUBLIC_BASE_URL is not set (optional for standalone test stand)"
fi

# 8. Docker compose config check
check_compose_config() {
    if ! command -v docker >/dev/null 2>&1; then
        report_fail "Cannot validate Compose configuration: docker not found"
        return
    fi

    if [[ ! -f "$ENV_FILE" ]]; then
        report_fail "Cannot validate Compose configuration: env file missing"
        return
    fi

    if (
        cd "$ROOT_DIR"
        RDM_RELEASE="preflight-check-0000" RDM_ENV_FILE="$ENV_FILE" docker compose \
            -f docker-compose.yml \
            -f docker-compose.stage.yml \
            --env-file "$ENV_FILE" \
            config --quiet 2>/dev/null
    ); then
        report_ok "Docker Compose staging configuration is valid"
    else
        report_fail "Docker Compose staging configuration validation failed (check compose syntax or env variables)"
    fi
}
check_compose_config

# 9. Git working tree check
check_git_status() {
    if ! command -v git >/dev/null 2>&1; then
        report_warn "Git binary not found, skipping working tree verification"
        return
    fi

    (
        cd "$ROOT_DIR"
        if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            report_warn "Not inside a git repository, skipping working tree verification"
            return
        fi

        local head_sha
        head_sha="$(git rev-parse HEAD 2>/dev/null || echo "unknown")"

        if git diff-index --quiet HEAD -- 2>/dev/null; then
            report_ok "Git tracked files are clean (HEAD: ${head_sha})"
        else
            report_fail "Git working tree has uncommitted changes in tracked files"
        fi
    )
}
check_git_status

# 10. Source Alembic migration head check
find_source_alembic_head() {
    local versions_dir="$ROOT_DIR/backend/alembic/versions"
    if [[ ! -d "$versions_dir" ]]; then
        report_fail "Alembic versions directory not found: ${versions_dir}"
        return
    fi

    local head_rev=""
    if command -v python3 >/dev/null 2>&1; then
        head_rev="$(python3 -c '
import sys, re, glob, os

versions_dir = sys.argv[1]
revisions = set()
down_revisions = set()

rev_pattern = re.compile(r"""^revision(?:\s*:\s*[^=]+)?\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]""", re.MULTILINE)
down_pattern = re.compile(r"""^down_revision(?:\s*:\s*[^=]+)?\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]""", re.MULTILINE)

for filepath in glob.glob(os.path.join(versions_dir, "*.py")):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            m_rev = rev_pattern.search(content)
            if m_rev:
                revisions.add(m_rev.group(1))
            m_down = down_pattern.search(content)
            if m_down:
                down_revisions.add(m_down.group(1))
    except Exception:
        pass

heads = revisions - down_revisions
if len(heads) == 1:
    print(list(heads)[0])
elif len(heads) > 1:
    print(f"MULTIPLE_HEADS:{sys.argv[1]}")
    sys.exit(1)
else:
    print("NO_HEAD")
    sys.exit(1)
' "$versions_dir" 2>/dev/null || true)"
    else
        local revs downs
        revs="$(grep -E "^revision\s*=" "$versions_dir"/*.py 2>/dev/null | sed -E "s/.*revision\s*=\s*['\"]([^'\"]+)['\"].*/\1/" | sort -u)"
        downs="$(grep -E "^down_revision\s*=" "$versions_dir"/*.py 2>/dev/null | grep -v "None" | sed -E "s/.*down_revision\s*=\s*['\"]([^'\"]+)['\"].*/\1/" | sort -u)"
        head_rev="$(comm -23 <(echo "$revs") <(echo "$downs") | tr -d '\n')"
    fi

    if [[ -n "$head_rev" && "$head_rev" != *"MULTIPLE_HEADS"* && "$head_rev" != *"NO_HEAD"* ]]; then
        report_ok "Source Alembic migration head: ${head_rev}"
    else
        report_fail "Could not determine unique Alembic migration head (result: ${head_rev})"
    fi
}
find_source_alembic_head

printf '\n=== Summary: %d failures, %d warnings ===\n' "$FAILURES" "$WARNINGS"

if (( FAILURES > 0 )); then
    printf 'Preflight checks FAILED. Please resolve the errors above before deploying.\n' >&2
    exit 1
fi

printf 'Preflight checks PASSED successfully.\n'
exit 0
