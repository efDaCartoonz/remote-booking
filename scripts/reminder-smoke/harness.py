"""Isolated, fail-closed Compose runner for the reminder e2e suite."""
from __future__ import annotations
import argparse, os, re, secrets, subprocess, sys, tempfile, time
from pathlib import Path

PREFIX = "rdm-reminder-smoke-"
PROJECT_RE = re.compile(r"^rdm-reminder-smoke-[a-z0-9]{12}$")
SERVICES = ("postgres", "redis", "stub", "worker", "beat")
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "scripts/reminder-smoke/compose.yml"

def project_name() -> str: return PREFIX + secrets.token_hex(6)
def validate_project_name(value: str) -> str:
    if not PROJECT_RE.fullmatch(value) or not value.startswith(PREFIX): raise ValueError("unsafe compose project name")
    return value
def wait_until(predicate, *, timeout: float, interval: float = 1.0, label: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate(): return
        except Exception: pass
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {label}")
def compose(project: str, env: dict[str, str], *args: str, check=True):
    return subprocess.run(["docker", "compose", "-p", validate_project_name(project), "-f", str(COMPOSE), *args], cwd=ROOT, env=env, text=True, check=check)
def cleanup(project: str, env: dict[str, str]) -> None:
    """Remove only this validated Compose project; never run global prune."""
    validate_project_name(project)
    compose(project, env, "down", "--volumes", "--remove-orphans")
    result = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"], text=True, capture_output=True, check=True)
    if result.stdout.strip(): raise RuntimeError("compose project still has containers")
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--timeout", type=float, default=180); args = parser.parse_args()
    project = validate_project_name(project_name()); env = os.environ.copy(); env_file = None
    try:
        with tempfile.NamedTemporaryFile("w", prefix=PREFIX, suffix=".env", delete=False) as handle:
            handle.write((ROOT / "scripts/reminder-smoke/.env.example").read_text().replace("REMINDER_L2_INTERVAL_SECONDS=2", "REMINDER_L2_INTERVAL_SECONDS=1")); env_file = Path(handle.name)
        env["SMOKE_ENV_FILE"] = str(env_file); env["COMPOSE_PROJECT_NAME"] = project
        compose(project, env, "config"); compose(project, env, "up", "-d", *SERVICES)
        compose(project, env, "run", "--rm", "fixture", "python", "/smoke/fixture.py")
        compose(project, env, "run", "--rm", "scenarios", "python", "/smoke/scenarios.py", "--timeout", str(args.timeout))
        print(f"reminder smoke passed: project={project}"); return 0
    except Exception as exc:
        print(f"reminder smoke failed: {exc}", file=sys.stderr); return 1
    finally:
        try: cleanup(project, env)
        finally:
            if env_file: env_file.unlink(missing_ok=True)
if __name__ == "__main__": raise SystemExit(main())
