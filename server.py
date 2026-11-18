"""Compatibility entrypoint for hosts that load server.py:mcp from a checkout."""

import sys
from pathlib import Path

try:
    from wos_mcp_bridge.server import mcp
except ModuleNotFoundError as error:
    if error.name != "wos_mcp_bridge":
        raise
    # Some hosts install the runtime requirements and load this file directly.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from wos_mcp_bridge.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
