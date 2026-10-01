from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_stage_files_presence() -> None:
    expected_files = [
        ROOT_DIR / "docker-compose.stage.yml",
        ROOT_DIR / "deploy" / "stage" / "env.stage.example",
        ROOT_DIR / "scripts" / "stage-preflight.sh",
        ROOT_DIR / "scripts" / "stage-deploy.sh",
        ROOT_DIR / "scripts" / "stage-rollback.sh",
        ROOT_DIR / "deploy" / "systemd" / "rdm-stage.service",
        ROOT_DIR / "deploy" / "stage" / "README.md",
    ]
    for path in expected_files:
        assert path.is_file(), f"Expected stage file missing: {path}"


def test_compose_stage_overlay_contract() -> None:
    compose_stage = (ROOT_DIR / "docker-compose.stage.yml").read_text(encoding="utf-8")

    assert "${RDM_RELEASE:?set RDM_RELEASE}" in compose_stage
    assert "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}" in compose_stage
    assert "127.0.0.1:5432:5432" in compose_stage
    assert "127.0.0.1:8000:8000" in compose_stage
    assert "curl" in compose_stage
    assert "healthcheck:" in compose_stage
    assert "rdm-backend:${RDM_RELEASE:?set RDM_RELEASE}" in compose_stage
    assert "rdm-frontend:${RDM_RELEASE:?set RDM_RELEASE}" in compose_stage
    assert "${RDM_ENV_FILE:-.env.stage}" in compose_stage


def test_systemd_stage_service_contract() -> None:
    service = (ROOT_DIR / "deploy" / "systemd" / "rdm-stage.service").read_text(
        encoding="utf-8"
    )

    assert "WorkingDirectory=/home/user-rdm/remote-booking-new" in service
    assert (
        "EnvironmentFile=-/home/user-rdm/remote-booking-new/deploy/stage/.state/release.env"
        in service
    )
    assert "-p rdm-stage" in service
    assert "-f docker-compose.stage.yml" in service
    assert "--env-file .env.stage" in service
    assert "RemainAfterExit=yes" in service


def test_scripts_safety_and_static_rules() -> None:
    scripts = [
        ROOT_DIR / "scripts" / "stage-preflight.sh",
        ROOT_DIR / "scripts" / "stage-deploy.sh",
        ROOT_DIR / "scripts" / "stage-rollback.sh",
    ]

    for script_path in scripts:
        content = script_path.read_text(encoding="utf-8")
        assert (
            "set -euo pipefail" in content
        ), f"{script_path} missing set -euo pipefail"
        assert "eval " not in content, f"{script_path} contains unsafe eval"
        assert "rm -rf" not in content, f"{script_path} contains unsafe rm -rf"
        assert "system prune" not in content, f"{script_path} contains system prune"

    deploy_content = (ROOT_DIR / "scripts" / "stage-deploy.sh").read_text(
        encoding="utf-8"
    )
    # Volumes are removed in exactly one place, and --fresh is refused
    # without the explicit confirmation flag.
    assert deploy_content.count("--volumes") == 1
    assert "down --volumes" in deploy_content
    assert '"$fresh" == true && "$confirmed" != true' in deploy_content
    assert "--yes-destroy-stage-data" in deploy_content


@pytest.fixture
def mock_environment(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    bin_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)

    docker_mock = bin_dir / "docker"
    docker_mock.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{log_dir}/docker.log"
if [[ "$1" == "compose" && "$2" == "version" ]]; then
    echo "Docker Compose version v2.29.1"
    exit 0
fi
if [[ "$1" == "inspect" ]]; then
    echo '"healthy"'
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    docker_mock.chmod(0o755)

    git_mock = bin_dir / "git"
    git_mock.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{log_dir}/git.log"
if [[ "$1" == "rev-parse" ]]; then
    if [[ "${{2:-}}" == "--is-inside-work-tree" ]]; then
        exit 0
    fi
    echo "66c6439c87021dc6d29b6162e42f16c547fde64e"
    exit 0
