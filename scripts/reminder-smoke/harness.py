#!/usr/bin/env python3
"""Isolated, fail-closed Compose runner for the reminder e2e suite."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PREFIX = "rdm-reminder-smoke-"
PROJECT_RE = re.compile(r"^rdm-reminder-smoke-[a-z0-9]{12}$")
SERVICES = ("postgres", "redis", "stub", "backend", "worker", "beat")
LONG_RUNNING_SERVICES = ("stub", "backend", "worker", "beat")
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "scripts/reminder-smoke/compose.yml"


class TemporaryProbeError(RuntimeError):
    """A startup condition that is expected to become ready during polling."""


class PreflightError(RuntimeError):
    """The isolated Compose definition does not meet its safety contract."""


def project_name() -> str:
    return PREFIX + secrets.token_hex(6)


def validate_project_name(value: str) -> str:
    if not PROJECT_RE.fullmatch(value) or not value.startswith(PREFIX):
        raise ValueError("unsafe compose project name")
    return value


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 1.0,
    label: str = "condition",
    retry_exceptions: tuple[type[Exception], ...] = (TemporaryProbeError,),
) -> None:
    """Wait for a condition without hiding unexpected implementation failures."""
    deadline = time.monotonic() + timeout
    last_reason: str | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except retry_exceptions as exc:
            last_reason = type(exc).__name__
        time.sleep(interval)
    detail = f"; last temporary error={last_reason}" if last_reason else ""
    raise TimeoutError(f"timed out waiting for {label}{detail}")


def compose(
    project: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            validate_project_name(project),
            "-f",
            str(COMPOSE),
            *args,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        check=check,
        capture_output=capture_output,
    )


def validate_compose_config(config: dict[str, Any]) -> None:
    """Check the rendered Compose definition before isolated containers start."""
    services = config.get("services")
    if not isinstance(services, dict):
        raise PreflightError("compose config has no services mapping")

    missing = sorted(set(SERVICES) - set(services))
    if missing:
        raise PreflightError(f"compose config is missing required services: {missing}")

    for service_name in LONG_RUNNING_SERVICES:
        command = services[service_name].get("command")
        if not command:
            raise PreflightError(f"compose service {service_name} has no command")

    worker_command = " ".join(map(str, services["worker"]["command"]))
    if "--queues=notifications" not in worker_command:
        raise PreflightError("worker does not listen to notifications")

    if any(service.get("ports") for service in services.values()):
        raise PreflightError("isolated Compose project must not publish external ports")

    networks = config.get("networks")
    smoke_network = networks.get("smoke") if isinstance(networks, dict) else None
    if not isinstance(smoke_network, dict) or not smoke_network.get("internal"):
        raise PreflightError("smoke network must be internal")


def preflight(project: str, env: dict[str, str]) -> None:
    rendered = compose(project, env, "config", "--format", "json", capture_output=True)
    validate_compose_config(json.loads(rendered.stdout))


def cleanup(project: str, env: dict[str, str]) -> None:
    """Remove only this validated Compose project; never run global prune."""
    validate_project_name(project)
    compose(project, env, "down", "--volumes", "--remove-orphans")
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    if result.stdout.strip():
        raise RuntimeError("compose project still has containers")


def _remove_env_file(env_file: Path | None) -> None:
    if env_file is not None:
        env_file.unlink()


def run_smoke(project: str, env: dict[str, str], timeout: float) -> None:
    preflight(project, env)
    compose(project, env, "up", "-d", *SERVICES)
    compose(project, env, "run", "--rm", "fixture", "python", "/smoke/fixture.py")
    compose(
        project,
        env,
        "run",
        "--rm",
        "scenarios",
        "python",
        "/smoke/scenarios.py",
        "--timeout",
        str(timeout),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    project = validate_project_name(project_name())
    env = os.environ.copy()
    env_file: Path | None = None
    primary_error: Exception | None = None
    cleanup_error: Exception | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w", prefix=PREFIX, suffix=".env", delete=False
        ) as handle:
            env_file = Path(handle.name)
            handle.write(
                (ROOT / "scripts/reminder-smoke/.env.example")
                .read_text()
                .replace(
                    "REMINDER_L2_INTERVAL_SECONDS=2", "REMINDER_L2_INTERVAL_SECONDS=1"
                )
            )
        env["SMOKE_ENV_FILE"] = str(env_file)
        env["COMPOSE_PROJECT_NAME"] = project
        run_smoke(project, env, args.timeout)
    except (
        OSError,
        PreflightError,
        RuntimeError,
        subprocess.SubprocessError,
        TimeoutError,
    ) as exc:
        primary_error = exc
    finally:
        try:
            cleanup(project, env)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            cleanup_error = exc
        try:
            _remove_env_file(env_file)
        except OSError as exc:
            cleanup_error = cleanup_error or exc

    if primary_error is not None:
        print(f"reminder smoke failed: {primary_error}", file=sys.stderr)
    if cleanup_error is not None:
        print(f"reminder smoke cleanup failed: {cleanup_error}", file=sys.stderr)
    if primary_error is not None or cleanup_error is not None:
        return 1

    print(f"reminder smoke passed: project={project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
