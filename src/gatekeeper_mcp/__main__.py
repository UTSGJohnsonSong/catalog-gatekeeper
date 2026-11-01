"""Run the MCP server: stdio by default, or streamable HTTP with --http."""

from __future__ import annotations

import argparse
import os
import sys

from gatekeeper_mcp.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8808)
    args = parser.parse_args()
    database_url = os.environ.get("MCP_DATABASE_URL")
    if not database_url:
        sys.exit("MCP_DATABASE_URL is required; point it at the read-only gatekeeper_mcp_ro role.")
    server = build_server(database_url)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
