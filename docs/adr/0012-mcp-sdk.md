# ADR-0012: MCP server on the official SDK v2, read-only at three levels

**Status:** accepted.

PyPI's `mcp` is 2.3.0, whose server class is `MCPServer`, and it is pinned. Read-only holds at three levels: only read tools exist and each is annotated read-only, the database role has SELECT on views only, and its sessions default to read-only transactions. Tests run through a real client session and assert that a write through the role fails. Catalog text is fenced in tool output, because MCP clients are LLMs too.

**Cost:** the HTTP transport has no authentication of its own, so compose binds it to loopback, and Terraform does not deploy it.
