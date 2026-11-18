"""Launch and observe installed stdio servers for release testing."""

import runpy
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import httpx


def upstream(request: httpx.Request) -> httpx.Response:
    if (
        request.method == "GET"
        and request.url.host == "api.clarivate.com"
        and request.url.path == "/apis/wos-starter/v1/documents"
        and request.url.params.get("q") == "TS=(quantum computing)"
    ):
        return httpx.Response(
            200,
            json={
                "metadata": {"total": 1},
                "hits": [{"uid": "WOS:SMOKE001", "title": "Synthetic quantum computing paper"}],
            },
        )
    raise AssertionError("Unexpected outbound request from the artifact smoke test.")


def capture_stdout(transcript: Path, command: list[str]) -> None:
    # The MCP client may stop parsing during teardown. Keep recording through EOF.
    with (
        transcript.open("wb") as output,
        subprocess.Popen(command, stdout=subprocess.PIPE) as child,
    ):
        assert child.stdout is not None
        for line in child.stdout:
            output.write(line)
            output.flush()
            with suppress(BrokenPipeError):
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        if child.wait() != 0:
            raise SystemExit(child.returncode)
    transcript.with_suffix(".complete").touch()


def run_synthetic_server() -> None:
    # Verify that the normal CLI sees no arguments from this test launcher.
    sys.argv[:] = ["wos-mcp-bridge"]
    original_client = httpx.AsyncClient

    def offline_client(**options):
        options["transport"] = httpx.MockTransport(upstream)
        return original_client(**options)

    with patch.object(httpx, "AsyncClient", offline_client):
        runpy.run_module("wos_mcp_bridge", run_name="__main__")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--capture-stdout":
        capture_stdout(Path(sys.argv[2]), sys.argv[3:])
    else:
        run_synthetic_server()