fi
if [[ "$1" == "diff-index" ]]; then
    exit 0
fi
if [[ "$1" == "cat-file" ]]; then
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    git_mock.chmod(0o755)

    curl_mock = bin_dir / "curl"
    curl_mock.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{log_dir}/curl.log"
exit 0
""",
        encoding="utf-8",
    )
    curl_mock.chmod(0o755)

    df_mock = bin_dir / "df"
    df_mock.write_text(
        """#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/disk1 100000000 20000000 80000000 20% /"
exit 0
""",
        encoding="utf-8",
    )
    df_mock.chmod(0o755)

    return bin_dir, log_dir, tmp_path


def create_valid_env_stage(target: Path, secrets: dict[str, str] | None = None) -> None:
    sec = secrets or {}
    secret_key = sec.get("APP_SECRET_KEY", "valid_secure_secret_key_1234567890")
    pg_password = sec.get("POSTGRES_PASSWORD", "valid_secure_pg_pass_1234567890")
    notif_enabled = sec.get("NOTIFICATION_DELIVERY_ENABLED", "false")
    scanner_enabled = sec.get("REMINDER_SCANNER_ENABLED", "false")
    outbox_enabled = sec.get("OMNIDESK_OUTBOX_DELIVERY_ENABLED", "false")

    content = f"""
APP_ENV=stage
APP_NAME=Remote Desktop Manager
APP_SECRET_KEY={secret_key}

POSTGRES_DB=rdm
POSTGRES_USER=nimda
POSTGRES_PASSWORD={pg_password}
DATABASE_URL=postgresql+psycopg://nimda:{pg_password}@postgres:5432/rdm

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

BACKEND_CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080

