"""Run the MCP server: stdio by default, or streamable HTTP with --http."""

from __future__ import annotations

import argparse
import os
import sys

from mcp.server.transport_security import TransportSecuritySettings

from gatekeeper_mcp.server import build_server

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


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
        # The SDK only enables these checks when it binds a loopback address, and inside a container it binds
        # 0.0.0.0. Without this, a web page that rebinds its domain to 127.0.0.1 could read the catalog.
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=LOCAL_HOSTS, allowed_origins=LOCAL_ORIGINS
        )
        server.run("streamable-http", host=args.host, port=args.port, transport_security=security)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
