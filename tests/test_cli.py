"""CLI inspection must not start a credential-dependent MCP server."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path


def test_module_version_works_without_credentials_outside_checkout(tmp_path):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"WOS_API_KEY", "OPENALEX_API_KEY"}
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "wos_mcp_bridge", "--version"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"wos-mcp-bridge {version('wos-mcp-bridge')}"


def test_http_cli_requires_auth_settings_before_listening(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "wos_mcp_bridge", "--transport", "http"],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "FASTMCP_CHECK_FOR_UPDATES": "off",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "WOS_MCP_HTTP_ORIGIN must be" in result.stderr
