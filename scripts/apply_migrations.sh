#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

docker compose up -d postgres redis
docker compose build backend
docker compose run --rm backend alembic -c alembic.ini upgrade head

echo "Database migrations applied"
