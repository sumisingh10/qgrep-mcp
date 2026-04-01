"""Allow running as `python -m qgrep_mcp`.

Supports two modes:
    python -m qgrep_mcp              # MCP server (stdio)
    python -m qgrep_mcp --http       # REST API (HTTP)
    python -m qgrep_mcp --http 9000  # REST API on custom port
"""

import sys


def main():
    """Parse CLI args and start the appropriate server."""
    args = sys.argv[1:]

    if "--http" in args:
        from .api import run_http

        port = 8080
        idx = args.index("--http")
        # Check for --port flag or positional arg after --http
        if "--port" in args:
            port_idx = args.index("--port")
            if port_idx + 1 < len(args):
                port = int(args[port_idx + 1])
        elif idx + 1 < len(args) and args[idx + 1].isdigit():
            port = int(args[idx + 1])

        run_http(port=port)
    else:
        from .server import main as mcp_main

        mcp_main()


main()
