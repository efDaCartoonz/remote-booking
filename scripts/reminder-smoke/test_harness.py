from __future__ import annotations

import subprocess

import harness
import pytest


def test_project_name_guard() -> None:
    assert harness.validate_project_name(harness.PREFIX + "0123456789ab")
    with pytest.raises(ValueError):
        harness.validate_project_name("rdm-reminder-smoke-../../x")
    with pytest.raises(ValueError):
        harness.validate_project_name("other-0123456789ab")


def test_required_compose_cleanup_is_scoped() -> None:
    assert "docker system prune" not in harness.cleanup.__doc__.lower()


def test_report_shape_has_no_sensitive_fields() -> None:
    report = {"telegram": {"calls": 1, "mode": "success", "codes": [200]}}
    assert not {"token", "recipient", "message", "body"} & set(report)


def compose_config(*, services: dict[str, dict] | None = None) -> dict:
    required = {name: {"command": ["command"]} for name in harness.SERVICES}
    required["worker"] = {"command": ["celery", "--queues=notifications"]}
    required["backend"] = {"command": ["uvicorn"]}
    if services is not None:
        required = services
    return {"services": required, "networks": {"smoke": {"internal": True}}}


def test_preflight_rejects_missing_backend() -> None:
    config = compose_config()
    del config["services"]["backend"]
    with pytest.raises(harness.PreflightError, match="backend"):
        harness.validate_compose_config(config)


def test_compose_failure_reports_safe_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        harness, "project_name", lambda: harness.PREFIX + "0123456789ab"
    )
    monkeypatch.setattr(
        harness,
        "run_smoke",
        lambda *_: (_ for _ in ()).throw(
            subprocess.CalledProcessError(7, ["docker", "compose"])
        ),
    )
    monkeypatch.setattr(harness, "cleanup", lambda *_: None)
    monkeypatch.setattr(harness, "_remove_env_file", lambda _: None)
    monkeypatch.setattr("sys.argv", ["harness.py"])
    assert harness.main() == 1
    assert "exit_code=7" in capsys.readouterr().err


def test_load_smoke_env_reads_only_simple_pairs(tmp_path) -> None:
    env_file = tmp_path / "smoke.env"
    env_file.write_text("# comment\nA=one\nB=two=three\n")
    assert harness.load_smoke_env(env_file) == {"A": "one", "B": "two=three"}


def test_smoke_env_exposes_application_package_path() -> None:
    smoke_env = harness.ROOT / "scripts/reminder-smoke/.env.example"
    assert harness.load_smoke_env(smoke_env)["PYTHONPATH"] == "/app"


def test_load_smoke_env_rejects_invalid_line(tmp_path) -> None:
    env_file = tmp_path / "smoke.env"
    env_file.write_text("not-an-assignment\n")
    with pytest.raises(harness.PreflightError, match="invalid line"):
        harness.load_smoke_env(env_file)


def test_wait_until_times_out_with_safe_last_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)

    def pending() -> bool:
        raise harness.TemporaryProbeError("not-ready")

    with pytest.raises(TimeoutError, match="TemporaryProbeError"):
        harness.wait_until(pending, timeout=1, interval=0, label="stub")


def test_wait_until_retries_expected_temporary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)

    def eventually_ready() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise harness.TemporaryProbeError("starting")
        return True

    harness.wait_until(eventually_ready, timeout=1, interval=0)
    assert attempts == 2


def test_wait_until_does_not_hide_unexpected_exception() -> None:
    with pytest.raises(ValueError, match="bug"):
        harness.wait_until(lambda: (_ for _ in ()).throw(ValueError("bug")), timeout=1)


@pytest.mark.parametrize("run_error", [None, RuntimeError("scenario failed")])
def test_main_runs_cleanup_after_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, run_error: Exception | None
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        harness, "project_name", lambda: harness.PREFIX + "0123456789ab"
    )
    monkeypatch.setattr(harness, "_remove_env_file", lambda _: None)
    monkeypatch.setattr(harness, "cleanup", lambda *_: calls.append("cleanup"))

    def fake_run(*_args: object) -> None:
        calls.append("run")
        if run_error is not None:
            raise run_error

    monkeypatch.setattr(harness, "run_smoke", fake_run)
    monkeypatch.setattr("sys.argv", ["harness.py"])
    assert harness.main() == (1 if run_error else 0)
    assert calls == ["run", "cleanup"]


def test_main_surfaces_cleanup_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        harness, "project_name", lambda: harness.PREFIX + "0123456789ab"
    )
    monkeypatch.setattr(harness, "run_smoke", lambda *_: None)
    monkeypatch.setattr(harness, "_remove_env_file", lambda _: None)
    monkeypatch.setattr(
        harness,
        "cleanup",
        lambda *_: (_ for _ in ()).throw(subprocess.SubprocessError("cleanup")),
    )
    monkeypatch.setattr("sys.argv", ["harness.py"])
    assert harness.main() == 1
    assert "cleanup failed" in capsys.readouterr().err
