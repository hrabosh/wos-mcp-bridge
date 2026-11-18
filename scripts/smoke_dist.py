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

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from jsonschema import Draft202012Validator

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


async def discovery(command: str, args: list[str], cwd: Path, environment: dict[str, str]) -> None:
    transport = StdioTransport(
        command=command,
        args=args,
        cwd=str(cwd),
        env={
            **environment,
            "WOS_API_KEY": "synthetic-distribution-smoke-key",
            "OPENALEX_API_KEY": "",
        },
        keep_alive=False,
    )
    async with asyncio.timeout(25):
        async with Client(transport, mode="legacy") as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == TOOLS
            for tool in tools:
                assert tool.output_schema and tool.output_schema.get("properties"), tool.name
                Draft202012Validator.check_schema(tool.output_schema)
            result = await client.call_tool("fetch", {"id": "invalid-id"}, raise_on_error=False)
            assert result.is_error and "WOS:" in result.content[0].text


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
            await discovery(str(python), ["-m", "wos_mcp_bridge"], outside, environment)
            await discovery(str(console), [], outside, environment)
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
