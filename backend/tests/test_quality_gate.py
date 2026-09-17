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
    assert "alembic upgrade 20260901_0001" in script
    assert "alembic downgrade 20260901_0001" in script
    assert "migration_contract_check.py seed-baseline" in script
    assert script.count("migration_contract_check.py check-head") == 2
    assert "rdm-quality-gate-$$-$RANDOM" in script
    assert script.count('grep -qx "20260904_0005 (head)"') == 2


def test_migration_contract_check_preserves_baseline_data_and_checks_invariants() -> (
    None
):
    checker = (
        ROOT_DIR / "backend" / "scripts" / "migration_contract_check.py"
    ).read_text(encoding="utf-8")
    dockerfile = (ROOT_DIR / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "migration-gate-l2" in checker
    assert "ux_reminder_schedules_one_active_per_kind" in checker
    assert "only one active reminder per card and kind is allowed" in checker
    assert "NON_OVERLAPPING_TICKET" in checker
    assert "ExclusionViolation" in checker
    assert "UniqueViolation" in checker
    assert "COPY scripts ./scripts" in dockerfile


def test_quality_gate_pins_its_linter_version() -> None:
    requirements = (ROOT_DIR / "backend" / "requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "ruff==0.6.9" in requirements


def test_compose_allows_the_gate_to_select_template_env_file() -> None:
    compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    gate_compose = (ROOT_DIR / "docker-compose.quality-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "${RDM_ENV_FILE:-.env}" in compose
    assert "ports: !reset []" in gate_compose
