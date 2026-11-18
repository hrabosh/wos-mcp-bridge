"""Install each built distribution outside the checkout and verify its entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from anyio import fail_after
from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent, jsonrpc_message_adapter

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    "search",
    "fetch",
    "search_wos",
    "search_literature",
    "resolve_article",
    "get_article",
    "get_article_section",
}


def run(command: list[str], cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=environment, check=False, capture_output=True, text=True, timeout=180
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed: {command[0]}\n{completed.stderr[-4000:]}")
    return completed.stdout.strip()


async def exercise_stdio(
    python: Path, command: list[str], cwd: Path, *, synthetic_search: bool = False
) -> None:
    launcher = cwd / "smoke_stdio_server.py"
    transcript = cwd / "stdout.jsonl"
    completed = transcript.with_suffix(".complete")
    completed.unlink(missing_ok=True)
    transcript.unlink(missing_ok=True)
    server = StdioServerParameters(
        command=str(python),
        args=["-I", str(launcher), "--capture-stdout", str(transcript), *command],
        cwd=cwd,
        env={
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "WOS_API_KEY": "synthetic-distribution-smoke-key",
            "OPENALEX_API_KEY": "",
        },
    )
    try:
        with fail_after(25):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.protocol_version == "2025-11-25"
                    tools = (await client.list_tools()).tools
                    assert {tool.name for tool in tools} == TOOLS
                    schemas = {}
                    for tool in tools:
                        assert tool.output_schema and tool.output_schema.get("properties"), (
                            tool.name
                        )
                        Draft202012Validator.check_schema(tool.output_schema)
                        schemas[tool.name] = tool.output_schema
                    if synthetic_search:
                        result = await client.call_tool(
                            "search_wos", {"query": "TS=(quantum computing)"}
                        )
                        assert not result.is_error, result.content
                        assert isinstance(result.content[0], TextContent)
                        assert json.loads(result.content[0].text) == result.structured_content
                        assert result.structured_content is not None
                        Draft202012Validator(
                            schemas["search_wos"], format_checker=FormatChecker()
                        ).validate(result.structured_content)
                        assert result.structured_content["results"][0]["id"] == "WOS:SMOKE001"
                        error = await client.call_tool("fetch", {"id": "invalid-id"})
                        assert error.is_error and isinstance(error.content[0], TextContent)
                        assert "WOS:" in error.content[0].text
    finally:
        if transcript.is_file():
            with transcript.open("rb") as output:
                for line_number, line in enumerate(output, 1):
                    try:
                        assert line.endswith(b"\n")
                        jsonrpc_message_adapter.validate_json(line, by_name=False)
                    except (ValueError, AssertionError):
                        raise AssertionError(
                            f"Server wrote non-protocol output to stdout at line {line_number}."
                        ) from None
    assert completed.is_file(), "Server did not exit cleanly."


async def smoke(directory: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("Install uv 0.13.9 before running this check.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "WOS_API_KEY", "OPENALEX_API_KEY"}
    }
    environment["FASTMCP_CHECK_FOR_UPDATES"] = "off"
    artifacts = [*directory.glob("*.whl"), *directory.glob("*.tar.gz")]
    if len(artifacts) != 2:
        raise RuntimeError("Run check_dist.py on a wheel/source pair first.")
    with tempfile.TemporaryDirectory(prefix="wos-dist-smoke-") as temporary:
        outside = Path(temporary).resolve()
        assert not outside.is_relative_to(ROOT)
        for index, artifact in enumerate(artifacts):
            target = outside / f"env-{index}"
            python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            console = target / (
                "Scripts/wos-mcp-bridge.exe" if os.name == "nt" else "bin/wos-mcp-bridge"
            )
            run([uv, "venv", "--python", sys.executable, str(target)], outside, environment)
            run(
                [uv, "pip", "sync", "--python", str(python), str(ROOT / "requirements.txt")],
                outside,
                environment,
            )
            if artifact.suffix == ".whl":
                # Existing hosts install only runtime requirements before loading server.py:mcp.
                result = run(
                    [
                        str(python),
                        "-I",
                        "-c",
                        "import importlib.util, runpy, sys; "
                        "assert importlib.util.find_spec('wos_mcp_bridge') is None; "
                        "print(runpy.run_path(sys.argv[1])['mcp'].name)",
                        str(ROOT / "server.py"),
                    ],
                    outside,
                    environment,
                )
                assert result == "Web of Science", result
                print("PASS: server.py:mcp loads with only runtime requirements", flush=True)
            run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    str(artifact.resolve()),
                ],
                outside,
                environment,
            )
            installed = json.loads(
                run(
                    [
                        str(python),
                        "-c",
                        "import json; from unittest.mock import patch; "
                        "guard = patch('httpx.AsyncClient', side_effect=AssertionError('HTTP client at import')); "
                        "guard.start(); import wos_mcp_bridge.server; import wos_mcp_bridge; "
                        "print(json.dumps(wos_mcp_bridge.__file__))",
                    ],
                    outside,
                    environment,
                )
            )
            assert Path(installed).resolve().is_relative_to(target)
            expected = f"wos-mcp-bridge {version}"
            assert (
                run([str(python), "-m", "wos_mcp_bridge", "--version"], outside, environment)
                == expected
            )
            assert run([str(console), "--version"], outside, environment) == expected
            launcher = outside / "smoke_stdio_server.py"
            shutil.copyfile(ROOT / "scripts" / launcher.name, launcher)
            await exercise_stdio(
                python, [str(python), "-I", str(launcher)], outside, synthetic_search=True
            )
            await exercise_stdio(python, [str(console)], outside)
            print(
                f"PASS: {artifact.name}: installed import, module/console version and stdio",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    asyncio.run(smoke(arguments.directory.resolve()))


if __name__ == "__main__":
    main()
