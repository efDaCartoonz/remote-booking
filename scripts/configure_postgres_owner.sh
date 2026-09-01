#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

db_name="${RDM_DB_NAME:-rdm}"
db_owner="${RDM_DB_OWNER:-nimda}"
password_file="${RDM_PASSWORD_FILE:-db-password-nimda.txt}"

if [[ ! "$db_name" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "Invalid database name: $db_name" >&2
    exit 1
fi

if [[ ! "$db_owner" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "Invalid database owner: $db_owner" >&2
    exit 1
fi

if [[ ! -f "$password_file" ]]; then
    echo "Password file not found: $password_file" >&2
    exit 1
fi

db_password="$(tr -d '\n' < "$password_file")"

if [[ -z "$db_password" ]]; then
    echo "Password file is empty: $password_file" >&2
    exit 1
fi

if [[ "$db_password" == *"'"* ]]; then
    echo "Password contains unsupported quote character" >&2
    exit 1
fi

docker compose up -d postgres

for _ in {1..30}; do
    health_status="$(docker inspect --format='{{.State.Health.Status}}' "${PWD##*/}-postgres-1" 2>/dev/null || true)"
    if [[ "$health_status" == "healthy" ]]; then
        break
    fi
    sleep 2
done

health_status="$(docker inspect --format='{{.State.Health.Status}}' "${PWD##*/}-postgres-1" 2>/dev/null || true)"
if [[ "$health_status" != "healthy" ]]; then
    echo "PostgreSQL container is not healthy: $health_status" >&2
    exit 1
fi

role_exists="$(
    docker compose exec -T postgres sh -c \
        "export PGPASSWORD=\"\$POSTGRES_PASSWORD\"; psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"SELECT 1 FROM pg_roles WHERE rolname = '$db_owner';\"" \
    | tr -d '[:space:]'
)"

if [[ "$role_exists" == "1" ]]; then
    role_sql="ALTER ROLE ${db_owner} WITH LOGIN PASSWORD '${db_password}';"
else
    role_sql="CREATE ROLE ${db_owner} WITH LOGIN PASSWORD '${db_password}';"
fi

{
    printf "%s\n" "$role_sql"
    printf "ALTER DATABASE %s OWNER TO %s;\n" "$db_name" "$db_owner"
    printf "\\connect %s\n" "$db_name"
    printf "ALTER SCHEMA public OWNER TO %s;\n" "$db_owner"
    printf "GRANT ALL PRIVILEGES ON DATABASE %s TO %s;\n" "$db_name" "$db_owner"
    printf "GRANT ALL ON SCHEMA public TO %s;\n" "$db_owner"
} | docker compose exec -T postgres sh -c \
    'export PGPASSWORD="$POSTGRES_PASSWORD"; psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'

echo "PostgreSQL owner is configured: $db_owner"
