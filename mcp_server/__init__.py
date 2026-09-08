"""mpire-audit MCP server: read-mostly access to derived, masked audit data for MCP clients.

Entry points: ``build_server()`` (library use and tests) and ``python -m mcp_server`` (stdio by
default). No tool here reads a source document, contacts an LOS, AUS, TRID, pricing, lender, or
messaging system, or starts an audit run.
"""
from mcp_server.server import SERVER_NAME, SERVER_VERSION, build_server, main

__all__ = ["SERVER_NAME", "SERVER_VERSION", "build_server", "main"]