NOTIFICATION_DELIVERY_ENABLED={notif_enabled}
REMINDER_SCANNER_ENABLED={scanner_enabled}
{"" if outbox_enabled == "UNSET" else f"OMNIDESK_OUTBOX_DELIVERY_ENABLED={outbox_enabled}"}
"""
    target.write_text(content.strip(), encoding="utf-8")
    target.chmod(0o600)


def test_stage_deploy_cli_validations(
    mock_environment: tuple[Path, Path, Path],
) -> None:
    bin_dir, _, tmp_path = mock_environment
    env_file = tmp_path / ".env.stage"
    create_valid_env_stage(env_file)

    script = str(ROOT_DIR / "scripts" / "stage-deploy.sh")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}

    # 1. Missing --sha -> exit 2
    res = subprocess.run(
        [script, "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 2
    assert "--sha is mandatory" in res.stderr

    # 2. Branch name instead of hex sha -> exit 2
    res = subprocess.run(
        [script, "--sha", "main", "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 2
    assert "Invalid commit SHA" in res.stderr

    # 3. --fresh without --yes-destroy-stage-data -> exit 2
    res = subprocess.run(
        [script, "--sha", "66c6439c8702", "--fresh", "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 2
    assert "--yes-destroy-stage-data" in res.stderr


def test_stage_deploy_dry_run_does_not_mutate(
    mock_environment: tuple[Path, Path, Path],
) -> None:
    bin_dir, log_dir, tmp_path = mock_environment
    env_file = tmp_path / ".env.stage"
    create_valid_env_stage(env_file)

    script = str(ROOT_DIR / "scripts" / "stage-deploy.sh")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}

    res = subprocess.run(
        [
            script,
            "--sha",
            "66c6439c87021dc6d29b6162e42f16c547fde64e",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, f"Dry run failed: {res.stderr}"

    # Verify no git checkout and no docker up/build/run commands executed
    docker_log = log_dir / "docker.log"
    git_log = log_dir / "git.log"

    if docker_log.exists():
        docker_calls = docker_log.read_text(encoding="utf-8")
        assert "up" not in docker_calls
        assert "build" not in docker_calls
        assert "run" not in docker_calls

    if git_log.exists():
        git_calls = git_log.read_text(encoding="utf-8")
        assert "checkout" not in git_calls


def test_stage_preflight_checks_and_secret_redaction(
    mock_environment: tuple[Path, Path, Path],
) -> None:
    bin_dir, _, tmp_path = mock_environment
    script = str(ROOT_DIR / "scripts" / "stage-preflight.sh")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}

    sentinel_app_secret = "UNIQUE_APP_SECRET_SENTINEL_XYZ_98765"
    sentinel_pg_secret = "UNIQUE_PG_SECRET_SENTINEL_ABC_54321"

    env_file = tmp_path / ".env.stage"
    create_valid_env_stage(
        env_file,
        secrets={
            "APP_SECRET_KEY": sentinel_app_secret,
            "POSTGRES_PASSWORD": sentinel_pg_secret,
        },
    )

    # Valid preflight check
    res = subprocess.run(
        [script, "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, f"Preflight failed: {res.stdout}\n{res.stderr}"
    # Secrets MUST NEVER appear in stdout or stderr
    assert sentinel_app_secret not in res.stdout
    assert sentinel_app_secret not in res.stderr
    assert sentinel_pg_secret not in res.stdout
    assert sentinel_pg_secret not in res.stderr

    # Placeholder password check -> code 1
    create_valid_env_stage(
        env_file,
        secrets={"POSTGRES_PASSWORD": "CHANGE_ME_PASSWORD"},
    )
    res_placeholder = subprocess.run(
        [script, "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_placeholder.returncode == 1
    assert (
        "POSTGRES_PASSWORD" in res_placeholder.stderr
        or "POSTGRES_PASSWORD" in res_placeholder.stdout
    )

    # Delivery enabled without flag -> code 1
    create_valid_env_stage(
        env_file,
        secrets={"NOTIFICATION_DELIVERY_ENABLED": "true"},
    )
    res_delivery = subprocess.run(
        [script, "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_delivery.returncode == 1

    # Delivery enabled WITH --allow-delivery -> code 0 (warn only)
    res_delivery_allowed = subprocess.run(
        [script, "--env-file", str(env_file), "--allow-delivery"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_delivery_allowed.returncode == 0
    assert "[WARN]" in res_delivery_allowed.stdout


def _run_preflight(tmp_path: Path, secrets: dict[str, str], *extra: str):
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {
        "docker": "#!/usr/bin/env bash\nexit 0\n",
        "git": "#!/usr/bin/env bash\nexit 0\n",
    }.items():
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    env_file = tmp_path / ".env.stage"
    create_valid_env_stage(env_file, secrets)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "scripts" / "stage-preflight.sh"),
            "--env-file",
            str(env_file),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT_DIR,
    )


def test_preflight_fails_when_omnidesk_outbox_delivery_enabled(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, {"OMNIDESK_OUTBOX_DELIVERY_ENABLED": "true"})
    assert result.returncode == 1
    assert "OMNIDESK_OUTBOX_DELIVERY_ENABLED" in result.stderr


def test_preflight_fails_when_omnidesk_outbox_flag_is_unset(tmp_path: Path) -> None:
    # The application default is true, so an absent key must not pass.
    result = _run_preflight(tmp_path, {"OMNIDESK_OUTBOX_DELIVERY_ENABLED": "UNSET"})
    assert result.returncode == 1
    assert "OMNIDESK_OUTBOX_DELIVERY_ENABLED" in result.stderr


def test_preflight_omnidesk_outbox_allowed_with_flag_only_as_warning(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path, {"OMNIDESK_OUTBOX_DELIVERY_ENABLED": "true"}, "--allow-delivery"
    )
    assert "OMNIDESK_OUTBOX_DELIVERY_ENABLED must be set" not in result.stderr
    assert "WILL write to Omnidesk" in result.stdout


def test_stage_env_template_disables_omnidesk_outbox() -> None:
    template = (ROOT_DIR / "deploy" / "stage" / "env.stage.example").read_text(
        encoding="utf-8"
    )
    assert "OMNIDESK_OUTBOX_DELIVERY_ENABLED=false" in template
