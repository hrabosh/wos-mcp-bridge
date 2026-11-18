"""Stdio and authenticated HTTP entrypoint."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wos-mcp-bridge", description="Read-only scientific literature MCP server."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('wos-mcp-bridge')}"
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port")
    args = parser.parse_args()

    if args.transport == "stdio":
        from .server import mcp

        mcp.run(transport="stdio")
        return

    import uvicorn

    from .http_server import create_http_app

    try:
        app = create_http_app()
    except ValueError as exc:
        parser.error(str(exc))
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        lifespan="on",
        timeout_graceful_shutdown=5,
        access_log=False,
    )


if __name__ == "__main__":
    main()
