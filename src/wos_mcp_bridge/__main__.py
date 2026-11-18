"""Local stdio entrypoint. Remote hosting supplies its own authentication."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wos-mcp-bridge", description="Read-only scientific literature MCP server over stdio."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('wos-mcp-bridge')}"
    )
    parser.parse_args()

    from .server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
