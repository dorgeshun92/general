"""`python -m mcp_server [--transport stdio|streamable-http] [--host H] [--port P] [--selftest]`."""
import sys

from mcp_server.server import main

if __name__ == "__main__":
    sys.exit(main())
