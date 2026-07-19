from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = REPO_ROOT / "demo" / "run_demo.ps1"
MANAGED_ENVIRONMENT = (
    "HIGHGROUND_DATABASE_PATH",
    "HIGHGROUND_API_KEY",
    "HIGHGROUND_ENV",
    "HIGHGROUND_ACTUATOR_MODE",
    "HIGHGROUND_AUTH_TTL_SECONDS",
    "HIGHGROUND_EVENT_MAX_AGE_SECONDS",
    "HIGHGROUND_ALLOWED_ORIGINS",
    "PYTHONUTF8",
)


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the demo launcher smoke test")
    return executable


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_launcher_runs_real_http_demo_in_an_isolated_environment(tmp_path: Path) -> None:
    port = _unused_local_port()
    evidence_path = tmp_path / "launcher-evidence.json"
    snapshot_path = tmp_path / "restored-environment.json"
    wrapper_path = tmp_path / "invoke-launcher.ps1"
    hostile_database = tmp_path / "must-not-be-used.db"
    api_key = "launcher-http-smoke-test-key"

    names = ", ".join(_powershell_literal(name) for name in MANAGED_ENVIRONMENT)
    wrapper_path.write_text(
        "\n".join(
            (
                '$ErrorActionPreference = "Stop"',
                "try {",
                f"    & {_powershell_literal(LAUNCHER_PATH)} `",
                "        -TimeScale 0 `",
                f"        -Port {port} `",
                f"        -ApiKey {_powershell_literal(api_key)} `",
                f"        -Output {_powershell_literal(evidence_path)}",
                "} finally {",
                "    $snapshot = [ordered]@{}",
                f"    foreach ($name in @({names})) {{",
                "        $snapshot[$name] = "
                "[System.Environment]::GetEnvironmentVariable($name, 'Process')",
                "    }",
                "    $snapshot | ConvertTo-Json -Compress | "
                f"Set-Content -LiteralPath {_powershell_literal(snapshot_path)} -Encoding UTF8",
                "}",
                "",
            )
        ),
        encoding="utf-8",
    )

    expected_environment: dict[str, str | None] = {
        "HIGHGROUND_DATABASE_PATH": str(hostile_database),
        "HIGHGROUND_API_KEY": "parent-api-key",
        "HIGHGROUND_ENV": "production",
        "HIGHGROUND_ACTUATOR_MODE": "disabled",
        "HIGHGROUND_AUTH_TTL_SECONDS": "not-an-integer",
        "HIGHGROUND_EVENT_MAX_AGE_SECONDS": "also-not-an-integer",
        "HIGHGROUND_ALLOWED_ORIGINS": None,
        "PYTHONUTF8": "0",
    }
    child_environment = os.environ.copy()
    for name, value in expected_environment.items():
        if value is None:
            child_environment.pop(name, None)
        else:
            child_environment[name] = value

    command = [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive"]
    if os.name == "nt":
        command.extend(("-ExecutionPolicy", "Bypass"))
    command.extend(("-File", str(wrapper_path)))
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=child_environment,
        capture_output=True,
        timeout=90,
        check=False,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")

    assert completed.returncode == 0, output
    assert evidence_path.is_file(), output
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "passed"
    assert evidence["time_scale"] == 0
    assert evidence["scenario_duration_seconds"] == 120
    assert evidence["record_only"] is True
    assert evidence["vehicle_command_transmitted"] is False
    assert len(evidence["steps"]) == 9

    restored_environment = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    assert restored_environment == expected_environment
    assert not hostile_database.exists()

    database_match = re.search(r"(?m)^Temporary demo database: (.+?)\r?$", output)
    assert database_match is not None, output
    temporary_database = Path(database_match.group(1))
    assert temporary_database.name == "highground.db"
    assert not temporary_database.exists()
    assert not temporary_database.parent.exists()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
