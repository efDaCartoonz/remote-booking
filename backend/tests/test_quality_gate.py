from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_makefile_exposes_all_quality_gate_parts() -> None:
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    for target in (
        "verify:",
        "verify-backend:",
        "verify-frontend:",
        "verify-compose:",
        "verify-migrations:",
    ):
        assert target in makefile


def test_quality_gate_uses_template_env_and_isolated_migration_project() -> None:
    script = (ROOT_DIR / "scripts" / "quality-gate.sh").read_text(encoding="utf-8")

    assert "--env-file .env.example" in script
    assert "RDM_ENV_FILE=.env.example" in script
    assert '--project-name "$GATE_PROJECT"' in script
    assert "down --volumes --remove-orphans" in script
    assert "alembic downgrade base" in script


def test_compose_allows_the_gate_to_select_template_env_file() -> None:
    compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    gate_compose = (ROOT_DIR / "docker-compose.quality-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "${RDM_ENV_FILE:-.env}" in compose
    assert "ports: !reset []" in gate_compose
