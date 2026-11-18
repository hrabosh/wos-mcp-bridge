"""Compatibility entrypoint for existing server.py:mcp hosting configurations.

Install this project (pip install -e . for development) before importing it.
"""

from wos_mcp_bridge.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
