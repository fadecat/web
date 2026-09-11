"""Contract tests for the local/ECS application operation scripts."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
PS_SCRIPT = ROOT / "scripts" / "app.ps1"
SH_SCRIPT = ROOT / "scripts" / "app.sh"


def _pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _bash() -> str | None:
    windows_candidates = [
        r"E:\SOFTWARE\Git\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        str(
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/bin/bash.exe"
        ),
    ]
    candidates = windows_candidates + [shutil.which("bash")] if os.name == "nt" else [shutil.which("bash")]
    return next((value for value in candidates if value and Path(value).is_file()), None)


def _run_ps(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    executable = _pwsh()
    if executable is None:
        pytest.skip("PowerShell is not installed")
    return subprocess.run(
        [executable, "-NoProfile", "-File", str(PS_SCRIPT), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _run_ps_without_pipes(*args: str) -> subprocess.CompletedProcess[str]:
    """Avoid inherited pipe handles when the script launches a detached process."""
    executable = _pwsh()
    if executable is None:
        pytest.skip("PowerShell is not installed")
    artifact_dir = ROOT / ".test-artifacts" / "app-operations"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = artifact_dir / "powershell.stdout.log"
    stderr_path = artifact_dir / "powershell.stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            [executable, "-NoProfile", "-File", str(PS_SCRIPT), *args],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            text=True,
            env={**os.environ, "APP_RUNTIME_DIR": str(artifact_dir / "runtime")},
            timeout=30,
            check=False,
        )
    result.stdout = stdout_path.read_text(encoding="utf-8")
    result.stderr = stderr_path.read_text(encoding="utf-8")
    return result


def _run_sh(
    *args: str,
    cwd: Path | None = None,
    use_root_override: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = _bash()
    if executable is None:
        pytest.skip("Bash is not installed")
    env = os.environ.copy()
    env["APP_OPS_DRY_RUN"] = "1"
    if use_root_override:
        env["APP_ROOT"] = str(ROOT).replace("\\", "/")
    else:
        env.pop("APP_ROOT", None)
    return subprocess.run(
        [executable, str(SH_SCRIPT), *args],
        cwd=cwd or ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_powershell_local_restart_all_has_fixed_order(tmp_path):
    result = _run_ps("restart", "all", "-DryRun", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    output = result.stdout
    steps = [
        "DRY-RUN local stop frontend",
        "DRY-RUN local stop backend",
        "DRY-RUN local start backend",
        "DRY-RUN local start frontend",
    ]
    positions = [output.index(step) for step in steps]
    assert positions == sorted(positions)


def test_powershell_local_frontend_build_is_explicit():
    result = _run_ps("build", "frontend", "-DryRun")

    assert result.returncode == 0, result.stderr
    assert "DRY-RUN local build frontend" in result.stdout
    assert "pnpm" in result.stdout.lower()
    assert "build" in result.stdout.lower()


def test_powershell_ecs_delegation_uses_configurable_destination():
    result = _run_ps(
        "restart",
        "all",
        "-Environment",
        "ecs",
        "-EcsHost",
        "test-ecs",
        "-EcsRoot",
        "/srv/test-web",
        "-DryRun",
    )

    assert result.returncode == 0, result.stderr
    assert "DRY-RUN ecs test-ecs /srv/test-web restart all" in result.stdout
    assert "BatchMode=yes" in result.stdout
    assert "ConnectTimeout=10" in result.stdout
    assert "scripts/app.sh" in result.stdout


def test_powershell_rejects_build_backend():
    result = _run_ps("build", "backend", "-DryRun")

    assert result.returncode != 0
    assert "build" in (result.stdout + result.stderr).lower()
    assert "frontend" in (result.stdout + result.stderr).lower()


@pytest.mark.skipif(os.name != "nt", reason="Windows process lifecycle test")
def test_powershell_frontend_real_start_status_stop():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    start = _run_ps_without_pipes("start", "frontend", "-FrontendPort", str(port))
    try:
        assert start.returncode == 0, start.stdout + start.stderr
        assert "DRY-RUN" not in start.stdout
        status = _run_ps_without_pipes("status", "frontend", "-FrontendPort", str(port))
        assert status.returncode == 0, status.stdout + status.stderr
        assert "RUNNING" in status.stdout
        assert "DRY-RUN" not in status.stdout
    finally:
        stop = _run_ps_without_pipes("stop", "frontend", "-FrontendPort", str(port))
    assert stop.returncode == 0, stop.stdout + stop.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows process lifecycle test")
def test_powershell_backend_real_start_status_stop():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    start = _run_ps_without_pipes("start", "backend", "-BackendPort", str(port))
    try:
        assert start.returncode == 0, start.stdout + start.stderr
        assert "DRY-RUN" not in start.stdout
        status = _run_ps_without_pipes("status", "backend", "-BackendPort", str(port))
        assert status.returncode == 0, status.stdout + status.stderr
        assert "RUNNING" in status.stdout
    finally:
        stop = _run_ps_without_pipes("stop", "backend", "-BackendPort", str(port))
    assert stop.returncode == 0, stop.stdout + stop.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows port ownership test")
def test_powershell_start_refuses_port_owned_by_another_process():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_ps_without_pipes("start", "frontend", "-FrontendPort", str(port))
        assert result.returncode != 0, result.stdout + result.stderr
        assert "port" in (result.stdout + result.stderr).lower()
    finally:
        server.shutdown()
        server.server_close()
        _run_ps_without_pipes("stop", "frontend", "-FrontendPort", str(port))


def test_bash_restart_all_builds_before_service_restart():
    result = _run_sh("restart", "all")

    assert result.returncode == 0, result.stderr
    output = result.stdout
    build = output.index("DRY-RUN frontend build")
    restart = output.index("DRY-RUN systemctl restart webapp")
    health = output.index("DRY-RUN health http://127.0.0.1:8000/api/health")
    assert build < restart < health


def test_bash_frontend_build_does_not_restart_backend():
    result = _run_sh("build", "frontend")

    assert result.returncode == 0, result.stderr
    assert "DRY-RUN frontend build" in result.stdout
    assert "systemctl" not in result.stdout


def test_bash_resolves_root_from_its_own_path():
    result = _run_sh("build", "frontend", cwd=ROOT, use_root_override=False)

    assert result.returncode == 0, result.stderr
    normalized = result.stdout.replace("\\", "/").lower()
    assert "/web/frontend" in normalized


@pytest.mark.parametrize("action", ["start", "stop"])
def test_bash_rejects_frontend_process_actions(action):
    result = _run_sh(action, "frontend")

    assert result.returncode != 0
    output = (result.stdout + result.stderr).lower()
    assert "static" in output or "静态" in output
    assert "frontend" in output
